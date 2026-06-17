"""Small Textual widgets for Tau's interactive TUI."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.widgets import RichLog, Static

from tau_agent.tools import AgentTool
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.skills import Skill
from tau_coding.tui.autocomplete import CompletionState
from tau_coding.tui.state import ChatItem, TuiState

_ROLE_BLOCK_STYLES = {
    "user": ("#58a6ff", "white on #101923"),
    "assistant": ("#3fb950", "white on #101f17"),
    "tool": ("#d29922", "white on #201b10"),
    "error": ("#f85149", "white on #241315"),
    "status": ("#8b949e", "white on #161b22"),
}


class SessionSummarySource(Protocol):
    """Session attributes displayed by the sidebar."""

    @property
    def cwd(self) -> Path: ...

    @property
    def model(self) -> str: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def tools(self) -> Sequence[AgentTool]: ...

    @property
    def skills(self) -> Sequence[Skill]: ...

    @property
    def prompt_templates(self) -> Sequence[PromptTemplate]: ...


class SessionSidebar(Static):
    """Compact sidebar with current session metadata."""

    def update_from_session(self, session: SessionSummarySource) -> None:
        """Redraw the sidebar from current session metadata."""
        self.update(render_session_sidebar(session))


class TranscriptView(RichLog):
    """Scrollable transcript view backed by ``TuiState``."""

    def update_from_state(self, state: TuiState) -> None:
        """Redraw the transcript from display state."""
        self.clear()
        for item in state.items:
            self.write(render_chat_item(item), expand=True, shrink=True, scroll_end=True)
        if state.assistant_buffer:
            self.write(
                render_chat_item(ChatItem(role="assistant", text=state.assistant_buffer)),
                expand=True,
                shrink=True,
                scroll_end=True,
            )


def render_session_sidebar(session: SessionSummarySource) -> RenderableType:
    """Render a dark, minimalist summary of the active coding session."""
    metadata = Table.grid(padding=(0, 1))
    metadata.add_column(style="bright_black", no_wrap=True)
    metadata.add_column(style="white")
    metadata.add_row("provider", session.provider_name)
    metadata.add_row("model", session.model)
    metadata.add_row("cwd", _short_path(session.cwd))
    metadata.add_row("tools", str(len(session.tools)))
    metadata.add_row("skills", str(len(session.skills)))

    tools = _bullet_list([tool.name for tool in session.tools], empty="No tools")
    skills = _bullet_list([skill.name for skill in session.skills], empty="No skills loaded yet")
    prompts = _bullet_list(
        [template.name for template in session.prompt_templates],
        empty="No prompt templates",
    )

    return Group(
        Panel(metadata, title="session", border_style="bright_black", padding=(0, 1)),
        Panel(tools, title="tools", border_style="cyan", padding=(0, 1)),
        Panel(skills, title="skills", border_style="green", padding=(0, 1)),
        Panel(prompts, title="prompts", border_style="magenta", padding=(0, 1)),
    )


def render_chat_item(item: ChatItem) -> Panel:
    """Render a chat item as a standalone colored transcript block."""
    border_style, body_style = _ROLE_BLOCK_STYLES[item.role]
    body = _render_chat_body(item.text, body_style=body_style)
    return Panel(
        body,
        border_style=border_style,
        style=body_style,
        padding=(0, 1),
        expand=True,
    )


def _render_chat_body(text: str, *, body_style: str) -> RenderableType:
    patch_body = _render_patch_body(text, body_style=body_style)
    if patch_body is not None:
        return patch_body
    fenced_body = _render_fenced_body(text, body_style=body_style)
    if fenced_body is not None:
        return fenced_body
    return _plain_text(text, body_style=body_style)


def _render_patch_body(text: str, *, body_style: str) -> RenderableType | None:
    marker = "\nPatch:\n"
    if marker not in text:
        return None
    before_patch, patch = text.split(marker, 1)
    if not patch.strip():
        return None
    return Group(
        _plain_text(f"{before_patch}{marker.rstrip()}", body_style=body_style),
        Syntax(
            patch.rstrip("\n"),
            "diff",
            theme="ansi_dark",
            word_wrap=True,
            background_color="default",
        ),
    )


def _render_fenced_body(text: str, *, body_style: str) -> RenderableType | None:
    if "```" not in text:
        return None

    renderables: list[RenderableType] = []
    cursor = 0
    while cursor < len(text):
        fence_start = text.find("```", cursor)
        if fence_start == -1:
            _append_plain(renderables, text[cursor:], body_style=body_style)
            break

        line_start = text.rfind("\n", 0, fence_start) + 1
        if line_start != fence_start:
            return None

        fence_line_end = text.find("\n", fence_start)
        if fence_line_end == -1:
            return None
        closing_start = text.find("\n```", fence_line_end + 1)
        if closing_start == -1:
            return None

        _append_plain(renderables, text[cursor:fence_start], body_style=body_style)
        language = _fence_language(text[fence_start + 3 : fence_line_end])
        code = text[fence_line_end + 1 : closing_start]
        renderables.append(
            Syntax(
                code.rstrip("\n"),
                language,
                theme="ansi_dark",
                word_wrap=True,
                background_color="default",
            )
        )
        closing_line_end = text.find("\n", closing_start + 1)
        cursor = len(text) if closing_line_end == -1 else closing_line_end + 1

    return Group(*renderables) if renderables else None


def _append_plain(
    renderables: list[RenderableType],
    text: str,
    *,
    body_style: str,
) -> None:
    if text:
        renderables.append(_plain_text(text.rstrip("\n"), body_style=body_style))


def _plain_text(text: str, *, body_style: str) -> Text:
    return Text(text, style=body_style, overflow="fold", no_wrap=False)


def _fence_language(raw: str) -> str:
    language = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
    return language or "text"


def render_completion_suggestions(state: CompletionState) -> Text:
    """Render prompt completion suggestions."""
    text = Text()
    for index, item in enumerate(state.items[:6]):
        if index:
            text.append("\n")
        selected = index == state.selected_index
        prefix = "› " if selected else "  "
        style = "bold white on #238636" if selected else "white"
        description_style = "white on #238636" if selected else "bright_black"
        text.append(prefix, style=style)
        text.append(item.display, style=style)
        if item.description:
            text.append("  ")
            text.append(item.description, style=description_style)
    return text


def _bullet_list(items: Sequence[str], *, empty: str) -> Text:
    text = Text()
    if not items:
        text.append(empty, style="bright_black")
        return text

    for index, item in enumerate(items):
        if index:
            text.append("\n")
        text.append("• ", style="bright_black")
        text.append(item, style="white")
    return text


def _short_path(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)
