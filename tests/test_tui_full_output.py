"""Regression tests for issue #730: opt-in full (untruncated) TUI output.

The TUI previews long tool results and terminal-command output so a single
result cannot flood the transcript. The hidden remainder was previously
unreachable without exporting the session, so these tests pin the three parts
of the escape hatch:

1. the preview formatters keep their lossy default and can produce full text,
2. a tool result retains its full text only when the preview was actually lossy,
3. the CLI flag reaches the TUI state (and stays per-invocation, not durable).
"""

from __future__ import annotations

from tau_agent.messages import TextContent
from tau_agent.tools import AgentToolResult
from tau_coding.tui.state import (
    TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES,
    TOOL_RESULT_PREVIEW_LINES,
    ChatItem,
    TuiState,
    format_terminal_command_result_block,
    format_tool_result_block,
)


def _long_output(lines: int) -> str:
    return "\n".join(f"line {index}" for index in range(lines))


# --- preview formatters -----------------------------------------------------


def test_terminal_command_preview_still_truncates_by_default() -> None:
    output = _long_output(TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES + 80)

    preview = format_terminal_command_result_block(
        ok=True,
        added_to_context=False,
        output=output,
    )

    assert "[Preview only:" in preview
    assert f"line {TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES + 79}" not in preview


def test_terminal_command_full_keeps_every_line_and_no_marker() -> None:
    output = _long_output(TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES + 80)

    full = format_terminal_command_result_block(
        ok=True,
        added_to_context=False,
        output=output,
        full=True,
    )

    assert "[Preview only:" not in full
    assert full.endswith(f"line {TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES + 79}")
    # The status line is still present so the block stays self-describing.
    assert full.startswith("✓ bash")


def test_tool_result_full_keeps_every_line() -> None:
    content = _long_output(TOOL_RESULT_PREVIEW_LINES + 40)

    preview = format_tool_result_block(name="bash", ok=True, content=content)
    full = format_tool_result_block(name="bash", ok=True, content=content, full=True)

    assert "[Preview only:" in preview
    assert "[Preview only:" not in full
    assert full.endswith(f"line {TOOL_RESULT_PREVIEW_LINES + 39}")


def test_tool_patch_preview_is_also_bypassed_by_full() -> None:
    patch = _long_output(200)

    preview = format_tool_result_block(
        name="edit",
        ok=True,
        content="ok",
        data={"patch": patch},
    )
    full = format_tool_result_block(
        name="edit",
        ok=True,
        content="ok",
        data={"patch": patch},
        full=True,
    )

    assert "[Preview only:" in preview
    assert "[Preview only:" not in full
    assert "line 199" in full


def test_full_text_is_char_exact_for_a_single_huge_line() -> None:
    """A char-budget truncation must also be recoverable, not just line drops."""
    output = "x" * 5_000

    preview = format_terminal_command_result_block(
        ok=True,
        added_to_context=False,
        output=output,
    )
    full = format_terminal_command_result_block(
        ok=True,
        added_to_context=False,
        output=output,
        full=True,
    )

    assert "additional text" in preview
    assert len(preview) < len(output)
    assert full.endswith(output)


# --- ChatItem selection -----------------------------------------------------


def test_result_text_prefers_full_only_when_expanded_and_opted_in() -> None:
    item = ChatItem(
        role="tool",
        text="→ bash",
        tool_result_text="preview",
        tool_result_full_text="everything",
    )

    # `full_output` defaults off: an expanded row still shows the preview.
    assert item.result_text(expanded=False) == "preview"
    assert item.result_text(expanded=True) == "preview"

    item.full_output = True
    assert item.result_text(expanded=False) == "preview"
    assert item.result_text(expanded=True) == "everything"


def test_result_text_falls_back_when_no_full_text_was_retained() -> None:
    item = ChatItem(role="tool", text="→ bash", tool_result_text="preview", full_output=True)

    assert item.result_text(expanded=True) == "preview"


def test_record_tool_result_stamps_the_state_opt_in() -> None:
    """A row captured under `--show-full-output` keeps its full text on redraw."""
    state = TuiState()
    state.set_full_output(True)
    state.add_item("tool", "→ bash", tool_call_id="call-1", always_show_tool_result=True)

    state.record_tool_result(
        "call-1",
        "bash",
        AgentToolResult(content=[TextContent(text=_long_output(TOOL_RESULT_PREVIEW_LINES + 10))]),
        False,
    )

    item = state.find_tool_item("call-1")
    assert item is not None
    assert item.full_output is True
    assert item.result_text(expanded=True).endswith("line 17")


def test_set_full_output_updates_rows_already_in_the_transcript() -> None:
    state = TuiState()
    item = ChatItem(
        role="tool",
        text="→ bash",
        tool_result_text="preview",
        tool_result_full_text="everything",
    )
    state.items.append(item)

    assert item.full_output is False
    state.set_full_output(True)

    assert item.full_output is True
    assert item.result_text(expanded=True) == "everything"


# --- state retention --------------------------------------------------------


def test_record_tool_result_retains_full_text_only_when_lossy() -> None:
    state = TuiState()
    state.add_item("tool", "→ bash", tool_call_id="call-1", always_show_tool_result=True)

    state.record_tool_result(
        "call-1",
        "bash",
        AgentToolResult(content=[TextContent(text=_long_output(TOOL_RESULT_PREVIEW_LINES + 10))]),
        False,
    )

    item = state.find_tool_item("call-1")
    assert item is not None
    assert item.tool_result_full_text is not None
    assert "[Preview only:" not in item.tool_result_full_text
    # Retention alone does not change what a default row renders.
    assert item.result_text(expanded=True) == item.tool_result_text

    # A short result hides nothing, so it must not be stored twice.
    state.add_item("tool", "→ read", tool_call_id="call-2", always_show_tool_result=True)
    state.record_tool_result(
        "call-2",
        "read",
        AgentToolResult(content=[TextContent(text="short")]),
        False,
    )

    short_item = state.find_tool_item("call-2")
    assert short_item is not None
    assert short_item.tool_result_full_text is None
    assert short_item.result_text(expanded=True) == short_item.tool_result_text


def test_state_full_output_defaults_off_and_can_be_enabled() -> None:
    state = TuiState()

    assert state.show_full_output is False
    assert state.set_full_output(True) is True
    assert state.show_full_output is True


# --- CLI wiring -------------------------------------------------------------


def test_show_full_output_flag_is_accepted_by_the_cli() -> None:
    from typer.testing import CliRunner

    from tau_coding.cli import app

    result = CliRunner().invoke(app, ["--show-full-output", "--version"])

    assert result.exit_code == 0, result.output


def test_show_full_output_reaches_run_tui_app(monkeypatch) -> None:
    """The flag must survive the CLI's `partial` dispatch, not just parse."""
    from typer.testing import CliRunner

    from tau_coding import cli as cli_module

    seen: dict[str, object] = {}

    async def fake_run_openai_tui(*args: object, **kwargs: object) -> str | None:
        seen.update(kwargs)
        return None

    monkeypatch.setattr(cli_module, "run_openai_tui", fake_run_openai_tui)
    result = CliRunner().invoke(cli_module.app, ["--show-full-output", "hello"])

    assert result.exit_code == 0, result.output
    assert seen.get("show_full_output") is True


def test_flag_absent_leaves_full_output_off(monkeypatch) -> None:
    from typer.testing import CliRunner

    from tau_coding import cli as cli_module

    seen: dict[str, object] = {}

    async def fake_run_openai_tui(*args: object, **kwargs: object) -> str | None:
        seen.update(kwargs)
        return None

    monkeypatch.setattr(cli_module, "run_openai_tui", fake_run_openai_tui)
    result = CliRunner().invoke(cli_module.app, ["hello"])

    assert result.exit_code == 0, result.output
    assert seen.get("show_full_output") is False
