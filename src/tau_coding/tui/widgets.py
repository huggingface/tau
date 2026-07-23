"""Small Textual widgets for Tau's interactive TUI."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import TimeoutExpired, run
from typing import Any, ClassVar, Literal, Protocol

from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from rich.align import Align
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.markdown import CodeBlock, Heading, Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Style as TextualStyle  # type: ignore[attr-defined]
from textual.css.query import NoMatches
from textual.geometry import Offset
from textual.selection import Selection
from textual.widget import Widget
from textual.widgets import Markdown as TextualMarkdown
from textual.widgets import Static
from textual.widgets.markdown import MarkdownBlock, MarkdownStream

from tau_agent.tools import AgentTool
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.session_stats import SessionStats
from tau_coding.skills import Skill
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.tui.autocomplete import CompletionState
from tau_coding.tui.config import TAU_DARK_THEME, TuiRoleStyle, TuiTheme
from tau_coding.tui.state import ChatItem, TuiState
from tau_coding.version import current_version

TAU_SIDEBAR_LOGO = "τ = 2π"
SIDEBAR_BULLET_LIST_LIMIT = 5
SIDEBAR_COMMA_LIST_MAX_LINES = 3


@dataclass(frozen=True, slots=True)
class TranscriptLine:
    """Plain transcript line used by compatibility inspection helpers."""

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

    @property
    def session_title(self) -> str | None: ...

    @property
    def extension_names(self) -> Sequence[str]: ...

    @property
    def session_stats(self) -> SessionStats: ...


class SessionSidebar(Vertical):
    """Compact sidebar with session metadata and bottom-aligned branding."""

    def compose(self) -> Any:
        yield Static("", id="sidebar-content")
        yield Static("", id="sidebar-brand")

    _summary_fingerprint: tuple[object, ...] | None = None

    def update_from_session(
        self,
        session: SessionSummarySource,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Redraw the sidebar only when displayed session metadata changed."""
        fingerprint = _session_summary_fingerprint(session, theme=theme)
        if fingerprint == self._summary_fingerprint:
            return
        self._summary_fingerprint = fingerprint
        self.query_one("#sidebar-content", Static).update(
            render_session_sidebar(session, theme=theme),
            layout=False,
        )
        self.query_one("#sidebar-brand", Static).update(
            _sidebar_brand(theme=theme),
            layout=False,
        )


class CompactSessionInfo(Static):
    """Single-line session metadata for narrow TUI layouts."""

    _summary_fingerprint: tuple[object, ...] | None = None

    def update_from_session(
        self,
        session: SessionSummarySource,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Redraw compact session metadata only when its inputs changed."""
        fingerprint = _session_summary_fingerprint(session, theme=theme)
        if fingerprint == self._summary_fingerprint:
            return
        self._summary_fingerprint = fingerprint
        self.update(render_compact_session_info(session, theme=theme))


def _session_summary_fingerprint(
    session: SessionSummarySource,
    *,
    theme: TuiTheme,
) -> tuple[object, ...]:
    return (
        theme.name,
        session.cwd,
        session.provider_name,
        session.model,
        session.thinking_level,
        session.context_token_estimate,
        session.auto_compact_token_threshold,
        session.context_window_tokens,
        session.session_title,
        session.session_stats,
        tuple(session.extension_names),
        tuple(tool.name for tool in session.tools),
        tuple(skill.name for skill in session.skills),
        tuple(template.name for template in session.prompt_templates),
        tuple(context.path for context in session.context_files),
    )


class TauMarkdownBlock(MarkdownBlock):
    """Markdown block that applies Tau's themed inline link color."""

    DEFAULT_CSS = """
    TauMarkdownBlock {
        link-style: none;
        link-style-hover: underline;
        link-background-hover: transparent;
    }
    """

    @property
    def allow_select(self) -> bool:
        """Only allow native selection once Textual has mounted the block.

        Textual may hit freshly-created Markdown blocks during a mouse-down before
        they have a parent. Its selection startup path assumes selected content
        widgets have a parent container, so an unmounted selectable Markdown block
        can crash with ``container is None``.
        """
        return self.parent is not None and super().allow_select

    def _token_to_content(self, token: Any) -> Any:
        content = super()._token_to_content(token)
        markdown = self._markdown
        if not isinstance(markdown, ThemedMarkdownWidget):
            return content
        link_style = TextualStyle.parse(markdown.tau_link_style)
        spans = []
        for span in content.spans:
            style = span.style
            if isinstance(style, TextualStyle) and "@click" in style.meta:
                style = link_style + style
            spans.append(type(span)(span.start, span.end, style))
        return type(content)(content.plain, spans=spans)


class ThemedMarkdownWidget(TextualMarkdown):
    """Textual Markdown widget reserved for Tau transcript streaming."""

    BLOCKS = {**TextualMarkdown.BLOCKS, "paragraph_open": TauMarkdownBlock}

    DEFAULT_CSS = """
    ThemedMarkdownWidget MarkdownH1,
    ThemedMarkdownWidget MarkdownH2,
    ThemedMarkdownWidget MarkdownH3,
    ThemedMarkdownWidget MarkdownH4,
    ThemedMarkdownWidget MarkdownH5,
    ThemedMarkdownWidget MarkdownH6 {
        color: $tau-markdown-highlight;
        content-align: left middle;
        text-style: bold;
    }

    ThemedMarkdownWidget MarkdownBlock > .code_inline {
        color: $tau-markdown-inline-code !important;
        background: transparent !important;
    }

    ThemedMarkdownWidget MarkdownBullet {
        color: $tau-markdown-bullet;
    }

    ThemedMarkdownWidget MarkdownFence {
        background: $tau-markdown-code-block-background;
        overflow-x: auto;
        scrollbar-size-horizontal: 1;
    }

    /* Textual's built-in `MarkdownFence:light` rule (type + pseudo-class)
       outranks the plain descendant selector above, so restate the themed
       background for light themes. */
    ThemedMarkdownWidget MarkdownFence:light {
        background: $tau-markdown-code-block-background;
    }

    ThemedMarkdownWidget MarkdownTableContent {
        keyline: thin $tau-markdown-table-border;
    }

    ThemedMarkdownWidget MarkdownTableContent > .header {
        color: $tau-markdown-table-header;
        text-style: bold;
    }
    """

    def __init__(
        self,
        markdown: str | None = None,
        *,
        theme: TuiTheme,
        classes: str | None = None,
    ) -> None:
        self.tau_link_style = theme.markdown_link
        super().__init__(markdown, classes=classes)


# Roles rendered as free-flowing text with no left accent or role background,
# matching how they appear while streaming.
_BORDERLESS_TRANSCRIPT_ROLES = frozenset({"assistant", "thinking"})
_HIDDEN_THINKING_PLACEHOLDER = "Thinking… Press Ctrl+T to show thinking tokens."
TRANSCRIPT_WINDOW_ITEMS = 200
TRANSCRIPT_WINDOW_PAGE_ITEMS = 80
TRANSCRIPT_WINDOW_OVERSCAN_ITEMS = 40


class TranscriptWindowBoundary(Static):
    """Small paging sentinel shown when transcript items are outside the DOM window."""

    ALLOW_SELECT = False
    DEFAULT_CSS = """
    TranscriptWindowBoundary {
        width: 1fr;
        height: 1;
        margin: 0 1;
        color: $tau-muted-text;
        content-align: center middle;
    }
    """

    def __init__(self, direction: Literal["earlier", "later"], count: int) -> None:
        self.direction = direction
        super().__init__(self._label(count), classes=f"transcript-window-{direction}")

    def update_count(self, count: int) -> None:
        """Update the hidden-item count without scheduling a layout pass."""
        self.update(self._label(count), layout=False)

    def _label(self, count: int) -> str:
        noun = "message" if count == 1 else "messages"
        arrow = "↑" if self.direction == "earlier" else "↓"
        return f"{arrow} Scroll for {count} {self.direction} {noun}"


class TranscriptMessageWidget(Horizontal):
    """One selectable transcript message rendered as a full-height role block."""

    DEFAULT_CSS = """
    TranscriptMessageWidget {
        width: 1fr;
        height: auto;
        margin: 1 1 2 0;
    }

    TranscriptMessageWidget > .transcript-message-body {
        width: 1fr;
        height: auto;
        padding: 0 1 0 1;
    }

    TranscriptMessageWidget > .transcript-markdown-body > MarkdownParagraph {
        margin: 0 0 1 0;
    }

    """

    def __init__(
        self,
        item: ChatItem,
        *,
        theme: TuiTheme,
        show_tool_results: bool,
        custom_markup: str | None = None,
        invocation: str | None = None,
        result_markup: str | None = None,
    ) -> None:
        self.item = item
        self._custom_markup = custom_markup if item.role == "custom" else None
        self._invocation = invocation if item.role == "tool" else None
        self._result_markup = result_markup if item.role == "tool" else None
        self.selection_text = transcript_item_selection_text(
            item,
            show_tool_results=show_tool_results,
            custom_markup=self._custom_markup,
            invocation=self._invocation,
            result_markup=self._result_markup,
        )
        self._markdown_text = _transcript_item_markdown(
            item,
            show_tool_results=show_tool_results,
            invocation=self._invocation,
        )
        self._theme = theme
        self._role_style = _chat_item_role_style(item, theme)
        self._plain_render_key = (
            self.selection_text,
            self._role_style,
            self._invocation,
            self._result_markup,
        )
        super().__init__(classes="transcript-message")
        if item.role == "user":
            self.styles.padding = (1, 0)
        foreground, background = _split_rich_style_colors(self._role_style.body)
        self._body_foreground = foreground
        if item.role in _BORDERLESS_TRANSCRIPT_ROLES:
            self._body_background = None
        else:
            self._body_background = background
            self.styles.border_left = ("tall", self._role_style.border)
            if background:
                self.styles.background = background

    def compose(self) -> Any:
        yield self._body_widget()

    def _body_widget(self) -> Static | ThemedMarkdownWidget:
        body: Static | ThemedMarkdownWidget
        if self.item.role == "custom":
            return Static(
                _custom_body_renderable(
                    self._custom_markup,
                    raw_text=self.item.text,
                    body_style=self._role_style.body,
                ),
                expand=True,
                shrink=True,
                markup=False,
                classes="transcript-message-body transcript-plain-body",
            )
        if _use_plain_transcript_body(self.item):
            body = Static(
                _transcript_plain_body_text(
                    self.item,
                    text=self.selection_text,
                    body_style=self._role_style.body,
                    theme=self._theme,
                    invocation=self._invocation,
                    result_markup=self._result_markup,
                ),
                expand=True,
                shrink=True,
                markup=False,
                classes="transcript-message-body transcript-plain-body",
            )
        else:
            body = ThemedMarkdownWidget(
                self._markdown_text,
                theme=self._theme,
                classes="transcript-message-body transcript-markdown-body",
            )
        if self._body_foreground:
            body.styles.color = self._body_foreground
        if self._body_background:
            body.styles.background = self._body_background
        return body

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return selected plain text from this message, not rendered Markdown markup."""
        selected_text = _extract_text_selection(self.selection_text, selection)
        if not selected_text:
            return None
        return selected_text, "\n"

    def refresh_invocation(
        self,
        *,
        show_tool_results: bool,
        invocation: str | None = None,
        result_markup: str | None = None,
    ) -> bool:
        """Re-render a plain-body row's text in place; False when unsupported.

        Used for high-frequency updates (spinner frames, live tool progress)
        where remounting the widget causes visible layout flicker.
        """
        if self.item.role == "custom" or not _use_plain_transcript_body(self.item):
            return False
        self._invocation = invocation if self.item.role == "tool" else None
        self._result_markup = result_markup if self.item.role == "tool" else None
        next_role_style = _chat_item_role_style(self.item, self._theme)
        next_selection_text = transcript_item_selection_text(
            self.item,
            show_tool_results=show_tool_results,
            invocation=self._invocation,
            result_markup=self._result_markup,
        )
        next_render_key = (
            next_selection_text,
            next_role_style,
            self._invocation,
            self._result_markup,
        )
        if next_render_key == self._plain_render_key:
            return True
        self._plain_render_key = next_render_key
        self.selection_text = next_selection_text
        self._role_style = next_role_style
        foreground, background = _split_rich_style_colors(self._role_style.body)
        self._body_foreground = foreground
        self._body_background = background
        self.styles.border_left = ("tall", self._role_style.border)
        if background:
            self.styles.background = background
        self._markdown_text = _transcript_item_markdown(
            self.item,
            show_tool_results=show_tool_results,
            invocation=self._invocation,
        )
        try:
            body = self.query_one(".transcript-plain-body", Static)
        except NoMatches:
            return False
        body.update(
            _transcript_plain_body_text(
                self.item,
                text=self.selection_text,
                body_style=self._role_style.body,
                theme=self._theme,
                invocation=self._invocation,
                result_markup=self._result_markup,
            )
        )
        if foreground:
            body.styles.color = foreground
        if background:
            body.styles.background = background
        return True


class StreamingTranscriptMessageWidget(ThemedMarkdownWidget):
    """One assistant or thinking Markdown block that accepts streamed fragments."""

    DEFAULT_CSS = """
    StreamingTranscriptMessageWidget {
        width: 1fr;
        height: auto;
        margin: 1 1 2 1;
        padding: 0 1 0 0;
    }

    StreamingTranscriptMessageWidget > MarkdownParagraph {
        margin: 0 0 1 0;
    }

    StreamingTranscriptMessageWidget.-streaming MarkdownFence {
        overflow-x: hidden;
        scrollbar-size-horizontal: 0;
    }

    StreamingTranscriptMessageWidget.-finalized MarkdownFence {
        overflow-x: auto;
        scrollbar-size-horizontal: 1;
    }
    """

    def __init__(self, item: ChatItem, *, theme: TuiTheme) -> None:
        if item.role not in {"assistant", "thinking"}:
            raise ValueError("Streaming transcript widgets only support assistant/thinking items")
        self.item = item
        self.selection_text = item.text
        self._stream: MarkdownStream | None = None
        self._is_streaming = True
        super().__init__(item.text, theme=theme)
        self.add_class("transcript-message")
        self.add_class("-streaming")
        # Apply the role foreground so streamed text matches the finalized block
        # (e.g. dimmed thinking) instead of shifting color on the next redraw.
        foreground, _ = _split_rich_style_colors(_chat_item_role_style(item, theme).body)
        if foreground:
            self.styles.color = foreground

    @property
    def stream(self) -> MarkdownStream:
        if self._stream is None:
            self._stream = self.get_stream(self)
        return self._stream

    async def append_fragment(self, fragment: str) -> None:
        """Append streamed markdown without reparsing the full accumulated message."""
        if not fragment:
            return
        self.item.text += fragment
        self.selection_text += fragment
        await self.stream.write(fragment)

    async def _stop_stream(self) -> None:
        """Stop the Textual markdown stream, flushing pending fragments first."""
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        await stream.stop()

    async def replace_text(self, text: str) -> None:
        """Replace the current markdown text, usually with corrected final content."""
        await self._stop_stream()
        self.item.text = text
        self.selection_text = text
        await self.update(text)

    async def finalize(self, text: str | None = None) -> None:
        """Mark the streamed message complete and restore finalized Markdown chrome."""
        if text is not None and text != self.selection_text:
            await self.replace_text(text)
        else:
            if text is not None:
                self.item.text = text
                self.selection_text = text
            await self._stop_stream()
        self._is_streaming = False
        self.remove_class("-streaming")
        self.add_class("-finalized")

    async def on_unmount(self) -> None:
        """Cancel the markdown stream task if the widget is removed mid-stream."""
        await self._stop_stream()

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return selected text from this streamed message block."""
        selected_text = _extract_text_selection(self.selection_text, selection)
        if not selected_text:
            return None
        return selected_text, "\n"


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
        self._active_assistant_widget: StreamingTranscriptMessageWidget | None = None
        self._active_thinking_widget: StreamingTranscriptMessageWidget | None = None
        self._active_message_widgets: list[Widget] = []
        self._hidden_thinking_placeholder_visible = False
        self._follow_output = True
        self._follow_scroll_pending = False
        self._window_start = 0
        self._window_end = 0
        self._window_shift_pending = False
        self._item_widgets: dict[
            int, TranscriptMessageWidget | StreamingTranscriptMessageWidget
        ] = {}
        self._top_boundary: TranscriptWindowBoundary | None = None
        self._bottom_boundary: TranscriptWindowBoundary | None = None

    def on_mount(self) -> None:
        """Follow new transcript content until the user scrolls away."""
        self.follow_output()

    def follow_output(self) -> None:
        """Return to follow mode for a user-driven turn or explicit jump to bottom."""
        self._follow_output = True
        self.anchor(True)
        self._request_follow_scroll(force=True)

    def _request_follow_scroll(self, *, force: bool = False) -> None:
        """Scroll to the bottom after layout if follow mode is still active."""
        if self._follow_scroll_pending and not force:
            return
        self._follow_scroll_pending = True

        def scroll_if_still_following() -> None:
            self._follow_scroll_pending = False
            if force or self._follow_output or self.is_vertical_scroll_end:
                self.scroll_end(animate=False, immediate=True)

        self.call_after_refresh(scroll_if_still_following)

    @property
    def _should_follow_output(self) -> bool:
        """Return whether new content should keep the viewport pinned to the bottom."""
        return self._follow_output or self.is_vertical_scroll_end

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Track follow mode and page the bounded transcript window near its edges."""
        super().watch_scroll_y(old_value, new_value)
        if new_value < old_value:
            self._follow_output = False
        elif new_value >= self.max_scroll_y and self._window_is_latest:
            self._follow_output = True

        if new_value <= 1 and self._window_start > 0:
            self._schedule_window_shift("earlier")
        elif (
            new_value >= max(0, self.max_scroll_y - 1)
            and self._render_state is not None
            and self._window_end < len(self._render_state.items)
        ):
            self._schedule_window_shift("later")

    @property
    def _window_is_latest(self) -> bool:
        state = self._render_state
        return state is None or self._window_end >= len(state.items)

    def _schedule_window_shift(self, direction: Literal["earlier", "later"]) -> None:
        if self._window_shift_pending:
            return
        self._window_shift_pending = True
        self.call_later(self._shift_window, direction)

    async def _shift_window(self, direction: Literal["earlier", "later"]) -> None:
        """Move the mounted window while keeping one existing message as the anchor."""
        state = self._render_state
        if state is None or not state.items:
            self._window_shift_pending = False
            return
        old_start = self._window_start
        old_end = self._window_end
        if direction == "earlier":
            if old_start <= 0:
                self._window_shift_pending = False
                return
            anchor_item = state.items[old_start] if old_start < len(state.items) else None
            new_start = max(0, old_start - TRANSCRIPT_WINDOW_PAGE_ITEMS)
            new_end = min(len(state.items), new_start + TRANSCRIPT_WINDOW_ITEMS)
        else:
            if old_end >= len(state.items):
                self._window_shift_pending = False
                return
            anchor_item = state.items[old_end - 1] if old_end > old_start else None
            new_end = min(len(state.items), old_end + TRANSCRIPT_WINDOW_PAGE_ITEMS)
            new_start = max(0, new_end - TRANSCRIPT_WINDOW_ITEMS)

        self._window_start = new_start
        self._window_end = new_end
        self._redraw(scroll_end=False, preserve_window=True)

        def restore_anchor() -> None:
            try:
                if anchor_item is None:
                    return
                anchor = self._item_widgets.get(id(anchor_item))
                if anchor is not None:
                    self.scroll_to_widget(
                        anchor,
                        top=direction == "earlier",
                        animate=False,
                        immediate=True,
                        force=True,
                    )
            finally:
                self._window_shift_pending = False

        self.call_after_refresh(restore_anchor)

    async def _finalize_active_thinking_message(self) -> None:
        """Stop streaming for a completed thinking block before another block starts."""
        widget = self._active_thinking_widget
        if widget is None:
            return
        await widget.finalize()
        self._active_thinking_widget = None

    async def _finalize_active_assistant_message(self) -> None:
        """Stop streaming for a completed assistant block before another block starts."""
        widget = self._active_assistant_widget
        if widget is None:
            return
        await widget.finalize()
        self._active_assistant_widget = None

    def update_from_state(
        self,
        state: TuiState,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Render display state while keeping the mounted transcript DOM bounded."""
        same_state = self._render_state is state
        retained_projection = same_state and any(
            id(item) in self._item_widgets for item in state.items
        )
        should_follow = self._should_follow_output
        if not retained_projection:
            self._follow_output = True
            should_follow = True
        self._render_state = state
        self._render_theme = theme
        self._redraw(
            scroll_end=should_follow,
            preserve_window=retained_projection and not should_follow,
        )

    def update_thinking_visibility(
        self,
        state: TuiState,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Update thinking rows in the mounted window without touching unrelated widgets."""
        self._render_state = state
        self._render_theme = theme
        should_follow = self._should_follow_output
        previous_scroll_y = self.scroll_y
        window_items = state.items[self._window_start : self._window_end]
        message_children = [
            child
            for child in self.children
            if isinstance(child, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
        ]
        thinking_children = [child for child in message_children if child.item.role == "thinking"]
        non_thinking_children = [
            child for child in message_children if child.item.role != "thinking"
        ]
        if thinking_children:
            self.remove_children(thinking_children)
        for item_id, widget in tuple(self._item_widgets.items()):
            if widget in thinking_children:
                del self._item_widgets[item_id]

        non_thinking_index = 0
        pending: list[tuple[ChatItem, TranscriptMessageWidget]] = []
        hidden_run = False

        def flush(before: Widget | None) -> None:
            nonlocal pending
            if not pending:
                return
            widgets = [widget for _, widget in pending]
            self.mount(*widgets, before=before)
            for item, widget in pending:
                self._item_widgets[id(item)] = widget
            pending = []

        for item in window_items:
            if item.role == "thinking":
                if state.show_thinking:
                    pending.append(
                        (
                            item,
                            TranscriptMessageWidget(
                                item,
                                theme=theme,
                                show_tool_results=state.show_tool_results,
                            ),
                        )
                    )
                elif not hidden_run:
                    placeholder = TranscriptMessageWidget(
                        ChatItem(role="thinking", text=_HIDDEN_THINKING_PLACEHOLDER),
                        theme=theme,
                        show_tool_results=state.show_tool_results,
                    )
                    pending.append((item, placeholder))
                    hidden_run = True
                else:
                    self._item_widgets[id(item)] = pending[-1][1]
                continue

            hidden_run = False
            target = None
            while non_thinking_index < len(non_thinking_children):
                candidate = non_thinking_children[non_thinking_index]
                non_thinking_index += 1
                if candidate.item is item:
                    target = candidate
                    break
            if target is not None:
                flush(target)

        flush(self._bottom_boundary)
        self._active_thinking_widget = None
        self._hidden_thinking_placeholder_visible = any(
            widget.selection_text == _HIDDEN_THINKING_PLACEHOLDER for _, widget in pending
        ) or _last_transcript_child_is_hidden_thinking_placeholder(self.children)
        self.refresh(layout=True)
        if should_follow:
            self._request_follow_scroll()
        else:
            self.call_after_refresh(
                lambda: self.scroll_to(y=previous_scroll_y, animate=False, immediate=True)
            )

    def _redraw(self, *, scroll_end: bool, preserve_window: bool = False) -> None:
        state = self._render_state
        if state is None:
            return
        theme = self._render_theme
        total = len(state.items)
        if not preserve_window or self._window_end <= self._window_start:
            self._window_end = total
            self._window_start = max(0, total - TRANSCRIPT_WINDOW_ITEMS)
        else:
            self._window_start = min(self._window_start, total)
            self._window_end = min(max(self._window_end, self._window_start), total)
            if self._window_end - self._window_start > TRANSCRIPT_WINDOW_ITEMS:
                self._window_start = self._window_end - TRANSCRIPT_WINDOW_ITEMS

        removable = [
            child
            for child in self.children
            if isinstance(
                child,
                TranscriptMessageWidget
                | StreamingTranscriptMessageWidget
                | TranscriptWindowBoundary,
            )
        ]
        if removable:
            self.remove_children(removable)
        self._active_assistant_widget = None
        self._active_thinking_widget = None
        self._active_message_widgets = []
        self._hidden_thinking_placeholder_visible = False
        self._item_widgets.clear()
        self._top_boundary = None
        self._bottom_boundary = None

        widgets: list[Widget] = []
        if self._window_start > 0:
            self._top_boundary = TranscriptWindowBoundary("earlier", self._window_start)
            widgets.append(self._top_boundary)

        hidden_thinking_widget: TranscriptMessageWidget | None = None
        for item in state.items[self._window_start : self._window_end]:
            if item.role == "thinking" and not state.show_thinking:
                if hidden_thinking_widget is None:
                    hidden_thinking_widget = TranscriptMessageWidget(
                        ChatItem(role="thinking", text=_HIDDEN_THINKING_PLACEHOLDER),
                        theme=theme,
                        show_tool_results=state.show_tool_results,
                    )
                    widgets.append(hidden_thinking_widget)
                self._item_widgets[id(item)] = hidden_thinking_widget
                continue
            hidden_thinking_widget = None
            expanded = state.show_tool_results or item.always_show_tool_result
            widget = TranscriptMessageWidget(
                item,
                theme=theme,
                show_tool_results=expanded,
                custom_markup=(
                    state.resolve_custom_markup(item, expanded=state.show_tool_results)
                    if item.role == "custom"
                    else None
                ),
                invocation=state.resolve_tool_invocation(item),
                result_markup=state.resolve_tool_result(item, expanded=expanded),
            )
            self._item_widgets[id(item)] = widget
            widgets.append(widget)

        if self._window_end < total:
            self._bottom_boundary = TranscriptWindowBoundary("later", total - self._window_end)
            widgets.append(self._bottom_boundary)
        elif state.assistant_buffer:
            self._active_assistant_widget = StreamingTranscriptMessageWidget(
                ChatItem(role="assistant", text=state.assistant_buffer),
                theme=theme,
            )
            self._active_message_widgets.append(self._active_assistant_widget)
            widgets.append(self._active_assistant_widget)

        if widgets:
            self.mount(*widgets)
        self._hidden_thinking_placeholder_visible = (
            hidden_thinking_widget is not None and self._window_end == total
        )
        self.refresh(layout=True)
        if scroll_end:
            self._request_follow_scroll()

    async def append_item(
        self,
        item: ChatItem,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_tool_results: bool = False,
        scroll_end: bool = False,
        custom_markup: str | None = None,
        invocation: str | None = None,
        result_markup: str | None = None,
    ) -> TranscriptMessageWidget | StreamingTranscriptMessageWidget:
        """Append one item, paging to the latest window only for followed output."""
        should_follow = self._should_follow_output if not scroll_end else True
        await self._finalize_active_assistant_message()
        await self._finalize_active_thinking_message()
        self._render_theme = theme
        state = self._render_state
        item_index = _identity_index(state.items, item) if state is not None else None

        if state is not None and item_index is not None and self._window_end < item_index:
            if should_follow:
                self._window_end = 0
                self._redraw(scroll_end=True)
                mounted = self._item_widgets.get(id(item))
                if mounted is not None:
                    return mounted
            elif self._bottom_boundary is not None:
                self._bottom_boundary.update_count(len(state.items) - self._window_end)

        widget = _transcript_widget(
            item,
            theme=theme,
            show_tool_results=show_tool_results,
            custom_markup=custom_markup,
            invocation=invocation,
            result_markup=result_markup,
        )
        if (
            state is not None
            and item_index is not None
            and (
                item_index < self._window_start
                or (item_index > self._window_end and not should_follow)
            )
        ):
            return widget
        if state is not None and item_index is not None:
            self._window_end = max(self._window_end, item_index + 1)
        await self.mount(widget, before=self._bottom_boundary)
        self._item_widgets[id(item)] = widget
        self._active_assistant_widget = None
        self._active_thinking_widget = None
        self._active_message_widgets = []
        self._hidden_thinking_placeholder_visible = False

        if (
            state is not None
            and self._window_end - self._window_start
            > TRANSCRIPT_WINDOW_ITEMS + TRANSCRIPT_WINDOW_OVERSCAN_ITEMS
        ):
            self._window_end = len(state.items)
            self._window_start = max(0, self._window_end - TRANSCRIPT_WINDOW_ITEMS)
            self._redraw(scroll_end=should_follow, preserve_window=True)
            widget = self._item_widgets.get(id(item), widget)
        elif self._top_boundary is not None:
            self._top_boundary.update_count(self._window_start)

        self.refresh(layout=True)
        if should_follow:
            self._request_follow_scroll(force=scroll_end)
        return widget

    async def update_tool_results_visibility(
        self,
        state: TuiState,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Update only mounted rows whose rendering depends on result visibility."""
        self._render_state = state
        self._render_theme = theme
        for item in state.items[self._window_start : self._window_end]:
            if item.role not in {"tool", "skill", "branch_summary", "compaction_summary"}:
                continue
            expanded = state.show_tool_results or item.always_show_tool_result
            await self.update_item(
                item,
                theme=theme,
                show_tool_results=expanded,
                invocation=state.resolve_tool_invocation(item),
                result_markup=state.resolve_tool_result(item, expanded=expanded),
            )

    async def update_item(
        self,
        item: ChatItem,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_tool_results: bool = False,
        invocation: str | None = None,
        result_markup: str | None = None,
    ) -> bool:
        """Update a mounted item in O(1); off-screen state is rendered when paged in."""
        child = self._item_widgets.get(id(item))
        if not isinstance(child, TranscriptMessageWidget) or child.item is not item:
            return False
        # Prefer updating the mounted widget's content: remounting forces a
        # layout pass and visible flicker for live progress and elapsed timers.
        if child.refresh_invocation(
            show_tool_results=show_tool_results,
            invocation=invocation,
            result_markup=result_markup,
        ):
            return True
        replacement = _transcript_widget(
            item,
            theme=theme,
            show_tool_results=show_tool_results,
            invocation=invocation,
            result_markup=result_markup,
        )
        await self.mount(replacement, after=child)
        await child.remove()
        self._item_widgets[id(item)] = replacement
        self.refresh(layout=True)
        if self._should_follow_output:
            self._request_follow_scroll()
        return True

    async def start_assistant_message(
        self,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        scroll_end: bool = False,
    ) -> StreamingTranscriptMessageWidget:
        """Create the active assistant message widget if needed."""
        if self._active_assistant_widget is not None:
            return self._active_assistant_widget
        await self._finalize_active_thinking_message()
        should_follow = self._should_follow_output if not scroll_end else True
        widget = StreamingTranscriptMessageWidget(
            ChatItem(role="assistant", text=""),
            theme=theme,
        )
        self._render_theme = theme
        await self.mount(widget, before=self._bottom_boundary)
        self._active_assistant_widget = widget
        self._active_message_widgets.append(widget)
        if should_follow:
            self._request_follow_scroll(force=scroll_end)
        return widget

    async def append_assistant_delta(
        self,
        delta: str,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        scroll_end: bool = False,
    ) -> None:
        """Append streamed assistant text when the latest window is mounted."""
        if not self._window_is_latest:
            return
        should_follow = self._should_follow_output if not scroll_end else True
        widget = await self.start_assistant_message(theme=theme, scroll_end=scroll_end)
        await widget.append_fragment(delta)
        if should_follow:
            self._request_follow_scroll(force=scroll_end)

    async def append_thinking_delta(
        self,
        delta: str,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_thinking: bool,
        scroll_end: bool = False,
    ) -> None:
        """Append streamed thinking text or one hidden-thinking placeholder."""
        state = self._render_state
        if state is not None:
            # The adapter adds provisional thinking items before this method runs.
            was_latest = self._window_end >= max(0, len(state.items) - 1)
            if was_latest:
                self._window_end = len(state.items)
            else:
                if self._bottom_boundary is not None:
                    self._bottom_boundary.update_count(len(state.items) - self._window_end)
                return
        should_follow = self._should_follow_output if not scroll_end else True
        if not show_thinking:
            if self._hidden_thinking_placeholder_visible:
                return
            widget = TranscriptMessageWidget(
                ChatItem(
                    role="thinking",
                    text=_HIDDEN_THINKING_PLACEHOLDER,
                ),
                theme=theme,
                show_tool_results=False,
            )
            await self.mount(widget, before=self._active_assistant_widget or self._bottom_boundary)
            self._active_message_widgets.append(widget)
            self._active_thinking_widget = None
            self._hidden_thinking_placeholder_visible = True
            self.refresh(layout=True)
            if should_follow:
                self._request_follow_scroll(force=scroll_end)
            return
        self._hidden_thinking_placeholder_visible = False
        if self._active_thinking_widget is None:
            self._active_thinking_widget = StreamingTranscriptMessageWidget(
                ChatItem(role="thinking", text=""),
                theme=theme,
            )
            await self.mount(
                self._active_thinking_widget,
                before=self._active_assistant_widget or self._bottom_boundary,
            )
            self._active_message_widgets.append(self._active_thinking_widget)
        await self._active_thinking_widget.append_fragment(delta)
        if should_follow:
            self._request_follow_scroll(force=scroll_end)

    async def finish_assistant_message(
        self,
        text: str | None = None,
        *,
        item: ChatItem | None = None,
    ) -> None:
        """Finalize the active assistant widget after the provider sends the full message."""
        widget = self._active_assistant_widget
        if widget is None:
            if item is not None:
                await self.append_item(item, theme=self._render_theme)
            elif text:
                await self.append_item(
                    ChatItem(role="assistant", text=text),
                    theme=self._render_theme,
                )
            return
        await widget.finalize(text)
        if item is not None:
            widget.item = item
            self._item_widgets[id(item)] = widget
        self._active_assistant_widget = None
        self._active_message_widgets = [
            candidate for candidate in self._active_message_widgets if candidate is not widget
        ]
        self._hidden_thinking_placeholder_visible = False

    async def finish_structured_assistant_message(
        self,
        items: Sequence[ChatItem],
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_thinking: bool,
    ) -> None:
        """Replace only the provisional assistant tail with canonical ordered blocks."""
        should_follow = self._should_follow_output
        for widget in tuple(self._active_message_widgets):
            if widget.parent is self:
                await widget.remove()
        self._active_message_widgets.clear()
        self._active_assistant_widget = None
        self._active_thinking_widget = None
        self._hidden_thinking_placeholder_visible = False
        for item_id, widget in tuple(self._item_widgets.items()):
            if widget.parent is None:
                del self._item_widgets[item_id]

        state = self._render_state
        if state is not None:
            self._window_end = len(state.items)
        hidden_widget: TranscriptMessageWidget | None = None
        mounted: list[Widget] = []
        for item in items:
            if item.role == "thinking" and not show_thinking:
                if hidden_widget is None:
                    hidden_widget = TranscriptMessageWidget(
                        ChatItem(role="thinking", text=_HIDDEN_THINKING_PLACEHOLDER),
                        theme=theme,
                        show_tool_results=False,
                    )
                    mounted.append(hidden_widget)
                self._item_widgets[id(item)] = hidden_widget
                continue
            hidden_widget = None
            widget = TranscriptMessageWidget(
                item,
                theme=theme,
                show_tool_results=False,
            )
            self._item_widgets[id(item)] = widget
            mounted.append(widget)
        if mounted:
            await self.mount(*mounted, before=self._bottom_boundary)
        self._hidden_thinking_placeholder_visible = hidden_widget is not None

        if (
            state is not None
            and self._window_end - self._window_start
            > TRANSCRIPT_WINDOW_ITEMS + TRANSCRIPT_WINDOW_OVERSCAN_ITEMS
        ):
            self._window_start = max(0, self._window_end - TRANSCRIPT_WINDOW_ITEMS)
            self._redraw(scroll_end=should_follow, preserve_window=True)
        elif should_follow:
            self._request_follow_scroll()

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


def _identity_index(items: Sequence[ChatItem], target: ChatItem) -> int | None:
    """Return an item's identity-based index with an O(1) append-path fast path."""
    if items and items[-1] is target:
        return len(items) - 1
    for index in range(len(items) - 2, -1, -1):
        if items[index] is target:
            return index
    return None


def _last_transcript_child_is_hidden_thinking_placeholder(children: Sequence[Widget]) -> bool:
    for child in reversed(children):
        if isinstance(child, TranscriptMessageWidget | StreamingTranscriptMessageWidget):
            return (
                child.item.role == "thinking"
                and child.selection_text == _HIDDEN_THINKING_PLACEHOLDER
            )
    return False


def _transcript_widget(
    item: ChatItem,
    *,
    theme: TuiTheme,
    show_tool_results: bool,
    custom_markup: str | None = None,
    invocation: str | None = None,
    result_markup: str | None = None,
) -> TranscriptMessageWidget | StreamingTranscriptMessageWidget:
    if item.role in {"assistant", "thinking"}:
        return StreamingTranscriptMessageWidget(item, theme=theme)
    return TranscriptMessageWidget(
        item,
        theme=theme,
        show_tool_results=show_tool_results,
        custom_markup=custom_markup,
        invocation=invocation,
        result_markup=result_markup,
    )


def transcript_item_selection_text(
    item: ChatItem,
    *,
    show_tool_results: bool = False,
    custom_markup: str | None = None,
    invocation: str | None = None,
    result_markup: str | None = None,
) -> str:
    """Return the plain text represented by a selectable transcript item."""
    if item.role == "custom":
        return _custom_selection_text(custom_markup, item.text)
    if item.role == "tool" and result_markup is not None:
        # A tool-rendered result replaces the generic block: invocation line
        # plus the markup-stripped card.
        invocation_line = invocation if invocation else item.text
        return f"{invocation_line}\n{_custom_markup_to_text(result_markup).plain}"
    return _visible_chat_text(item, show_tool_results=show_tool_results, invocation=invocation)


def _custom_markup_to_text(markup: str) -> Text:
    """Parse Rich markup safely; fall back to literal text on malformed markup."""
    try:
        return Text.from_markup(markup)
    except Exception:  # noqa: BLE001 - a bad renderer string must never crash the TUI
        return Text(markup)


def _custom_selection_text(markup: str | None, raw_text: str) -> str:
    """Return the plain (markup-stripped) text of a custom item for selection."""
    if markup is None:
        return raw_text
    return _custom_markup_to_text(markup).plain


def _custom_body_renderable(
    markup: str | None,
    *,
    raw_text: str,
    body_style: str,
) -> RenderableType:
    """Render a custom message body from renderer markup, or raw text on fallback."""
    if markup is None:
        return Text(raw_text, style=body_style, overflow="fold", no_wrap=False)
    text = _custom_markup_to_text(markup)
    text.overflow = "fold"
    text.no_wrap = False
    return text


def _split_rich_style_colors(style: str) -> tuple[str | None, str | None]:
    """Split the foreground/background colors from a simple Rich style string."""
    text_style = Style.parse(style)
    foreground = text_style.color.name if text_style.color is not None else None
    background = text_style.bgcolor.name if text_style.bgcolor is not None else None
    return foreground, background


def _use_plain_transcript_body(item: ChatItem) -> bool:
    """Return whether a transcript item can use fast selectable plain text."""
    return item.highlight == "update" or item.role in {"user", "tool", "skill", "error"}


def _transcript_plain_body_text(
    item: ChatItem,
    *,
    text: str,
    body_style: str,
    theme: TuiTheme,
    invocation: str | None = None,
    result_markup: str | None = None,
) -> RenderableType:
    """Return styled transcript text for selectable plain rows."""
    if item.role != "tool":
        return Text(text, style=body_style, overflow="fold", no_wrap=False)

    if result_markup is not None:
        # The tool's `render_result` markup replaces the generic result block;
        # the invocation line keeps its usual status-accented rendering.
        invocation_text = _render_transcript_tool_invocation(
            invocation if invocation else item.text,
            body_style=body_style,
            accent_style=_tool_accent_style(item, theme=theme),
        )
        markup_text = _custom_markup_to_text(result_markup)
        markup_text.overflow = "fold"
        markup_text.no_wrap = False
        return Group(invocation_text, markup_text)

    invocation_line, separator, result_text = text.partition("\n\n")
    invocation_text = _render_transcript_tool_invocation(
        invocation_line,
        body_style=body_style,
        accent_style=_tool_accent_style(item, theme=theme),
    )
    if not separator:
        return invocation_text

    patch_body = _render_patch_body(
        result_text,
        body_style=body_style,
        syntax_theme=theme.syntax_theme,
        code_block_background=theme.markdown_code_block_background,
    )
    if patch_body is not None:
        return Group(invocation_text, Text(""), patch_body)

    rendered = Text(style=body_style, overflow="fold", no_wrap=False)
    rendered.append(invocation_text)
    rendered.append(separator)
    rendered.append(result_text, style=body_style)
    return rendered


def _render_transcript_tool_invocation(
    text: str,
    *,
    body_style: str,
    accent_style: str | None,
) -> Text:
    """Render a selectable tool invocation with status color after the prefix."""
    rendered = Text(style=body_style, overflow="fold", no_wrap=False)
    accent_style = accent_style or body_style
    prefix, name, remainder = _split_tool_invocation(text)
    rendered.append(prefix, style=body_style)
    rendered.append(name, style=accent_style)
    rendered.append(remainder, style=accent_style)
    return rendered


def _transcript_item_markdown(
    item: ChatItem,
    *,
    show_tool_results: bool,
    invocation: str | None = None,
) -> str:
    """Return Markdown for a transcript item using native Textual Markdown blocks."""
    visible_text = _visible_chat_text(
        item, show_tool_results=show_tool_results, invocation=invocation
    )
    if item.role in {"assistant", "thinking", "status", "branch_summary", "compaction_summary"}:
        return visible_text
    return _plain_markdown(visible_text)


def _plain_markdown(text: str) -> str:
    """Represent arbitrary plain text as wrapping Markdown paragraphs."""
    if not text:
        return ""
    return "\n".join(_escape_plain_markdown_line(line) for line in text.splitlines())


def _escape_plain_markdown_line(line: str) -> str:
    """Escape Markdown syntax while preserving plain, wrapping text."""
    escaped = line.replace("\\", "\\\\")
    for character in "`*_{}[]()#+-.!|>":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


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
    title = Text(session.session_title or "Untitled session", style=f"bold {theme.accent}")
    stats = session.session_stats
    activity = Text(
        f"{stats.turn_count} {_plural(stats.turn_count, 'turn')}, "
        f"{stats.tool_call_count} tool {_plural(stats.tool_call_count, 'call')}",
        style=theme.completion_description,
    )
    usage = Text(style=theme.completion_description)
    usage.append(f"{_compact_usage_count(stats.input_tokens)} in, ")
    usage.append(f"{_compact_usage_count(stats.output_tokens)} out")
    usage.append(" · ", style=theme.completion_description)
    if stats.estimated_cost is None:
        usage.append("$N/A", style=theme.completion_description)
    else:
        usage.append(f"~{_format_cost(stats.estimated_cost)}")

    threshold = session.auto_compact_token_threshold
    compaction = Text(
        "off" if threshold is None else f"auto at {_compact_token_count(threshold)}",
        style=theme.completion_description,
    )
    tools = _comma_list([tool.name for tool in session.tools], empty="No tools", theme=theme)
    skills = _limited_bullet_list(
        [skill.name for skill in session.skills],
        empty="No skills loaded",
        theme=theme,
    )
    prompts = _comma_list(
        [template.name for template in session.prompt_templates],
        empty="No prompt templates",
        theme=theme,
    )
    extensions = _comma_list(
        list(session.extension_names),
        empty="No extensions",
        theme=theme,
    )
    context = _limited_bullet_list(
        _context_file_labels(session.context_files, cwd=session.cwd),
        empty="No context files",
        theme=theme,
    )
    sections = (
        Padding(title, (0, 0, 0, 1)),
        _sidebar_section("activity", activity, theme=theme),
        _sidebar_section("cumulative usage", usage, theme=theme),
        _sidebar_section("compaction", compaction, theme=theme),
        _sidebar_section("context", context, theme=theme),
        _sidebar_section("tools", tools, theme=theme),
        _sidebar_section("skills", skills, theme=theme),
        _sidebar_section("prompts", prompts, theme=theme),
        _sidebar_section("extensions", extensions, theme=theme),
    )
    separated_sections: list[RenderableType] = []
    for index, section in enumerate(sections):
        if index:
            separated_sections.append(_sidebar_separator(theme=theme))
        separated_sections.append(section)

    return Group(*separated_sections)


def _sidebar_section(
    title: str,
    body: RenderableType,
    *,
    theme: TuiTheme,
) -> RenderableType:
    """Render one sidebar section without a surrounding border."""
    header = Text(title, style=f"bold {theme.prompt_text}")
    return Group(Padding(header, (0, 0, 0, 1)), Padding(body, (0, 0, 0, 1)))


def _sidebar_separator(*, theme: TuiTheme) -> RenderableType:
    """Render a spaced divider between adjacent sidebar sections."""
    return Padding(Rule(style=theme.border), (0, 0, 1, 0))


def _sidebar_brand(*, theme: TuiTheme) -> RenderableType:
    brand = Text(style=f"bold {theme.prompt_text}")
    brand.append(TAU_SIDEBAR_LOGO)
    brand.append(f"  {current_version()}", style=theme.completion_description)
    return Align.center(brand)


def render_compact_session_info(
    session: SessionSummarySource,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
) -> RenderableType:
    """Render the session facts below the prompt."""
    left = _styled_cwd(session.cwd, theme=theme)
    right = Text(style=theme.muted_text, overflow="fold", no_wrap=False, justify="right")
    right.append(session.provider_name, style=theme.completion_description)
    right.append(f":{session.model}", style=theme.prompt_text)
    right.append(" ")
    right.append(f"({_thinking_level(session)})", style=theme.completion_description)
    right.append("\n")
    right.append(_context_usage(session), style=theme.completion_description)

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
    custom_markup: str | None = None,
) -> RenderableType:
    """Render a chat item as a standalone Toad-inspired transcript block."""
    role_style = _chat_item_role_style(item, theme)
    if item.role == "custom":
        body: RenderableType = _custom_body_renderable(
            custom_markup,
            raw_text=item.text,
            body_style=role_style.body,
        )
    else:
        body = (
            _render_tool_chat_body(
                item,
                body_style=theme.role_styles["tool"].body,
                accent_style=_tool_accent_style(item, theme=theme),
                show_tool_results=show_tool_results,
                syntax_theme=theme.syntax_theme,
                theme=theme,
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
    if item.highlight == "update":
        return TuiRoleStyle(border="#ffff00", body="bold #ffff00")
    if item.role == "tool" and item.tool_result_text:
        if item.tool_result_text.startswith("✓"):
            return TuiRoleStyle(border=theme.success, body=theme.role_styles["tool"].body)
        if item.tool_result_text.startswith("✗"):
            return TuiRoleStyle(border=theme.error, body=theme.role_styles["tool"].body)
    return theme.role_styles[item.role]


def _tool_accent_style(item: ChatItem, *, theme: TuiTheme) -> str | None:
    # Bare colors: the accent span inherits its background from the tool body
    # style, so it blends with any theme's transcript background.
    if item.role != "tool":
        return None
    if item.tool_result_text is None:
        return theme.role_styles["tool"].border
    if item.tool_result_text.startswith("✓"):
        return theme.tool_success_text
    if item.tool_result_text.startswith("✗"):
        return theme.tool_error_text
    return None


def _render_tool_chat_body(
    item: ChatItem,
    *,
    body_style: str,
    accent_style: str | None,
    show_tool_results: bool,
    syntax_theme: str,
    theme: TuiTheme,
) -> RenderableType:
    text = _render_tool_invocation(item.text, body_style=body_style, accent_style=accent_style)
    if not show_tool_results or not item.tool_result_text:
        return text

    result_body = _render_chat_body(
        item.tool_result_text,
        role=item.role,
        body_style=body_style,
        syntax_theme=syntax_theme,
        theme=theme,
    )
    return Group(text, Text(""), result_body)


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


def _visible_chat_text(
    item: ChatItem,
    *,
    show_tool_results: bool,
    invocation: str | None = None,
) -> str:
    if item.role == "branch_summary":
        if show_tool_results and item.tool_result_text:
            return f"**Branch Summary**\n\n{item.tool_result_text}"
        return item.text
    if item.role == "compaction_summary":
        if show_tool_results and item.tool_result_text:
            return f"**Compaction Summary**\n\n{item.tool_result_text}"
        return item.text
    if item.role not in {"tool", "skill"}:
        return item.text
    text = invocation if item.role == "tool" and invocation else item.text
    if show_tool_results and item.tool_result_text:
        return f"{text}\n\n{item.tool_result_text}"
    if item.update_text and not item.tool_result_text:
        return f"{text}\n\n… {item.update_text}"
    return text


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
        code_block_background=theme.markdown_code_block_background,
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
            heading_style=_markdown_highlight_style(theme),
            inline_code_style=_markdown_inline_code_style(theme),
            link_style=theme.markdown_link,
            bullet_style=theme.markdown_bullet,
            table_border_style=theme.markdown_table_border,
            code_block_background=theme.markdown_code_block_background,
        )
    fenced_body = _render_fenced_body(
        text,
        body_style=body_style,
        syntax_theme=syntax_theme,
        code_block_background=theme.markdown_code_block_background,
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
    code_block_background: str,
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
            background_color=code_block_background,
        ),
    )


class ThemedCodeBlock(CodeBlock):
    """Rich Markdown code block with Tau's themed background color."""

    @classmethod
    def create(cls, markdown: Markdown, token: Any) -> ThemedCodeBlock:
        node_info = token.info or ""
        lexer_name = node_info.partition(" ")[0]
        code_block_background = getattr(markdown, "code_block_background", "default")
        return cls(lexer_name or "text", markdown.code_theme, code_block_background)

    def __init__(self, lexer_name: str, theme: str, code_block_background: str) -> None:
        super().__init__(lexer_name, theme)
        self.code_block_background = code_block_background

    def __rich_console__(self, console: Console, options: Any) -> Any:
        code = str(self.text).rstrip()
        yield Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            padding=1,
            background_color=self.code_block_background,
        )


class LeftAlignedMarkdownHeading(Heading):
    """Rich Markdown heading that keeps all heading levels left-aligned."""

    LEVEL_ALIGN: ClassVar[dict[str, Literal["default", "left", "center", "right", "full"]]] = {
        "h1": "left",
        "h2": "left",
        "h3": "left",
        "h4": "left",
        "h5": "left",
        "h6": "left",
    }


class ThemedMarkdown(Markdown):
    """Markdown renderer with Tau's softer heading/accent colors."""

    elements = {
        **Markdown.elements,
        "heading_open": LeftAlignedMarkdownHeading,
        "fence": ThemedCodeBlock,
        "code_block": ThemedCodeBlock,
    }

    def __init__(
        self,
        markup: str,
        *,
        heading_style: str,
        inline_code_style: str,
        link_style: str,
        bullet_style: str,
        table_border_style: str,
        code_block_background: str,
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
        self.inline_code_style = inline_code_style
        self.link_style = link_style
        self.bullet_style = bullet_style
        self.table_border_style = table_border_style
        self.code_block_background = code_block_background

    def __rich_console__(self, console: Console, options: Any) -> Any:
        with console.use_theme(
            _markdown_theme(
                self.heading_style,
                self.inline_code_style,
                self.link_style,
                self.bullet_style,
                self.table_border_style,
                self.code_block_background,
            )
        ):
            yield from super().__rich_console__(console, options)


def _markdown_highlight_style(theme: TuiTheme) -> str:
    return theme.markdown_heading


def _markdown_inline_code_style(theme: TuiTheme) -> str:
    return theme.markdown_inline_code


def _markdown_theme(
    heading_style: str,
    inline_code_style: str,
    link_style: str,
    bullet_style: str,
    table_border_style: str,
    code_block_background: str,
) -> Theme:
    highlight = Style.parse(heading_style)
    inline_code = Style.parse(inline_code_style)
    link = Style.parse(link_style)
    bullet = Style.parse(bullet_style)
    table_border = Style.parse(table_border_style)
    code_block = Style(bgcolor=code_block_background)
    return Theme(
        {
            "markdown.h1": highlight + Style(bold=True),
            "markdown.h2": highlight + Style(bold=True),
            "markdown.h3": highlight + Style(bold=True),
            "markdown.h4": highlight + Style(bold=True),
            "markdown.h5": highlight + Style(bold=True),
            "markdown.h6": highlight + Style(bold=True),
            "markdown.item.bullet": bullet,
            "markdown.item.number": bullet,
            "markdown.block_quote": highlight,
            "markdown.link": link,
            "markdown.link_url": link,
            "markdown.table.header": highlight + Style(bold=True),
            "markdown.table.border": table_border,
            "markdown.code": inline_code,
            "markdown.code_block": code_block,
        }
    )


def _render_fenced_body(
    text: str,
    *,
    body_style: str,
    syntax_theme: str,
    code_block_background: str,
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
                background_color=code_block_background,
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
    limit = session.context_window_tokens if threshold is None or threshold <= 0 else threshold
    return f"{_compact_token_count(session.context_token_estimate)}/{_compact_token_count(limit)}"


def _styled_cwd(cwd: Path, *, theme: TuiTheme) -> Text:
    """Style the parent path as metadata while emphasizing the working directory."""
    short_path = _short_path(cwd)
    parent, separator, name = short_path.rpartition("/")
    text = Text(overflow="fold", no_wrap=False)
    if separator and name:
        text.append(f"{parent}{separator}", style=theme.completion_description)
        text.append(name, style=theme.prompt_text)
    else:
        text.append(short_path, style=theme.prompt_text)
    text.append(f" ({_git_branch(cwd)})", style=theme.completion_description)
    return text


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
    except (OSError, ValueError):
        try:
            absolute_path = expanded_path.resolve()
        except OSError:
            absolute_path = expanded_path.absolute()
        return _short_path(absolute_path)


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
) -> RenderableType:
    """Render prompt completion suggestions in aligned command/description columns."""
    table = Table.grid(expand=True)
    table.add_column(no_wrap=True)
    table.add_column(ratio=1)

    previous_category: str | None = None
    for index, item in enumerate(state.items):
        if item.category != previous_category:
            if index:
                table.add_row(Text(""), Text(""))
            if item.category:
                table.add_row(Text(item.category, style=theme.completion_description), Text(""))
            previous_category = item.category

        selected = index == state.selected_index
        prefix = "› " if selected else "  "
        style = theme.completion_selected if selected else theme.prompt_text
        description_style = (
            theme.completion_selected_description if selected else theme.completion_description
        )
        command = Text(prefix, style=style)
        command.append(item.display, style=style)
        command.append("  ", style=style)
        table.add_row(command, Text(item.description or "", style=description_style))
    return table


@dataclass(frozen=True, slots=True)
class _LineLimitedCommaList:
    items: tuple[str, ...]
    empty: str
    style: str

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> RenderResult:
        if not self.items:
            yield Text(self.empty, style=self.style)
            return

        visible_count = 0
        for count in range(1, len(self.items) + 1):
            candidate = self._text(self.items[:count])
            if len(candidate.wrap(console, options.max_width)) > SIDEBAR_COMMA_LIST_MAX_LINES:
                break
            visible_count = count

        if visible_count:
            text = self._text(self.items[:visible_count])
        else:
            visible_count = 1
            text = self._truncate_to_line_budget(
                self._text(self.items[:visible_count]),
                console=console,
                width=options.max_width,
            )

        hidden_count = len(self.items) - visible_count
        if hidden_count:
            text.append("\n")
            text.append(f"...({hidden_count} more)", style=self.style)
        yield text

    def _truncate_to_line_budget(self, text: Text, *, console: Console, width: int) -> Text:
        wrapped_lines = list(text.wrap(console, width))
        visible_lines = [line.copy() for line in wrapped_lines[:SIDEBAR_COMMA_LIST_MAX_LINES]]
        if len(wrapped_lines) > SIDEBAR_COMMA_LIST_MAX_LINES:
            last_line = visible_lines[-1]
            last_line.truncate(max(0, width - 1), overflow="crop")
            last_line.append("…", style=self.style)
        return Text("\n").join(visible_lines)

    def _text(self, items: Sequence[str]) -> Text:
        return Text(
            ", ".join(items),
            style=self.style,
            overflow="fold",
            no_wrap=False,
        )


def _comma_list(
    items: Sequence[str],
    *,
    empty: str,
    theme: TuiTheme,
) -> RenderableType:
    return _LineLimitedCommaList(
        items=tuple(items),
        empty=empty,
        style=theme.completion_description,
    )


def _compact_usage_count(value: int) -> str:
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "m"


def _format_cost(value: float) -> str:
    if 0 < value < 0.01:
        return f"${value:.3f}"
    return f"${value:.2f}"


def _plural(count: int, singular: str) -> str:
    return singular if count == 1 else f"{singular}s"


def _limited_bullet_list(
    items: Sequence[str],
    *,
    empty: str,
    theme: TuiTheme,
) -> Text:
    text = _bullet_list(
        items[:SIDEBAR_BULLET_LIST_LIMIT],
        empty=empty,
        theme=theme,
    )
    hidden_count = len(items) - SIDEBAR_BULLET_LIST_LIMIT
    if hidden_count > 0:
        text.append(f"\n...({hidden_count} more)", style=theme.completion_description)
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
        text.append(item, style=theme.completion_description)
    return text


def _short_path(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)
