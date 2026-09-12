"""Interleaved reasoning stays in one live thinking widget."""

import pytest

from tau_coding.tui.app import TauTuiApp
from tau_coding.tui.widgets import StreamingTranscriptMessageWidget, TranscriptView
from test_tui_app import FakeSession


@pytest.mark.anyio
async def test_text_delta_does_not_close_open_thinking_channel() -> None:
    app = TauTuiApp(FakeSession())
    async with app.run_test(size=(100, 30)):
        transcript = app.query_one("#transcript", TranscriptView)
        await transcript.append_thinking_delta("Done. C", show_thinking=True)
        await transcript.append_assistant_delta("Done. Pr")
        await transcript.append_thinking_delta("reated and pushed.", show_thinking=True)
        await transcript.append_assistant_delta("ivate repo created.")
        widgets = list(transcript.query(StreamingTranscriptMessageWidget))
        assert len(widgets) == 2
        assert [widget.item.text for widget in widgets] == [
            "Done. Created and pushed.",
            "Done. Private repo created.",
        ]
        await transcript.finish_thinking_message()
        assert transcript._active_thinking_widget is None
