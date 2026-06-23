"""Small Textual widgets for Tau's interactive TUI."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import TimeoutExpired, run
from typing import Any, Protocol, cast

from pygments.lexers import get_lexer_by_name  # type: ignore[import-untyped]
from pygments.util import ClassNotFound  # type: ignore[import-untyped]
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.events import Resize
from textual.geometry import Offset
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import Markdown as TextualMarkdown
from textual.widgets import Static
from textual.widgets.markdown import MarkdownStream

from tau_agent.tools import AgentTool
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.skills import Skill
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.tui.autocomplete import CompletionState
from tau_coding.tui.config import TAU_DARK_THEME, TuiRoleStyle, TuiTheme
from tau_coding.tui.state import ChatItem, TuiState

TAU_SIDEBAR_LOGO = "τ = 2π"


@dataclass(frozen=True, slots=True)
class TranscriptLine:
    """Plain transcript line used by compatibility inspection helpers."""

    text: str


@dataclass(frozen=True, slots=True)
class _RenderedSelectionLine:
    """One rendered transcript line mapped back to copyable body text."""

    rendered_y: int
    rendered_prefix_width: int
    text: str


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
    def context_window_tokens(self) -> int: ...

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


_SELECTABLE_MARKDOWN_BLOCKS: dict[type[Any], type[Any]] = {}


class ThemedMarkdownWidget(TextualMarkdown):
    """Textual Markdown widget reserved for Tau transcript streaming."""

    def __init__(self, markdown: str | None = None, *, theme: TuiTheme) -> None:
        del theme
        super().__init__(markdown)

    def get_block_class(self, block_name: str) -> type[Any]:
        """Return Markdown blocks that expose per-cell selection offsets."""
        return _selectable_markdown_block_class(super().get_block_class(block_name))


def _selectable_markdown_block_class(block_class: type[Any]) -> type[Any]:
    cached = _SELECTABLE_MARKDOWN_BLOCKS.get(block_class)
    if cached is not None:
        return cached

    class SelectableMarkdownBlock(block_class):  # type: ignore[misc]
        """Markdown block with live Textual selection painting."""

        def render_line(self, y: int) -> Strip:
            strip = cast(Strip, super().render_line(y)).apply_offsets(0, y)
            selection = self.text_selection
            if selection is None:
                return strip
            span = selection.get_span(y)
            if span is None:
                return strip
            start, end = span
            if end == -1:
                end = strip.cell_length
            return _stylize_strip_range(
                strip,
                start=start,
                end=end,
                style=self.screen.get_visual_style("screen--selection").rich_style,
            )

        def get_selection(self, selection: Selection) -> tuple[str, str] | None:
            visual = self._render()
            text = str(visual) if isinstance(visual, Text | Content) else self.source
            if text is None:
                return None
            selected_text = _extract_text_selection(text, selection)
            if not selected_text:
                return None
            return selected_text, "\n"

    SelectableMarkdownBlock.__name__ = f"Selectable{block_class.__name__}"
    _SELECTABLE_MARKDOWN_BLOCKS[block_class] = SelectableMarkdownBlock
    return SelectableMarkdownBlock


class TranscriptMessageWidget(Static):
    """One selectable transcript message block."""

    DEFAULT_CSS = """
    TranscriptMessageWidget {
        width: 1fr;
        height: auto;
    }
    """

    def __init__(
        self,
        item: ChatItem,
        *,
        theme: TuiTheme,
        show_tool_results: bool,
    ) -> None:
        self.item = item
        self.selection_text = transcript_item_selection_text(
            item,
            show_tool_results=show_tool_results,
        )
        super().__init__(
            render_chat_item(
                item,
                theme=theme,
                show_tool_results=show_tool_results,
            ),
            expand=True,
            shrink=True,
            markup=False,
            classes="transcript-message",
        )

    def render_line(self, y: int) -> Strip:
        """Render one line with Textual selection offsets and selection styling."""
        strip = super().render_line(y).apply_offsets(0, y)
        selection = self.text_selection
        if selection is None:
            return strip
        span = selection.get_span(y)
        if span is None:
            return strip
        start, end = span
        if end == -1:
            end = strip.cell_length
        return _stylize_strip_range(
            strip,
            start=start,
            end=end,
            style=self.screen.get_visual_style("screen--selection").rich_style,
        )

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return selected text from this message, not the whole transcript."""
        selected_text = _extract_rendered_selection(self, selection)
        if selected_text is None:
            selected_text = _extract_text_selection(self.selection_text, selection)
        if not selected_text:
            return None
        return selected_text, "\n"


class StreamingTranscriptMessageWidget(Vertical):
    """One assistant or thinking message block that accepts streamed fragments."""

    DEFAULT_CSS = """
    StreamingTranscriptMessageWidget {
        width: 1fr;
        height: auto;
        margin: 1 1 1 0;
    }

    StreamingTranscriptMessageWidget > .streaming-message-row {
        height: auto;
    }

    StreamingTranscriptMessageWidget > .streaming-message-row > .streaming-message-gutter {
        width: 1;
        height: auto;
    }

    StreamingTranscriptMessageWidget > .streaming-message-row > ThemedMarkdownWidget {
        width: 1fr;
        height: auto;
        padding: 0 1 0 1;
    }
    """

    def __init__(self, item: ChatItem, *, theme: TuiTheme) -> None:
        if item.role not in {"assistant", "thinking"}:
            raise ValueError("Streaming transcript widgets only support assistant/thinking items")
        self.item = item
        self.selection_text = item.text
        self._theme = theme
        self._markdown: ThemedMarkdownWidget | None = None
        self._stream: MarkdownStream | None = None
        super().__init__(classes="transcript-message")

    def compose(self) -> Any:
        self._markdown = ThemedMarkdownWidget(self.item.text, theme=self._theme)
        with Horizontal(classes="streaming-message-row"):
            yield Static("▌", classes="streaming-message-gutter")
            yield self._markdown

    @property
    def stream(self) -> MarkdownStream:
        markdown = self._markdown
        if markdown is None:
            raise RuntimeError("Streaming transcript widget is not mounted")
        if self._stream is None:
            self._stream = markdown.get_stream(markdown)
        return self._stream

    async def append_fragment(self, fragment: str) -> None:
        """Append streamed markdown without rebuilding the whole transcript."""
        if not fragment:
            return
        self.item.text += fragment
        self.selection_text += fragment
        await self.stream.write(fragment)

    async def replace_text(self, text: str) -> None:
        """Replace the current markdown text, usually with the final provider message."""
        self.item.text = text
        self.selection_text = text
        if self._markdown is not None:
            self._stream = None
            await self._markdown.update(text)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return selected text from this streamed message block."""
        selected_text = _extract_text_selection(self.selection_text, selection)
        if not selected_text:
            return None
        return selected_text, "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        """Refresh markdown children so Textual can paint live selection spans."""
        super().selection_updated(selection)
        if self._markdown is not None:
            self._markdown.selection_updated(selection)


class TranscriptView(VerticalScroll):
    """Scrollable transcript view backed by individual selectable message widgets."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        for legacy_option in ("wrap", "highlight", "markup"):
            kwargs.pop(legacy_option, None)
        min_width = kwargs.pop("min_width", None)
        super().__init__(*args, **kwargs)
        self.min_width = min_width
        if min_width is not None:
            self.styles.min_width = min_width
        self._render_state: TuiState | None = None
        self._render_theme: TuiTheme = TAU_DARK_THEME
        self._last_render_width = 0
        self._active_assistant_widget: StreamingTranscriptMessageWidget | None = None
        self._active_thinking_widget: StreamingTranscriptMessageWidget | None = None
        self._hidden_thinking_placeholder_visible = False

    def update_from_state(
        self,
        state: TuiState,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Redraw the transcript from display state."""
        self._render_state = state
        self._render_theme = theme
        self._redraw(scroll_end=True)

    def on_resize(self, event: Resize) -> None:
        """Re-render transcript entries when the terminal width changes."""
        del event
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
        self.remove_children(
            [
                child
                for child in self.children
                if isinstance(child, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
            ]
        )
        self._active_assistant_widget = None
        self._active_thinking_widget = None
        self._hidden_thinking_placeholder_visible = False
        hidden_thinking_placeholder = False
        for item in state.items:
            if item.role == "thinking" and not state.show_thinking:
                if not hidden_thinking_placeholder:
                    self.mount(
                        TranscriptMessageWidget(
                            ChatItem(
                                role="thinking",
                                text="Thinking… Press Ctrl+T to show thinking tokens.",
                            ),
                            theme=theme,
                            show_tool_results=state.show_tool_results,
                        )
                    )
                    hidden_thinking_placeholder = True
                continue
            hidden_thinking_placeholder = False
            self.mount(
                TranscriptMessageWidget(
                    item,
                    theme=theme,
                    show_tool_results=state.show_tool_results or item.always_show_tool_result,
                )
            )
        if state.assistant_buffer:
            self.mount(
                TranscriptMessageWidget(
                    ChatItem(role="assistant", text=state.assistant_buffer),
                    theme=theme,
                    show_tool_results=state.show_tool_results,
                )
            )
        self.refresh(layout=True)
        if scroll_end:
            self.scroll_end(animate=False)

    async def append_item(
        self,
        item: ChatItem,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_tool_results: bool = False,
        scroll_end: bool = True,
    ) -> TranscriptMessageWidget | StreamingTranscriptMessageWidget:
        """Append one transcript item without rebuilding previous blocks."""
        self._render_theme = theme
        widget = _transcript_widget(
            item,
            theme=theme,
            show_tool_results=show_tool_results,
        )
        await self.mount(widget)
        self._active_assistant_widget = None
        self._active_thinking_widget = None
        self._hidden_thinking_placeholder_visible = False
        self._last_render_width = self.scrollable_content_region.width
        self.refresh(layout=True)
        if scroll_end:
            self.scroll_end(animate=False)
        return widget

    async def start_assistant_message(
        self,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        scroll_end: bool = True,
    ) -> StreamingTranscriptMessageWidget:
        """Create the active assistant message widget if needed."""
        if self._active_assistant_widget is not None:
            return self._active_assistant_widget
        widget = StreamingTranscriptMessageWidget(
            ChatItem(role="assistant", text=""),
            theme=theme,
        )
        self._render_theme = theme
        await self.mount(widget)
        self._active_assistant_widget = widget
        self._last_render_width = self.scrollable_content_region.width
        if scroll_end:
            self.scroll_end(animate=False)
        return widget

    async def append_assistant_delta(
        self,
        delta: str,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        scroll_end: bool = True,
    ) -> None:
        """Append streamed assistant text to the active message widget."""
        self._active_thinking_widget = None
        self._hidden_thinking_placeholder_visible = False
        widget = await self.start_assistant_message(theme=theme, scroll_end=scroll_end)
        await widget.append_fragment(delta)
        if scroll_end:
            self.scroll_end(animate=False)

    async def append_thinking_delta(
        self,
        delta: str,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_thinking: bool,
        scroll_end: bool = True,
    ) -> None:
        """Append streamed thinking text or one hidden-thinking placeholder."""
        if not show_thinking:
            if self._hidden_thinking_placeholder_visible:
                return
            await self.append_item(
                ChatItem(
                    role="thinking",
                    text="Thinking… Press Ctrl+T to show thinking tokens.",
                ),
                theme=theme,
                scroll_end=scroll_end,
            )
            self._hidden_thinking_placeholder_visible = True
            return
        self._hidden_thinking_placeholder_visible = False
        if self._active_thinking_widget is None:
            self._active_thinking_widget = StreamingTranscriptMessageWidget(
                ChatItem(role="thinking", text=""),
                theme=theme,
            )
            await self.mount(self._active_thinking_widget)
        await self._active_thinking_widget.append_fragment(delta)
        if scroll_end:
            self.scroll_end(animate=False)

    async def finish_assistant_message(self, text: str | None = None) -> None:
        """Finalize the active assistant widget after the provider sends the full message."""
        widget = self._active_assistant_widget
        if widget is None:
            if text:
                await self.append_item(
                    ChatItem(role="assistant", text=text),
                    theme=self._render_theme,
                )
            return
        if text is not None and text != widget.selection_text:
            await widget.replace_text(text)
        self._active_assistant_widget = None

    @property
    def lines(self) -> tuple[TranscriptLine, ...]:
        """Compatibility text view for tests and lightweight transcript inspection."""
        messages = [
            child
            for child in self.children
            if isinstance(child, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
        ]
        return tuple(
            TranscriptLine(line)
            for message in messages
            for line in message.selection_text.splitlines()
        )


def _transcript_widget(
    item: ChatItem,
    *,
    theme: TuiTheme,
    show_tool_results: bool,
) -> TranscriptMessageWidget | StreamingTranscriptMessageWidget:
    if item.role in {"assistant", "thinking"}:
        return StreamingTranscriptMessageWidget(item, theme=theme)
    return TranscriptMessageWidget(
        item,
        theme=theme,
        show_tool_results=show_tool_results,
    )


def transcript_item_selection_text(
    item: ChatItem,
    *,
    show_tool_results: bool = False,
) -> str:
    """Return the plain text represented by a selectable transcript item."""
    return _visible_chat_text(item, show_tool_results=show_tool_results)


def _stylize_strip_range(strip: Strip, *, start: int, end: int, style: Any) -> Strip:
    """Apply a Rich style to a cell range inside a Textual strip."""
    start = max(start, 0)
    end = min(end, strip.cell_length)
    if end <= start:
        return strip
    before = strip.crop(0, start)
    selected = strip.crop(start, end).apply_style(style)
    after = strip.crop(end, None)
    return Strip.join([before, selected, after])


def _extract_rendered_selection(
    widget: TranscriptMessageWidget,
    selection: Selection,
) -> str | None:
    lines = _rendered_selection_lines(widget)
    if not lines:
        return None
    selected_lines: list[str] = []
    for line in lines:
        span = selection.get_span(line.rendered_y)
        if span is None:
            continue
        start, end = span
        text_start = max(start - line.rendered_prefix_width, 0)
        text_end = len(line.text) if end == -1 else max(end - line.rendered_prefix_width, 0)
        selected_lines.append(line.text[text_start:text_end])
    return "\n".join(selected_lines)


def _rendered_selection_lines(widget: TranscriptMessageWidget) -> list[_RenderedSelectionLine]:
    if widget.size.height <= 0:
        return []
    rendered_lines = [widget.render_line(y).text for y in range(widget.size.height)]
    content_bounds = _rendered_content_bounds(rendered_lines)
    if content_bounds is None:
        return []
    first_content_line, last_content_line = content_bounds
    body_prefix_width = _body_prefix_width(rendered_lines[first_content_line])
    selection_lines: list[_RenderedSelectionLine] = []
    for rendered_y in range(first_content_line, last_content_line + 1):
        line = rendered_lines[rendered_y]
        prefix_width = _line_prefix_width(line, body_prefix_width)
        selection_lines.append(
            _RenderedSelectionLine(
                rendered_y=rendered_y,
                rendered_prefix_width=prefix_width,
                text=line[prefix_width:].rstrip(),
            )
        )
    return selection_lines


def _rendered_content_bounds(lines: list[str]) -> tuple[int, int] | None:
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return None
    last = next(index for index in range(len(lines) - 1, -1, -1) if lines[index].strip())
    return first, last


def _body_prefix_width(first_content_line: str) -> int:
    if first_content_line.startswith("▌"):
        if len(first_content_line) > 1 and first_content_line[1] == " ":
            return 2
        return 1
    return len(first_content_line) - len(first_content_line.lstrip(" "))


def _line_prefix_width(line: str, body_prefix_width: int) -> int:
    if line.startswith("▌"):
        return min(body_prefix_width, len(line))
    prefix_width = 0
    while prefix_width < min(body_prefix_width, len(line)) and line[prefix_width] == " ":
        prefix_width += 1
    return prefix_width


def _extract_text_selection(text: str, selection: Selection) -> str:
    clipped_selection = _clip_selection_to_text(selection, text)
    return clipped_selection.extract(text)


def _clip_selection_to_text(selection: Selection, text: str) -> Selection:
    lines = text.splitlines()
    if not lines:
        return Selection(Offset(0, 0), Offset(0, 0))
    return Selection(
        _clip_selection_offset(selection.start, lines),
        _clip_selection_offset(selection.end, lines),
    )


def _clip_selection_offset(offset: Offset | None, lines: list[str]) -> Offset | None:
    if offset is None:
        return None
    line_index = min(max(offset.y, 0), len(lines) - 1)
    column = min(max(offset.x, 0), len(lines[line_index]))
    return Offset(column, line_index)


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
    equation = Text(TAU_SIDEBAR_LOGO, style=f"bold {theme.prompt_text}")

    return Group(
        Padding(Align.center(equation), (0, 0, 1, 0)),
        _sidebar_section("session", metadata, theme=theme),
        _sidebar_separator(theme=theme),
        _sidebar_section("context", context, theme=theme),
        _sidebar_separator(theme=theme),
        _sidebar_section("tools", tools, theme=theme),
        _sidebar_separator(theme=theme),
        _sidebar_section("skills", skills, theme=theme),
        _sidebar_separator(theme=theme),
        _sidebar_section("prompts", prompts, theme=theme),
    )


def _sidebar_section(
    title: str,
    body: RenderableType,
    *,
    theme: TuiTheme,
) -> RenderableType:
    """Render one sidebar section without a surrounding border."""
    header = Text(title, style=f"bold {theme.accent}")
    return Group(Padding(header, (0, 0, 0, 1)), Padding(body, (0, 0, 1, 1)))


def _sidebar_separator(*, theme: TuiTheme) -> RenderableType:
    """Render a subtle divider between sidebar sections."""
    return Padding(Rule(style=theme.border), (0, 0, 1, 0))


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
) -> RenderableType:
    """Render a chat item as a standalone Toad-inspired transcript block."""
    role_style = _chat_item_role_style(item, theme)
    body = (
        _render_tool_chat_body(
            item,
            body_style=theme.role_styles["tool"].body,
            accent_style=_tool_accent_style(item, theme=theme),
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
            return TuiRoleStyle(
                border=_tool_success_color(theme),
                body=theme.role_styles["tool"].body,
            )
        if item.tool_result_text.startswith("✗"):
            return TuiRoleStyle(border="#ff4f4f", body=theme.role_styles["tool"].body)
    return theme.role_styles[item.role]


def _tool_accent_style(item: ChatItem, *, theme: TuiTheme) -> str | None:
    if item.role != "tool" or not item.tool_result_text:
        return None
    if item.tool_result_text.startswith("✓"):
        return _tool_success_style(theme)
    if item.tool_result_text.startswith("✗"):
        return _tool_error_style(theme)
    return None


def _tool_success_color(theme: TuiTheme) -> str:
    if theme.name == "tau-light":
        return "#166534"
    return "#9cffb1"


def _tool_success_style(theme: TuiTheme) -> str:
    color = _tool_success_color(theme)
    if theme.name == "tau-light":
        return color
    return f"{color} on #000000"


def _tool_error_style(theme: TuiTheme) -> str:
    if theme.name == "tau-light":
        return theme.role_styles["error"].border
    return "#ff4f4f on #000000"


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
    if item.role == "branch_summary":
        if show_tool_results and item.tool_result_text:
            return f"**Branch Summary**\n\n{item.tool_result_text}"
        return item.text
    if item.role == "compaction_summary":
        if show_tool_results and item.tool_result_text:
            return f"**Compaction Summary**\n\n{item.tool_result_text}"
        return item.text
    if item.role not in {"tool", "skill"} or not show_tool_results or not item.tool_result_text:
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
    if role in {"assistant", "thinking", "status"}:
        if _has_unclosed_fence(text):
            return _plain_text(text, body_style=body_style)
        return ThemedMarkdown(
            text,
            style=body_style,
            code_theme=syntax_theme,
            inline_code_theme=syntax_theme,
            heading_style=theme.accent,
            highlight_style=_markdown_highlight_style(theme),
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
        highlight_style: str,
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
        self.highlight_style = highlight_style

    def __rich_console__(self, console: Console, options: Any) -> Any:
        with console.use_theme(_markdown_theme(self.heading_style, self.highlight_style)):
            yield from super().__rich_console__(console, options)


def _markdown_highlight_style(theme: TuiTheme) -> str:
    if theme.name == "tau-light":
        return theme.highlight_text
    return theme.accent


def _markdown_theme(heading_style: str, highlight_style: str) -> Theme:
    accent = Style.parse(heading_style)
    highlight = Style.parse(highlight_style)
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
            "markdown.code": highlight,
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
        return (
            f"{_compact_token_count(session.context_token_estimate)}"
            f"/{_compact_token_count(session.context_window_tokens)} context"
        )
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
