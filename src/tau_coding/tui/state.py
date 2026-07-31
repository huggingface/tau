"""Display state for Tau's Textual TUI."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    TextContent,
    ThinkingContent,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.tools import AgentToolResult, ToolCall
from tau_agent.types import JSONValue
from tau_coding.extensions.api import CustomMessageMarkup, ToolCallMarkup, ToolResultMarkup
from tau_coding.skills import Skill, parse_skill_invocation
from tau_coding.tui.themes import TranscriptRole

ChatItemRole = TranscriptRole
TOOL_RESULT_PREVIEW_LINES = 8
TOOL_PATCH_PREVIEW_LINES = 32
TOOL_RESULT_PREVIEW_CHARS = 2_000
TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES = 120
# Show live elapsed time on an executing tool row once it stops being instant;
# quick reads/edits never flash a "(0s)".
TOOL_TIMER_MIN_SECONDS = 1.0


@dataclass(slots=True)
class ChatItem:
    """One rendered item in the TUI transcript."""

    role: ChatItemRole
    text: str
    tool_call_id: str | None = None
    tool_result_text: str | None = None
    # The raw result object, kept alongside the formatted text so the tool's
    # `render_result` (resolved lazily, like `render_call`) can format it.
    tool_result: AgentToolResult | None = None
    update_text: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, JSONValue] | None = None
    started_at: float | None = None
    always_show_tool_result: bool = False
    custom_type: str | None = None
    details: dict[str, JSONValue] | None = None
    highlight: Literal["update"] | None = None


@dataclass(slots=True)
class TuiState:
    """Mutable display state for the interactive TUI."""

    items: list[ChatItem] = field(default_factory=list)
    assistant_buffer: str = ""
    running: bool = False
    error: str | None = None
    show_tool_results: bool = False
    show_thinking: bool = False
    queued_steering: tuple[str, ...] = ()
    queued_follow_up: tuple[str, ...] = ()
    skills: tuple[Skill, ...] = ()
    custom_renderer: CustomMessageMarkup | None = None
    tool_call_renderer: ToolCallMarkup | None = None
    tool_result_renderer: ToolResultMarkup | None = None
    _tool_items_by_call_id: dict[str, ChatItem] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def add_item(
        self,
        role: ChatItemRole,
        text: str,
        *,
        tool_call_id: str | None = None,
        tool_result_text: str | None = None,
        always_show_tool_result: bool = False,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
        highlight: Literal["update"] | None = None,
    ) -> None:
        """Append a transcript item."""
        item = ChatItem(
            role=role,
            text=text,
            tool_call_id=tool_call_id,
            tool_result_text=tool_result_text,
            always_show_tool_result=always_show_tool_result,
            custom_type=custom_type,
            details=details,
            highlight=highlight,
        )
        self.items.append(item)
        if tool_call_id is not None and role in {"tool", "skill"}:
            self._tool_items_by_call_id[tool_call_id] = item

    def resolve_custom_markup(self, item: ChatItem, *, expanded: bool) -> str | None:
        """Render a custom item's markup via the installed resolver, or ``None``.

        Returns ``None`` when the item is not custom, no resolver is installed,
        or the resolver declines/fails to render (the caller then falls back to
        the raw ``item.text``).
        """
        if item.role != "custom" or item.custom_type is None or self.custom_renderer is None:
            return None
        return self.custom_renderer(item.custom_type, item.text, item.details, expanded)

    def resolve_tool_invocation(self, item: ChatItem) -> str | None:
        """Render a tool item's invocation via the installed resolver, or ``None``.

        Resolved lazily at render time (like custom markup) so tool calls
        restored before the extension runtime connects still pick up their
        tool's `render_call` on the next redraw. ``None`` means "no renderer"
        and the caller falls back to the generic ``item.text``.
        """
        if item.role != "tool":
            return None
        line: str | None = None
        if item.tool_name is not None and self.tool_call_renderer is not None:
            line = self.tool_call_renderer(item.tool_name, item.tool_arguments or {})
        if item.tool_result_text is None and item.started_at is not None:
            elapsed = time.monotonic() - item.started_at
            if elapsed >= TOOL_TIMER_MIN_SECONDS:
                return f"{line if line is not None else item.text} ({format_elapsed(elapsed)})"
        return line

    def resolve_tool_result(self, item: ChatItem, *, expanded: bool) -> str | None:
        """Render a tool item's result via its tool's `render_result`, or ``None``.

        Resolved lazily at render time (like `resolve_tool_invocation`) so
        results restored before the extension runtime connects still pick up
        their tool's `render_result` on the next redraw. ``None`` means "no
        renderer" and the caller falls back to the generic result block.
        """
        if item.role != "tool" or item.tool_result is None or self.tool_result_renderer is None:
            return None
        if item.tool_name is None:
            return None
        return self.tool_result_renderer(item.tool_name, item.tool_result, expanded)

    def add_tool_call(self, tool_call: ToolCall) -> None:
        """Append a collapsed tool-call item."""
        skill_name = self._read_skill_name(tool_call)
        if skill_name is not None:
            self.add_item(
                "skill",
                f"Loading skill: {skill_name}",
                tool_call_id=tool_call.id,
            )
            return
        item = ChatItem(
            role="tool",
            text=format_tool_call_block(tool_call),
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            tool_arguments=tool_call.arguments,
            started_at=time.monotonic(),
        )
        self.items.append(item)
        self._tool_items_by_call_id[tool_call.id] = item

    def add_user_message(
        self,
        content: str,
        *,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        """Append a user-authored message, compacting skill and summary messages.

        A message carrying ``custom_type`` is stored as a ``"custom"`` item so
        the transcript can render it through a registered custom renderer; the
        raw ``content`` is retained as the fallback and LLM-context text.
        """
        if custom_type is not None:
            self.add_item("custom", content, custom_type=custom_type, details=details)
            return

        branch_summary = _parse_branch_summary_message(content)
        if branch_summary is not None:
            self.add_item(
                "branch_summary",
                "Branch summary (Ctrl+O to expand)",
                tool_result_text=branch_summary,
            )
            return

        compaction_summary = _parse_compaction_summary_message(content)
        if compaction_summary is not None:
            self.add_item(
                "compaction_summary",
                "Compaction summary (Ctrl+O to expand)",
                tool_result_text=compaction_summary,
            )
            return

        skill_invocation = parse_skill_invocation(content)
        if skill_invocation is None:
            self.add_item("user", content)
            return
        self.add_item("skill", f"Using skill: {skill_invocation.name}")
        if skill_invocation.additional_instructions:
            self.add_item("user", skill_invocation.additional_instructions)

    def add_thinking_delta(self, delta: str) -> None:
        """Append a thinking/reasoning fragment to the current thinking block."""
        if self.items and self.items[-1].role == "thinking":
            self.items[-1].text += delta
            return
        self.add_item("thinking", delta)

    def find_tool_item(self, tool_call_id: str) -> ChatItem | None:
        """Return the transcript item for a tool call id in O(1)."""
        return self._tool_items_by_call_id.get(tool_call_id)

    def record_tool_update(self, tool_call_id: str, message: str) -> ChatItem | None:
        """Attach live progress to its pending tool call; drop orphan updates."""
        item = self.find_tool_item(tool_call_id)
        if item is None or item.tool_result_text is not None:
            return None
        item.update_text = message
        return item

    def record_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: AgentToolResult,
        is_error: bool,
    ) -> None:
        """Attach a Pi-compatible tool result to its matching call."""
        result_text = format_tool_result_block(
            name=tool_name,
            ok=not is_error,
            content=result.text,
            data=result.details if isinstance(result.details, dict) else None,
        )
        item = self.find_tool_item(tool_call_id)
        if item is not None:
            item.tool_result_text = result_text
            item.tool_result = result
            item.update_text = None
            return
        item = ChatItem(
            role="tool",
            text=format_tool_result_summary(name=tool_name, ok=not is_error),
            tool_call_id=tool_call_id,
            tool_result_text=result_text,
            tool_result=result,
        )
        self.items.append(item)
        self._tool_items_by_call_id[tool_call_id] = item

    def toggle_tool_results(self) -> bool:
        """Toggle expanded display for tool results and return the new state."""
        self.show_tool_results = not self.show_tool_results
        return self.show_tool_results

    def toggle_thinking(self) -> bool:
        """Toggle thinking-token display and return the new state."""
        self.show_thinking = not self.show_thinking
        return self.show_thinking

    def update_queue(self, *, steering: tuple[str, ...], follow_up: tuple[str, ...]) -> None:
        """Replace visible queued-message state."""
        self.queued_steering = steering
        self.queued_follow_up = follow_up

    @property
    def queued_message_count(self) -> int:
        """Return the total number of pending queued messages."""
        return len(self.queued_steering) + len(self.queued_follow_up)

    def clear(self) -> None:
        """Clear visible transcript state without modifying durable session history."""
        self.items.clear()
        self._tool_items_by_call_id.clear()
        self.assistant_buffer = ""
        self.error = None

    def set_skills(self, skills: Iterable[Skill]) -> None:
        """Replace loaded skill metadata used for presentation-only path matching."""
        self.skills = tuple(skills)

    def load_messages(self, messages: Iterable[AgentMessage]) -> None:
        """Populate the transcript from restored canonical session messages."""
        for message in messages:
            if isinstance(message, UserMessage):
                self.add_user_message(message.text)
            elif isinstance(message, CustomMessage):
                self.add_user_message(
                    message.text,
                    custom_type=message.custom_type,
                    details=message.details if isinstance(message.details, dict) else None,
                )
            elif isinstance(message, AssistantMessage):
                if message.stop_reason in {"error", "aborted"}:
                    self.add_assistant_error(message)
                else:
                    self.add_assistant_message(message)
            elif isinstance(message, ToolResultMessage):
                self.record_tool_result(
                    message.tool_call_id,
                    message.tool_name,
                    AgentToolResult(content=message.content, details=message.details),
                    message.is_error,
                )
            elif isinstance(message, BranchSummaryMessage):
                self.add_item(
                    "branch_summary",
                    "Branch summary (Ctrl+O to expand)",
                    tool_result_text=message.summary,
                )
            elif isinstance(message, CompactionSummaryMessage):
                self.add_item(
                    "compaction_summary",
                    "Compaction summary (Ctrl+O to expand)",
                    tool_result_text=message.summary,
                )

    def add_assistant_message(
        self,
        message: AssistantMessage,
        *,
        include_tool_calls: bool = True,
    ) -> None:
        """Project canonical assistant blocks into display state in order."""
        for block in message.content:
            if isinstance(block, ThinkingContent):
                if block.thinking:
                    self.add_item("thinking", block.thinking)
            elif isinstance(block, TextContent):
                if block.text:
                    self.add_item("assistant", block.text)
            elif include_tool_calls:
                self.add_tool_call(block)

    def add_assistant_error(self, message: AssistantMessage) -> None:
        """Project any partial response followed by its terminal error."""
        self.add_assistant_message(message, include_tool_calls=False)
        text = message.error_message or "Error"
        self.error = text
        self.add_item("error", f"Error: {text}")

    def _read_skill_name(self, tool_call: ToolCall) -> str | None:
        if tool_call.name != "read":
            return None
        path = _string_argument(tool_call.arguments, "path")
        if path is None:
            return None
        read_path = _normalized_path(path)
        for skill in self.skills:
            if _normalized_path(skill.path) == read_path:
                return skill.name
        return None


def _parse_branch_summary_message(content: str) -> str | None:
    prefix = (
        "The following is a summary of a branch that this conversation came back from:\n<summary>\n"
    )
    suffix = "\n</summary>"
    if content.startswith(prefix) and content.endswith(suffix):
        return content.removeprefix(prefix).removesuffix(suffix)
    return None


def _parse_compaction_summary_message(content: str) -> str | None:
    prefix = "Previous conversation summary:\n"
    if content.startswith(prefix):
        return content.removeprefix(prefix)
    return None


def format_elapsed(seconds: float) -> str:
    """Format an elapsed duration tersely: 23s, 1m 23s, 1h 2m."""
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def format_tool_call_block(tool_call: ToolCall) -> str:
    """Format a collapsed tool call for live and restored transcript blocks."""
    invocation = format_tool_call_invocation(tool_call)
    if tool_call.name == "bash":
        return invocation
    return f"→ {invocation}"


def format_tool_call_invocation(tool_call: ToolCall) -> str:
    """Format a tool call as a terse human-readable invocation."""
    arguments = tool_call.arguments
    if tool_call.name == "read":
        path = _string_argument(arguments, "path")
        if path is None:
            return _fallback_tool_call_invocation(tool_call)
        return f"read {path}{_read_line_suffix(arguments)}"
    if tool_call.name == "edit":
        path = _string_argument(arguments, "path")
        if path is None:
            return _fallback_tool_call_invocation(tool_call)
        return f"edit {path}"
    if tool_call.name == "write":
        path = _string_argument(arguments, "path")
        if path is None:
            return _fallback_tool_call_invocation(tool_call)
        return f"write {path}"
    if tool_call.name == "bash":
        command = _string_argument(arguments, "command")
        if command is None:
            return _fallback_tool_call_invocation(tool_call)
        timeout = _number_argument(arguments, "timeout")
        suffix = f" (timeout {timeout:g}s)" if timeout is not None else ""
        return f"$ {command}{suffix}"
    return _fallback_tool_call_invocation(tool_call)


def _read_line_suffix(arguments: dict[str, JSONValue]) -> str:
    offset = _int_argument(arguments, "offset")
    limit = _int_argument(arguments, "limit")
    if offset is None and limit is None:
        return ""
    start = 1 if offset is None else max(1, offset)
    if limit is None:
        return f":{start}-"
    return f":{start}-{start + max(1, limit) - 1}"


FALLBACK_INVOCATION_ARGS_CHARS = 160


def _fallback_tool_call_invocation(tool_call: ToolCall) -> str:
    if tool_call.arguments:
        rendered = str(tool_call.arguments)
        if len(rendered) > FALLBACK_INVOCATION_ARGS_CHARS:
            rendered = rendered[:FALLBACK_INVOCATION_ARGS_CHARS].rstrip() + "…"
        return f"{tool_call.name} {rendered}"
    return tool_call.name


def _string_argument(arguments: dict[str, JSONValue], key: str) -> str | None:
    value = arguments.get(key)
    return value if isinstance(value, str) else None


def _normalized_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _int_argument(arguments: dict[str, JSONValue], key: str) -> int | None:
    value = arguments.get(key)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _number_argument(arguments: dict[str, JSONValue], key: str) -> int | float | None:
    value = arguments.get(key)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int | float) else None


def format_tool_result_summary(*, name: str, ok: bool) -> str:
    """Format a terse tool result line for orphaned results."""
    status = "✓" if ok else "✗"
    return f"{status} {name}"


def format_tool_result_block(
    *,
    name: str,
    ok: bool,
    content: str,
    data: dict[str, JSONValue] | None = None,
) -> str:
    """Format a tool result for live and restored transcript blocks."""
    status = "✓" if ok else "✗"
    lines = [f"{status} {name}"]
    if content:
        lines.append(_preview_text(content, max_lines=TOOL_RESULT_PREVIEW_LINES))
    patch = _result_patch(name=name, ok=ok, data=data)
    if patch:
        lines.extend(["", "Patch:", _preview_text(patch, max_lines=TOOL_PATCH_PREVIEW_LINES)])
    return "\n".join(lines)


def format_terminal_command_result_block(
    *,
    ok: bool,
    added_to_context: bool,
    output: str,
) -> str:
    """Format an input-bar terminal command result for visible TUI display."""
    status = "✓" if ok else "✗"
    suffix = " · added to context" if added_to_context else " · not added to context"
    lines = [f"{status} bash{suffix}"]
    if output:
        lines.append(_preview_text(output, max_lines=TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES))
    return "\n".join(lines)


def _result_patch(
    *,
    name: str,
    ok: bool,
    data: dict[str, JSONValue] | None,
) -> str | None:
    if name != "edit" or not ok or data is None:
        return None
    patch = data.get("patch")
    return patch if isinstance(patch, str) and patch.strip() else None


def _preview_text(text: str, *, max_lines: int) -> str:
    lines = text.splitlines()
    if not lines:
        return text[:TOOL_RESULT_PREVIEW_CHARS]

    preview_lines = lines[:max_lines]
    preview = "\n".join(preview_lines)
    hidden_lines = max(0, len(lines) - len(preview_lines))

    truncated_by_chars = len(preview) > TOOL_RESULT_PREVIEW_CHARS
    if truncated_by_chars:
        preview = preview[:TOOL_RESULT_PREVIEW_CHARS].rstrip()

    if hidden_lines or truncated_by_chars:
        details: list[str] = []
        if hidden_lines:
            details.append(f"{hidden_lines} more line{'s' if hidden_lines != 1 else ''}")
        if truncated_by_chars:
            details.append("additional text")
        preview = f"{preview}\n\n[Preview only: {', '.join(details)} hidden from the TUI.]"
    return preview
