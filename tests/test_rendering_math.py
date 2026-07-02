import pytest

from tau_coding.rendering.math import render_terminal_math
from tau_coding.tui.config import TAU_DARK_THEME
from tau_coding.tui.state import ChatItem
from tau_coding.tui.widgets import StreamingTranscriptMessageWidget, TranscriptMessageWidget


def test_render_terminal_math_renders_inline_euler_identity() -> None:
    assert (
        render_terminal_math(r"Euler identity: $e^{i\pi} + 1 = 0$") == "Euler identity: eⁱπ + 1 = 0"
    )


def test_render_terminal_math_renders_inline_and_block_spans() -> None:
    text = r"Inline $x^2$ and block:" "\n" r"$$\sum_{i}" "\n" r"x_i$$"

    assert render_terminal_math(text) == "Inline x² and block:\n∑ᵢ\nxᵢ"


def test_render_terminal_math_preserves_currency_and_lone_dollars() -> None:
    assert render_terminal_math("It costs $5 and $10") == "It costs $5 and $10"
    assert render_terminal_math("A lone $ is not math") == "A lone $ is not math"


def test_render_terminal_math_preserves_unsupported_macros() -> None:
    assert render_terminal_math(r"Unsupported: $\widehat{x}$") == r"Unsupported: $\widehat{x}$"


def test_render_terminal_math_is_idempotent_and_handles_malformed_input() -> None:
    text = r"No math here, and malformed $e^{i\pi}"

    assert render_terminal_math(text) == text
    assert render_terminal_math(render_terminal_math(text)) == text


def test_transcript_message_widget_renders_finalized_math_markdown() -> None:
    item = ChatItem(role="assistant", text=r"Euler identity: $e^{i\pi} + 1 = 0$")
    widget = TranscriptMessageWidget(item, theme=TAU_DARK_THEME, show_tool_results=False)

    assert widget._markdown_text == "Euler identity: eⁱπ + 1 = 0"
    assert widget.selection_text == r"Euler identity: $e^{i\pi} + 1 = 0$"


@pytest.mark.anyio
async def test_streaming_transcript_message_renders_math_after_closing_delimiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[str] = []

    async def capture_update(
        self: StreamingTranscriptMessageWidget,
        markdown: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        del self, args, kwargs
        updates.append(markdown)

    monkeypatch.setattr(StreamingTranscriptMessageWidget, "update", capture_update)
    widget = StreamingTranscriptMessageWidget(
        ChatItem(role="assistant", text=""),
        theme=TAU_DARK_THEME,
    )

    await widget.append_fragment(r"$e^{i")
    await widget.append_fragment(r"\pi}$")

    assert updates == [r"$e^{i", "eⁱπ"]
    assert widget.selection_text == r"$e^{i\pi}$"


@pytest.mark.anyio
async def test_streaming_transcript_message_replace_text_renders_math(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[str] = []

    async def capture_update(
        self: StreamingTranscriptMessageWidget,
        markdown: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        del self, args, kwargs
        updates.append(markdown)

    monkeypatch.setattr(StreamingTranscriptMessageWidget, "update", capture_update)
    widget = StreamingTranscriptMessageWidget(
        ChatItem(role="assistant", text=r"$e^{i"),
        theme=TAU_DARK_THEME,
    )

    await widget.replace_text(r"$e^{i\pi}$")

    assert updates == ["eⁱπ"]
    assert widget.selection_text == r"$e^{i\pi}$"
