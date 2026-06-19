"""Small Textual widgets for Tau's interactive TUI."""

from collections.abc import Sequence
from pathlib import Path
from subprocess import TimeoutExpired, run
from typing import Any, Protocol

from pygments.lexers import get_lexer_by_name  # type: ignore[import-untyped]
from pygments.util import ClassNotFound  # type: ignore[import-untyped]
from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from textual.events import Resize
from textual.selection import Selection
from textual.widgets import RichLog, Static

from tau_agent.tools import AgentTool
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.skills import Skill
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.tui.autocomplete import CompletionState
from tau_coding.tui.config import TAU_DARK_THEME, TuiRoleStyle, TuiTheme
from tau_coding.tui.state import ChatItem, TuiState

TAU_SIDEBAR_LOGO = """████████  █████  ██   ██
   ██    ██   ██ ██   ██
   ██    ███████ ██   ██
   ██    ██   ██ ██   ██
   ██    ██   ██  █████

       τ = 2π"""


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

    @property
    def context_files(self) -> Sequence[ProjectContextFile]: ...

    @property
    def context_token_estimate(self) -> int: ...

    @property
    def auto_compact_token_threshold(self) -> int | None: ...

    @property
    def thinking_level(self) -> str: ...


class SessionSidebar(Static):
    """Compact sidebar with current session metadata."""

    def update_from_session(
        self,
        session: SessionSummarySource,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Redraw the sidebar from current session metadata."""
        self.update(render_session_sidebar(session, theme=theme))


class CompactSessionInfo(Static):
    """Single-line session metadata for narrow TUI layouts."""

    def update_from_session(
        self,
        session: SessionSummarySource,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Redraw compact session metadata."""
        self.update(render_compact_session_info(session, theme=theme))


class TranscriptView(RichLog):
    """Scrollable transcript view backed by ``TuiState``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._render_state: TuiState | None = None
        self._render_theme: TuiTheme = TAU_DARK_THEME
        self._last_render_width = 0

    def update_from_state(
        self,
        state: TuiState,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Redraw the transcript from display state."""
        self._render_state = state
        self._render_theme = theme
        self._redraw(scroll_end=state.selected_item_index is None)

    def on_resize(self, event: Resize) -> None:
        """Re-render transcript entries when the terminal width changes."""
        super().on_resize(event)
        if self._render_state is None:
            return
        width = self.scrollable_content_region.width
        if width <= 0 or width == self._last_render_width:
            return
        was_at_end = self.is_vertical_scroll_end
        self._redraw(scroll_end=was_at_end)
        self.scroll_to(x=0, animate=False, immediate=True)

    def _redraw(self, *, scroll_end: bool) -> None:
        state = self._render_state
        if state is None:
            return
        theme = self._render_theme
        self._last_render_width = self.scrollable_content_region.width
        self.clear()
        for index, item in enumerate(state.items):
            if item.role == "thinking" and not state.show_thinking:
                continue
            self.write(
                render_chat_item(
                    item,
                    theme=theme,
                    show_tool_results=state.show_tool_results,
                    selected=index == state.selected_item_index,
                ),
                expand=True,
                shrink=True,
                scroll_end=scroll_end,
            )
        if state.assistant_buffer:
            self.write(
                render_chat_item(
                    ChatItem(role="assistant", text=state.assistant_buffer),
                    theme=theme,
                    show_tool_results=state.show_tool_results,
                ),
                expand=True,
                shrink=True,
                scroll_end=scroll_end,
            )

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return selected transcript text from RichLog's rendered line buffer."""
        if not self.lines:
            return None
        text = "\n".join(line.text.rstrip() for line in self.lines)
        return selection.extract(text), "\n"


def render_session_sidebar(
    session: SessionSummarySource,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
) -> RenderableType:
    """Render a dark, minimalist summary of the active coding session."""
    metadata = Table.grid(padding=(0, 1))
    metadata.add_column(style=theme.completion_description, no_wrap=True)
    metadata.add_column(style=theme.prompt_text)
    metadata.add_row("provider", session.provider_name)
    metadata.add_row("model", session.model)
    metadata.add_row("thinking", _thinking_level(session))
    metadata.add_row("tools", str(len(session.tools)))
    metadata.add_row("skills", str(len(session.skills)))

    tools = _bullet_list([tool.name for tool in session.tools], empty="No tools", theme=theme)
    skills = _bullet_list(
        [skill.name for skill in session.skills],
        empty="No skills loaded yet",
        theme=theme,
    )
    prompts = _bullet_list(
        [template.name for template in session.prompt_templates],
        empty="No prompt templates",
        theme=theme,
    )
    context = _bullet_list(
        _context_file_labels(session.context_files, cwd=session.cwd),
        empty="No context files",
        theme=theme,
    )
    logo = Text(TAU_SIDEBAR_LOGO, style=f"bold {theme.prompt_text}")

    return Group(
        Padding(logo, (0, 0, 1, 1)),
        Panel(
            metadata,
            title="session",
            box=box.SQUARE,
            border_style=theme.border,
            padding=(0, 1),
        ),
        Panel(
            context,
            title="context",
            box=box.SQUARE,
            border_style=theme.border,
            padding=(0, 1),
        ),
        Panel(
            tools,
            title="tools",
            box=box.SQUARE,
            border_style=theme.border,
            padding=(0, 1),
        ),
        Panel(
            skills,
            title="skills",
            box=box.SQUARE,
            border_style=theme.border,
            padding=(0, 1),
        ),
        Panel(
            prompts,
            title="prompts",
            box=box.SQUARE,
            border_style=theme.border,
            padding=(0, 1),
        ),
    )


def render_compact_session_info(
    session: SessionSummarySource,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
) -> RenderableType:
    """Render the session facts below the prompt."""
    left = Text(
        f"{_short_path(session.cwd)} ({_git_branch(session.cwd)})",
        style=theme.prompt_text,
        overflow="fold",
        no_wrap=False,
    )
    right = Text(style=theme.muted_text, overflow="fold", no_wrap=False, justify="right")
    right.append(_context_usage(session), style=theme.completion_description)
    right.append("  ")
    right.append(f"{session.provider_name}:{session.model}", style=theme.prompt_text)
    right.append(" ")
    right.append(f"({_thinking_level(session)})", style=theme.completion_description)

    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(ratio=1, justify="right")
    table.add_row(left, right)
    return table


def render_chat_item(
    item: ChatItem,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
    show_tool_results: bool = False,
    selected: bool = False,
) -> RenderableType:
    """Render a chat item as a standalone Toad-inspired transcript block."""
    role_style = _chat_item_role_style(item, theme)
    if selected:
        role_style = TuiRoleStyle(border=theme.accent, body=role_style.body)
    body = (
        _render_tool_chat_body(
            item,
            body_style=theme.role_styles["tool"].body,
            accent_style=_tool_accent_style(item),
            show_tool_results=show_tool_results,
        )
        if item.role == "tool"
        else _render_chat_body(
            _visible_chat_text(item, show_tool_results=show_tool_results),
            role=item.role,
            body_style=role_style.body,
            syntax_theme=theme.syntax_theme,
            theme=theme,
        )
    )
    table = Table.grid(expand=True)
    table.add_column(width=1, style=role_style.border)
    table.add_column(ratio=1, style=role_style.body)
    table.add_row(
        Align.left(Text("▌", style=role_style.border)),
        Padding(body, (0, 1, 0, 1), style=role_style.body),
    )
    return Padding(table, (1, 1, 1, 0), style=role_style.body)


def _chat_item_role_style(item: ChatItem, theme: TuiTheme) -> TuiRoleStyle:
    if item.role == "tool" and item.tool_result_text:
        if item.tool_result_text.startswith("✓"):
            return TuiRoleStyle(border="#9cffb1", body=theme.role_styles["tool"].body)
        if item.tool_result_text.startswith("✗"):
            return TuiRoleStyle(border="#ff4f4f", body=theme.role_styles["tool"].body)
    return theme.role_styles[item.role]


def _tool_accent_style(item: ChatItem) -> str | None:
    if item.role != "tool" or not item.tool_result_text:
        return None
    if item.tool_result_text.startswith("✓"):
        return "#9cffb1 on #000000"
    if item.tool_result_text.startswith("✗"):
        return "#ff4f4f on #000000"
    return None


def _render_tool_chat_body(
    item: ChatItem,
    *,
    body_style: str,
    accent_style: str | None,
    show_tool_results: bool,
) -> Text:
    text = _render_tool_invocation(item.text, body_style=body_style, accent_style=accent_style)
    if show_tool_results and item.tool_result_text:
        text.append("\n\n")
        text.append(item.tool_result_text, style=body_style)
    return text


def _render_tool_invocation(text: str, *, body_style: str, accent_style: str | None) -> Text:
    rendered = Text(style=body_style, overflow="fold", no_wrap=False)
    accent_style = accent_style or body_style
    prefix, name, remainder = _split_tool_invocation(text)
    rendered.append(prefix, style=body_style)
    rendered.append(name, style=body_style)
    rendered.append(remainder, style=accent_style)
    return rendered


def _split_tool_invocation(text: str) -> tuple[str, str, str]:
    if text.startswith("→ "):
        rest = text[2:]
        name, separator, remainder = rest.partition(" ")
        return "→ ", name, f"{separator}{remainder}" if separator else ""
    if text.startswith("$ "):
        return "$", "", text[1:]
    name, separator, remainder = text.partition(" ")
    return "", name, f"{separator}{remainder}" if separator else ""


def _visible_chat_text(item: ChatItem, *, show_tool_results: bool) -> str:
    if item.role != "tool" or not show_tool_results or not item.tool_result_text:
        return item.text
    return f"{item.text}\n\n{item.tool_result_text}"


def _render_chat_body(
    text: str,
    *,
    role: str,
    body_style: str,
    syntax_theme: str,
    theme: TuiTheme,
) -> RenderableType:
    patch_body = _render_patch_body(
        text,
        body_style=body_style,
        syntax_theme=syntax_theme,
    )
    if patch_body is not None:
        return patch_body
    if role == "assistant":
        if _has_unclosed_fence(text):
            return _plain_text(text, body_style=body_style)
        return ThemedMarkdown(
            text,
            style=body_style,
            code_theme=syntax_theme,
            inline_code_theme=syntax_theme,
            heading_style=theme.accent,
        )
    fenced_body = _render_fenced_body(
        text,
        body_style=body_style,
        syntax_theme=syntax_theme,
    )
    if fenced_body is not None:
        return fenced_body
    if "```" in text:
        return _plain_text(text, body_style=body_style)
    return _plain_text(text, body_style=body_style)


def _render_patch_body(
    text: str,
    *,
    body_style: str,
    syntax_theme: str,
) -> RenderableType | None:
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
            theme=syntax_theme,
            word_wrap=True,
            background_color="default",
        ),
    )


class ThemedMarkdown(Markdown):
    """Markdown renderer with Tau's softer heading/accent colors."""

    def __init__(
        self,
        markup: str,
        *,
        heading_style: str,
        code_theme: str,
        inline_code_theme: str,
        style: str = "none",
    ) -> None:
        super().__init__(
            markup,
            style=style,
            code_theme=code_theme,
            inline_code_theme=inline_code_theme,
        )
        self.heading_style = heading_style

    def __rich_console__(self, console: Console, options: Any) -> Any:
        with console.use_theme(_markdown_theme(self.heading_style)):
            yield from super().__rich_console__(console, options)


def _markdown_theme(heading_style: str) -> Theme:
    accent = Style.parse(heading_style)
    return Theme(
        {
            "markdown.h1": accent + Style(bold=True, underline=True),
            "markdown.h2": accent + Style(bold=True),
            "markdown.h3": accent + Style(bold=True),
            "markdown.h4": accent + Style(bold=True),
            "markdown.h5": accent + Style(bold=True),
            "markdown.h6": accent + Style(bold=True),
            "markdown.item.bullet": accent,
            "markdown.item.number": accent,
            "markdown.block_quote": accent,
        }
    )


def _render_fenced_body(
    text: str,
    *,
    body_style: str,
    syntax_theme: str,
) -> RenderableType | None:
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
        language = _syntax_language(text[fence_start + 3 : fence_line_end])
        code = text[fence_line_end + 1 : closing_start]
        renderables.append(
            Syntax(
                code.rstrip("\n"),
                language,
                theme=syntax_theme,
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


def _context_usage(session: SessionSummarySource) -> str:
    threshold = session.auto_compact_token_threshold
    if threshold is None or threshold <= 0:
        return f"{_compact_token_count(session.context_token_estimate)} context"
    return (
        f"{_compact_token_count(session.context_token_estimate)}"
        f"/{_compact_token_count(threshold)} context"
    )


def _compact_token_count(value: int) -> str:
    if value <= 0:
        return "0k"
    if value < 1000:
        return "<1k"
    return f"{(value + 500) // 1000}k"


def _context_file_labels(
    context_files: Sequence[ProjectContextFile],
    *,
    cwd: Path,
) -> list[str]:
    return [_context_file_label(Path(context_file.path), cwd=cwd) for context_file in context_files]


def _context_file_label(path: Path, *, cwd: Path) -> str:
    expanded_path = path.expanduser()
    if not expanded_path.is_absolute():
        expanded_path = cwd / expanded_path
    try:
        return str(expanded_path.resolve().relative_to(cwd.expanduser().resolve()))
    except OSError, ValueError:
        return _short_path(expanded_path)


def _thinking_level(session: SessionSummarySource) -> str:
    available = getattr(session, "available_thinking_levels", None)
    if available == ():
        return "unavailable"
    explicit_level = getattr(session, "thinking_level", None)
    if explicit_level:
        return str(explicit_level)
    state = getattr(session, "state", None)
    thinking_level = getattr(state, "thinking_level", None)
    return str(thinking_level) if thinking_level else "--"


def _git_branch(cwd: Path) -> str:
    try:
        result = run(
            ["git", "-C", str(cwd), "branch", "--show-current"],
            capture_output=True,
            check=False,
            text=True,
            timeout=0.5,
        )
    except OSError:
        return "--"
    except TimeoutExpired:
        return "--"
    branch = result.stdout.strip()
    if branch:
        return branch
    return "--"


def _has_unclosed_fence(text: str) -> bool:
    fence_count = sum(1 for line in text.splitlines() if line.startswith("```"))
    return fence_count % 2 == 1


def _fence_language(raw: str) -> str:
    language = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
    return language or "text"


def _syntax_language(raw: str) -> str:
    language = _fence_language(raw)
    if language == "text":
        return language
    try:
        get_lexer_by_name(language)
    except ClassNotFound:
        return "text"
    return language


def render_completion_suggestions(
    state: CompletionState,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
) -> Text:
    """Render prompt completion suggestions."""
    text = Text()
    for index, item in enumerate(state.items):
        if index:
            text.append("\n")
        selected = index == state.selected_index
        prefix = "› " if selected else "  "
        style = theme.completion_selected if selected else theme.prompt_text
        description_style = (
            theme.completion_selected_description if selected else theme.completion_description
        )
        text.append(prefix, style=style)
        text.append(item.display, style=style)
        if item.description:
            text.append("  ")
            text.append(item.description, style=description_style)
    return text


def _bullet_list(
    items: Sequence[str],
    *,
    empty: str,
    theme: TuiTheme,
) -> Text:
    text = Text()
    if not items:
        text.append(empty, style=theme.completion_description)
        return text

    for index, item in enumerate(items):
        if index:
            text.append("\n")
        text.append("• ", style=theme.completion_description)
        text.append(item, style=theme.prompt_text)
    return text


def _short_path(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)
