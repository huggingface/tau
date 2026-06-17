"""Command-line entry point for Tau."""

from pathlib import Path
from typing import Annotated

import anyio
import typer

from tau_agent import AgentHarness, AgentHarnessConfig
from tau_ai import ModelProvider, OpenAICompatibleProvider, openai_compatible_config_from_env
from tau_coding import __version__, create_coding_tools, load_skills
from tau_coding.rendering import PrintOutputMode, create_event_renderer
from tau_coding.resources import TauResourcePaths
from tau_coding.session_manager import CodingSessionRecord, SessionManager
from tau_coding.system_prompt import BuildSystemPromptOptions, build_system_prompt
from tau_coding.tui import run_tui_app

DEFAULT_MODEL = "gpt-4.1-mini"

app = typer.Typer(
    name="tau",
    help="Tau coding-agent harness.",
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    prompt_arg: Annotated[
        str | None,
        typer.Argument(help="Prompt to run in non-interactive print mode."),
    ] = None,
    prompt_option: Annotated[
        str | None,
        typer.Option("--prompt", "-p", help="Prompt to run in non-interactive print mode."),
    ] = None,
    model: Annotated[
        str,
        typer.Option("--model", "-m", help="Model name to request from the provider."),
    ] = DEFAULT_MODEL,
    cwd: Annotated[
        Path | None,
        typer.Option("--cwd", help="Working directory for built-in coding tools."),
    ] = None,
    output: Annotated[
        PrintOutputMode,
        typer.Option("--output", "-o", help="Output mode for print mode."),
    ] = PrintOutputMode.text,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Resume a session id in TUI mode."),
    ] = None,
    new_session: Annotated[
        bool,
        typer.Option("--new-session", help="Create a new session in TUI mode (default)."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show Tau's version and exit."),
    ] = False,
) -> None:
    """Run the Tau CLI."""
    if version:
        typer.echo(f"tau {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        return

    if prompt_option is None and prompt_arg == "sessions":
        render_session_list(SessionManager().list_sessions())
        raise typer.Exit()

    if prompt_option is None and (prompt_arg is None or prompt_arg == "tui"):
        try:
            anyio.run(run_openai_tui, model, cwd or Path.cwd(), resume, new_session)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        raise typer.Exit()

    prompt = prompt_option or prompt_arg
    if prompt is None:
        raise AssertionError("prompt should be set outside TUI mode")

    try:
        ok = anyio.run(run_openai_print_mode, prompt, model, cwd or Path.cwd(), output)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not ok:
        raise typer.Exit(1)


async def run_openai_tui(
    model: str, cwd: Path, session_id: str | None = None, new_session: bool = False
) -> None:
    """Run the Textual TUI with the default OpenAI-compatible provider."""
    await run_tui_app(model=model, cwd=cwd, session_id=session_id, new_session=new_session)


def render_session_list(records: list[CodingSessionRecord]) -> None:
    """Render indexed sessions for the CLI."""
    if not records:
        typer.echo("No sessions found.")
        return

    for record in records:
        title = record.title or "Untitled"
        typer.echo(f"{record.id}\t{title}\t{record.model}\t{record.cwd}")


async def run_openai_print_mode(
    prompt: str, model: str, cwd: Path, output: PrintOutputMode = PrintOutputMode.text
) -> bool:
    """Run print mode with the OpenAI-compatible provider configured from the environment."""
    provider = OpenAICompatibleProvider(openai_compatible_config_from_env())
    try:
        return await run_print_mode(
            prompt=prompt, model=model, cwd=cwd, provider=provider, output=output
        )
    finally:
        await provider.aclose()


async def run_print_mode(
    *,
    prompt: str,
    model: str,
    cwd: Path,
    provider: ModelProvider,
    output: PrintOutputMode = PrintOutputMode.text,
    resource_paths: TauResourcePaths | None = None,
) -> bool:
    """Run one non-interactive prompt and print streamed events.

    Returns False when the agent emits a non-recoverable error so CLI callers
    can fail non-interactive runs while still rendering the error message.
    """
    tools = create_coding_tools(cwd=cwd)
    skills = load_skills(resource_paths or TauResourcePaths(cwd=cwd))
    system = build_system_prompt(BuildSystemPromptOptions(cwd=cwd, tools=tools, skills=skills))
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model=model,
            system=system,
            tools=tools,
        )
    )
    renderer = create_event_renderer(output)
    async for event in harness.prompt(prompt):
        renderer.render(event)
    return renderer.finish()
