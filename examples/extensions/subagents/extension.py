"""Subagents extension for Tau, ported from tintinweb/pi-subagents.

Registers an `agent` tool that spawns autonomous subagents in-process (a
scoped `CodingSession` with its own tools and system prompt), a
`get_subagent_result` tool for background runs, and an `/agents` command.

Foreground agents block and return their final assistant text. Background
agents return an id immediately and deliver a `<task-notification>` back into
the parent conversation when they finish.

Install by copying this directory into `~/.tau/extensions/subagents/`, or run:

    tau -x examples/extensions/subagents
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from tau_agent.session import SessionEntry
from tau_agent.tools import AgentTool, AgentToolResult
from tau_coding import (
    CodingSession,
    CodingSessionConfig,
    load_provider_settings,
    resolve_provider_selection,
)
from tau_coding.extensions import ExtensionAPI, SessionShutdownEvent
from tau_coding.provider_runtime import create_model_provider
from tau_coding.thinking import DEFAULT_THINKING_LEVEL

from .agents import AgentDefinition, format_agent_type_list, load_agent_definitions

if TYPE_CHECKING:
    from tau_agent.events import AgentEvent

RESULT_TRUNCATION_CHARS = 4_000


class _MemoryStorage:
    """Append-only in-memory storage for subagent sessions."""

    def __init__(self) -> None:
        self.entries: list[SessionEntry] = []

    async def append(self, entry: SessionEntry) -> None:
        self.entries.append(entry)

    async def read_all(self) -> list[SessionEntry]:
        return list(self.entries)


@dataclass(slots=True)
class AgentRun:
    """State of one spawned subagent."""

    agent_id: str
    agent_type: str
    description: str
    prompt: str
    background: bool
    status: str = "running"
    result_text: str = ""
    error: str | None = None
    turns: int = 0
    tool_calls: int = 0
    task: asyncio.Task[None] | None = None
    session: CodingSession | None = None
    result_consumed: bool = field(default=False)


class SubagentManager:
    """Spawns and tracks subagent runs for one Tau session."""

    def __init__(self, api: ExtensionAPI) -> None:
        self._api = api
        self._runs: dict[str, AgentRun] = {}
        self._counter = 0

    @property
    def runs(self) -> dict[str, AgentRun]:
        return self._runs

    def definitions(self) -> dict[str, AgentDefinition]:
        return load_agent_definitions(self._api.context.cwd)

    def spawn(
        self,
        *,
        agent_type: AgentDefinition,
        prompt: str,
        description: str,
        background: bool,
    ) -> AgentRun:
        self._counter += 1
        run = AgentRun(
            agent_id=f"agent-{self._counter}",
            agent_type=agent_type.name,
            description=description,
            prompt=prompt,
            background=background,
        )
        self._runs[run.agent_id] = run
        run.task = asyncio.get_running_loop().create_task(
            self._run_agent(run, agent_type)
        )
        return run

    async def shutdown(self) -> None:
        for run in self._runs.values():
            if run.task is not None and not run.task.done():
                if run.session is not None:
                    run.session.cancel()
                run.task.cancel()

    async def _run_agent(self, run: AgentRun, definition: AgentDefinition) -> None:
        try:
            await self._execute(run, definition)
        except asyncio.CancelledError:
            run.status = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - report subagent failures as results
            run.status = "error"
            run.error = str(exc)
        finally:
            if run.background and run.status != "cancelled":
                self._deliver_background_result(run)

    async def _execute(self, run: AgentRun, definition: AgentDefinition) -> None:
        settings = load_provider_settings()
        selection = resolve_provider_selection(settings, model=definition.model)
        provider = create_model_provider(
            selection.provider,
            model=selection.model,
            thinking_level=DEFAULT_THINKING_LEVEL,
        )
        session = await CodingSession.load(
            CodingSessionConfig(
                provider=provider,
                model=selection.model,
                cwd=self._api.context.cwd,
                storage=_MemoryStorage(),
                custom_system_prompt=definition.system_prompt,
                provider_name=selection.provider.name,
                auto_compact_enabled=False,
                # Subagents load no extensions, so they cannot spawn recursively.
                extensions_enabled=False,
            )
        )
        run.session = session
        try:
            if definition.tools is not None:
                allowed = set(definition.tools)
                session._harness.config.tools = [  # noqa: SLF001 - scoped tool gating
                    tool for tool in session._harness.config.tools if tool.name in allowed
                ]
            final_text: list[str] = []
            async for event in session.prompt(run.prompt):
                self._observe(run, event, final_text)
            run.result_text = final_text[-1] if final_text else ""
            if run.status == "running":
                run.status = "completed"
        finally:
            await session.aclose()
            closer = getattr(provider, "aclose", None)
            if closer is not None:
                await closer()
            run.session = None

    def _observe(self, run: AgentRun, event: AgentEvent, final_text: list[str]) -> None:
        if event.type == "turn_end":
            run.turns += 1
        elif event.type == "tool_execution_start":
            run.tool_calls += 1
        elif event.type == "message_end":
            message = event.message
            if getattr(message, "role", None) == "assistant" and message.content.strip():
                final_text.append(message.content.strip())
        elif event.type == "error" and not event.recoverable:
            run.status = "error"
            run.error = event.message

    def _deliver_background_result(self, run: AgentRun) -> None:
        if run.result_consumed:
            return
        self._api.notify(
            f"Subagent {run.agent_id} ({run.description}) {run.status}.",
            "info" if run.status == "completed" else "warning",
        )
        self._api.send_user_message(
            format_task_notification(run),
            deliver_as="follow_up",
        )


def format_task_notification(run: AgentRun) -> str:
    """Format a background completion notice for the parent conversation."""
    body = run.error if run.status == "error" else run.result_text
    truncated = (body or "(no output)")[:RESULT_TRUNCATION_CHARS]
    return (
        "<task-notification>\n"
        f"<agent-id>{run.agent_id}</agent-id>\n"
        f"<type>{run.agent_type}</type>\n"
        f"<description>{run.description}</description>\n"
        f"<status>{run.status}</status>\n"
        f"<turns>{run.turns}</turns>\n"
        f"<result>{truncated}</result>\n"
        "</task-notification>\n"
        "This is an automated subagent completion notice, not a user message. "
        "Use the result to continue the original task."
    )


def format_run_summary(run: AgentRun) -> str:
    """Format one run for /agents and get_subagent_result output."""
    return (
        f"{run.agent_id} [{run.status}] type={run.agent_type}"
        f" turns={run.turns} tools={run.tool_calls} — {run.description}"
    )


def setup(tau: ExtensionAPI) -> None:
    """Register subagent tools, the /agents command, and shutdown cleanup."""
    manager = SubagentManager(tau)

    async def run_agent_tool(arguments, signal=None):  # noqa: ANN001, ANN202
        definitions = manager.definitions()
        agent_type = str(arguments.get("subagent_type", "general"))
        definition = definitions.get(agent_type)
        if definition is None:
            available = ", ".join(sorted(definitions))
            return _tool_result(
                "agent",
                ok=False,
                content=f"Unknown subagent_type: {agent_type}. Available: {available}",
            )
        prompt = str(arguments.get("prompt", "")).strip()
        if not prompt:
            return _tool_result("agent", ok=False, content="prompt is required")
        description = str(arguments.get("description", "")) or f"{agent_type} agent"
        background = bool(arguments.get("run_in_background", False))

        run = manager.spawn(
            agent_type=definition,
            prompt=prompt,
            description=description,
            background=background,
        )
        if background:
            return _tool_result(
                "agent",
                ok=True,
                content=(
                    f"Spawned background agent {run.agent_id} ({description}). "
                    "A <task-notification> will arrive when it finishes; use"
                    " get_subagent_result to poll or wait explicitly."
                ),
            )

        assert run.task is not None
        while not run.task.done():
            if signal is not None and signal.is_cancelled():
                if run.session is not None:
                    run.session.cancel()
                run.task.cancel()
                return _tool_result("agent", ok=False, content="Subagent cancelled")
            await asyncio.sleep(0.05)

        if run.status == "completed":
            content = run.result_text or "(subagent produced no output)"
            return _tool_result(
                "agent",
                ok=True,
                content=f"{format_run_summary(run)}\n\n{content}",
            )
        return _tool_result(
            "agent",
            ok=False,
            content=f"{format_run_summary(run)}\n\n{run.error or 'subagent failed'}",
        )

    async def run_get_result_tool(arguments, signal=None):  # noqa: ANN001, ANN202
        del signal
        agent_id = str(arguments.get("agent_id", ""))
        run = manager.runs.get(agent_id)
        if run is None:
            known = ", ".join(sorted(manager.runs)) or "none"
            return _tool_result(
                "get_subagent_result",
                ok=False,
                content=f"Unknown agent_id: {agent_id}. Known agents: {known}",
            )
        if bool(arguments.get("wait", False)) and run.task is not None and not run.task.done():
            # Claim the result before waiting so the background completion
            # path skips its redundant follow-up notification.
            run.result_consumed = True
            await asyncio.wait({run.task})
        if run.task is not None and not run.task.done():
            return _tool_result(
                "get_subagent_result",
                ok=True,
                content=f"{format_run_summary(run)}\n\nStill running.",
            )
        run.result_consumed = True
        body = run.error if run.status == "error" else run.result_text
        return _tool_result(
            "get_subagent_result",
            ok=run.status == "completed",
            content=f"{format_run_summary(run)}\n\n{body or '(no output)'}",
        )

    def agents_command(args: str, context) -> str:  # noqa: ANN001
        del args, context
        definitions = manager.definitions()
        lines = ["Agent types:", format_agent_type_list(definitions)]
        if manager.runs:
            lines.append("")
            lines.append("Runs:")
            lines.extend(format_run_summary(run) for run in manager.runs.values())
        else:
            lines.append("")
            lines.append("No agents have been spawned in this session.")
        return "\n".join(lines)

    async def on_shutdown(event: SessionShutdownEvent) -> None:
        if event.reason == "quit":
            await manager.shutdown()

    type_list = format_agent_type_list(load_agent_definitions(Path.cwd()))
    tau.register_tool(
        AgentTool(
            name="agent",
            description=(
                "Spawn an autonomous subagent to handle a task. The subagent works"
                " in its own context with its own tools and returns its final"
                " report. Set run_in_background=true for long tasks; a completion"
                " notification will arrive when it finishes.\n\nAvailable agent"
                f" types:\n{type_list}"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The task for the subagent to perform.",
                    },
                    "description": {
                        "type": "string",
                        "description": "A short (3-5 word) description of the task.",
                    },
                    "subagent_type": {
                        "type": "string",
                        "description": "The agent type to spawn (default: general).",
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "Return immediately and notify on completion.",
                    },
                },
                "required": ["prompt", "description"],
            },
            executor=run_agent_tool,
            prompt_snippet="Spawn an autonomous subagent for delegated tasks.",
        )
    )
    tau.register_tool(
        AgentTool(
            name="get_subagent_result",
            description="Get the status or final result of a spawned subagent.",
            input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The id returned when the agent was spawned.",
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "Block until the agent finishes.",
                    },
                },
                "required": ["agent_id"],
            },
            executor=run_get_result_tool,
        )
    )
    tau.register_command(
        "agents",
        agents_command,
        description="List subagent types and runs.",
    )
    tau.on("session_shutdown", on_shutdown)


def _tool_result(name: str, *, ok: bool, content: str) -> AgentToolResult:
    return AgentToolResult(
        tool_call_id="",
        name=name,
        ok=ok,
        content=content,
        error=None if ok else content,
    )
