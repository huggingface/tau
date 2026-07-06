"""Small Textual widgets for Tau's interactive TUI."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import TimeoutExpired, run
from typing import Any, ClassVar, Literal, Protocol

from pygments.lexers import get_lexer_by_name  # type: ignore[import-untyped]
from pygments.util import ClassNotFound  # type: ignore[import-untyped]
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.markdown import CodeBlock, Heading, Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from textual.containers import Horizontal, VerticalScroll
from textual.content import Style as TextualStyle  # type: ignore[attr-defined]
from textual.events import Resize
from textual.geometry import Offset
from textual.selection import Selection
from textual.widget import Widget
from textual.widgets import Markdown as TextualMarkdown
from textual.widgets import Static
from textual.widgets.markdown import MarkdownBlock, MarkdownStream

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


class TauMarkdownBlock(MarkdownBlock):
    """Markdown block that applies Tau's themed inline link color."""

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
    ) -> None:
        self.item = item
        self.selection_text = transcript_item_selection_text(
            item,
            show_tool_results=show_tool_results,
        )
        self._markdown_text = _transcript_item_markdown(
            item,
            show_tool_results=show_tool_results,
        )
        self._theme = theme
        self._role_style = _chat_item_role_style(item, theme)
        super().__init__(classes="transcript-message")
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
        if _use_plain_transcript_body(self.item):
            body = Static(
                _transcript_plain_body_text(
                    self.item,
                    text=self.selection_text,
                    body_style=self._role_style.body,
                    theme=self._theme,
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
        self._last_render_width = 0
        self._active_assistant_widget: StreamingTranscriptMessageWidget | None = None
        self._active_thinking_widget: StreamingTranscriptMessageWidget | None = None
        self._hidden_thinking_placeholder_visible = False
        self._follow_output = True
        self._follow_scroll_pending = False

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
        """Track whether user scrollback has opted out of transcript following."""
        super().watch_scroll_y(old_value, new_value)
        if new_value < old_value:
            self._follow_output = False
        elif new_value >= self.max_scroll_y:
            self._follow_output = True

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
        """Redraw the transcript from display state."""
        self._render_state = state
        self._render_theme = theme
        self._redraw(scroll_end=self._should_follow_output)

    def update_thinking_visibility(
        self,
        state: TuiState,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Update only thinking-token widgets after visibility changes."""
        self._render_state = state
        self._render_theme = theme
        should_follow = self._should_follow_output
        previous_scroll_y = self.scroll_y

        message_children = [
            child
            for child in self.children
            if isinstance(child, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
        ]
        thinking_children = [child for child in message_children if child.item.role == "thinking"]
        if thinking_children:
            self.remove_children(thinking_children)

        non_thinking_children = [
            child for child in message_children if child.item.role != "thinking"
        ]
        non_thinking_index = 0
        pending_thinking: list[TranscriptMessageWidget] = []
        hidden_thinking_placeholder = False

        def flush_pending(
            *, before: TranscriptMessageWidget | StreamingTranscriptMessageWidget | None
        ) -> None:
            nonlocal pending_thinking
            for widget in pending_thinking:
                self.mount(widget, before=before)
            pending_thinking = []

        for item in state.items:
            if item.role == "thinking":
                if state.show_thinking:
                    pending_thinking.append(
                        TranscriptMessageWidget(
                            item,
                            theme=theme,
                            show_tool_results=state.show_tool_results,
                        )
                    )
                elif not hidden_thinking_placeholder:
                    pending_thinking.append(
                        TranscriptMessageWidget(
                            ChatItem(role="thinking", text=_HIDDEN_THINKING_PLACEHOLDER),
                            theme=theme,
                            show_tool_results=state.show_tool_results,
                        )
                    )
                    hidden_thinking_placeholder = True
                continue

            hidden_thinking_placeholder = False
            target = None
            while non_thinking_index < len(non_thinking_children):
                candidate = non_thinking_children[non_thinking_index]
                non_thinking_index += 1
                if candidate.item is item:
                    target = candidate
                    break
            if target is not None:
                flush_pending(before=target)

        tail_child = (
            non_thinking_children[non_thinking_index]
            if non_thinking_index < len(non_thinking_children)
            else None
        )
        flush_pending(before=tail_child)
        self._active_thinking_widget = None
        self._hidden_thinking_placeholder_visible = (
            _last_transcript_child_is_hidden_thinking_placeholder(self.children)
        )
        self._last_render_width = self.scrollable_content_region.width
        self.refresh(layout=True)
        if should_follow:
            self._request_follow_scroll()
        else:
            self.call_after_refresh(
                lambda: self.scroll_to(y=previous_scroll_y, animate=False, immediate=True)
            )

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
                                text=_HIDDEN_THINKING_PLACEHOLDER,
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
            self._request_follow_scroll()

    async def append_item(
        self,
        item: ChatItem,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_tool_results: bool = False,
        scroll_end: bool = False,
    ) -> TranscriptMessageWidget | StreamingTranscriptMessageWidget:
        """Append one transcript item without rebuilding previous blocks."""
        should_follow = self._should_follow_output if not scroll_end else True
        await self._finalize_active_assistant_message()
        await self._finalize_active_thinking_message()
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
        if should_follow:
            self._request_follow_scroll(force=scroll_end)
        return widget

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
        await self.mount(widget)
        self._active_assistant_widget = widget
        self._last_render_width = self.scrollable_content_region.width
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
        """Append streamed assistant text to the active message widget."""
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
            await self.mount(widget, before=self._active_assistant_widget)
            self._active_thinking_widget = None
            self._hidden_thinking_placeholder_visible = True
            self._last_render_width = self.scrollable_content_region.width
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
                before=self._active_assistant_widget,
            )
        await self._active_thinking_widget.append_fragment(delta)
        if should_follow:
            self._request_follow_scroll(force=scroll_end)

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
        await widget.finalize(text)
        self._active_assistant_widget = None
        self._hidden_thinking_placeholder_visible = False

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


def _split_rich_style_colors(style: str) -> tuple[str | None, str | None]:
    """Split the foreground/background colors from a simple Rich style string."""
    text_style = Style.parse(style)
    foreground = text_style.color.name if text_style.color is not None else None
    background = text_style.bgcolor.name if text_style.bgcolor is not None else None
    return foreground, background


def _use_plain_transcript_body(item: ChatItem) -> bool:
    """Return whether a transcript item can use fast selectable plain text."""
    return item.role in {"user", "tool", "skill", "error"}


def _transcript_plain_body_text(
    item: ChatItem,
    *,
    text: str,
    body_style: str,
    theme: TuiTheme,
) -> RenderableType:
    """Return styled transcript text for selectable plain rows."""
    if item.role != "tool":
        return Text(text, style=body_style, overflow="fold", no_wrap=False)

    invocation, separator, result_text = text.partition("\n\n")
    invocation_text = _render_transcript_tool_invocation(
        invocation,
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
) -> str:
    """Return Markdown for a transcript item using native Textual Markdown blocks."""
    visible_text = _visible_chat_text(item, show_tool_results=show_tool_results)
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
    except (OSError, ValueError):
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
