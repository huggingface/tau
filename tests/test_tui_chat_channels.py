"""Interleaved reasoning stays in one live thinking widget."""

import pytest

from tau_agent import AssistantMessage, TextContent, ThinkingContent
from tau_coding.tui.app import TauTuiApp
from tau_coding.tui.config import TAU_DARK_THEME
from tau_coding.tui.widgets import StreamingTranscriptMessageWidget, TranscriptView
from test_tui_app import FakeSession


@pytest.mark.anyio
async def test_text_delta_does_not_close_open_thinking_channel() -> None:
    app = TauTuiApp(FakeSession())
    async with app.run_test(size=(100, 30)):
        transcript = app.query_one("#transcript", TranscriptView)

        # Chat Completions keeps one stable content index per channel, so the
        # cumulative snapshot grows both blocks in place as fragments interleave.
        snapshots = [
            ([ThinkingContent(thinking="Done. C")], 0),
            ([ThinkingContent(thinking="Done. C"), TextContent(text="Done. Pr")], 1),
            (
                [
                    ThinkingContent(thinking="Done. Created and pushed."),
                    TextContent(text="Done. Pr"),
                ],
                0,
            ),
            (
                [
                    ThinkingContent(thinking="Done. Created and pushed."),
                    TextContent(text="Done. Private repo created."),
                ],
                1,
            ),
        ]
        for content, changed_index in snapshots:
            app.state.update_assistant(AssistantMessage(content=list(content)))
            await transcript.sync_active_assistant(
                app.state.active_assistant,
                changed_content_index=changed_index,
                theme=TAU_DARK_THEME,
                show_thinking=True,
            )

        widgets = list(transcript.query(StreamingTranscriptMessageWidget))
        assert len(widgets) == 2
        assert [widget.item.role for widget in widgets] == ["thinking", "assistant"]
        assert [widget.selection_text for widget in widgets] == [
            "Done. Created and pushed.",
            "Done. Private repo created.",
        ]

        final = AssistantMessage(
            content=[
                ThinkingContent(thinking="Done. Created and pushed."),
                TextContent(text="Done. Private repo created."),
            ]
        )
        app.state.finish_assistant(final)
        await transcript.finish_active_assistant(
            app.state.items[-2:],
            theme=TAU_DARK_THEME,
            show_thinking=True,
        )
        assert transcript._active_render is None
        assert [widget.item.text for widget in widgets] == [
            "Done. Created and pushed.",
            "Done. Private repo created.",
        ]
