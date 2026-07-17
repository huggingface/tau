import json

import pytest

from tau_agent import (
    AgentToolResult,
    AssistantMessage,
    CustomMessage,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    TextContent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tau_agent.provider_events import TextDeltaEvent, ThinkingDeltaEvent
from tau_coding.events import AutoRetryStartEvent, QueueUpdateEvent
from tau_coding.rendering import FinalTextRenderer, JsonEventRenderer, TranscriptRenderer


def _assistant_update(event) -> MessageUpdateEvent:  # noqa: ANN001
    return MessageUpdateEvent(message=event.partial, assistant_message_event=event)


def _error_end(message: str) -> MessageEndEvent:
    return MessageEndEvent(message=AssistantMessage(stop_reason="error", error_message=message))


def test_transcript_renderer_streams_text_and_tool_events(
    capsys: pytest.CaptureFixture[str],
) -> None:
    renderer = TranscriptRenderer()
    partial = AssistantMessage(content="")

    renderer.render(MessageStartEvent(message=partial))
    renderer.render(
        _assistant_update(
            ThinkingDeltaEvent(content_index=0, delta="hidden reasoning", partial=partial)
        )
    )
    renderer.render(
        _assistant_update(TextDeltaEvent(content_index=0, delta="Hel", partial=partial))
    )
    renderer.render(_assistant_update(TextDeltaEvent(content_index=0, delta="lo", partial=partial)))
    renderer.render(
        AutoRetryStartEvent(
            attempt=2,
            max_attempts=3,
            delay_ms=0,
            error_message="Retrying provider request 2/3 after HTTP 503.",
        )
    )
    renderer.render(
        ToolExecutionStartEvent(tool_call_id="call-1", tool_name="read", args={"path": "a.py"})
    )
    renderer.render(
        ToolExecutionUpdateEvent(
            tool_call_id="call-1",
            tool_name="read",
            args={"path": "a.py"},
            partial_result=AgentToolResult(content="reading"),
        )
    )
    renderer.render(
        ToolExecutionEndEvent(
            tool_call_id="call-1",
            tool_name="read",
            result=AgentToolResult(content="done"),
            is_error=False,
        )
    )

    captured = capsys.readouterr()
    assert renderer.finish() is True
    assert captured.out == "Hello\n"
    assert "hidden reasoning" not in captured.out
    assert "hidden reasoning" not in captured.err
    assert "… Retrying provider request 2/3 after HTTP 503." in captured.err
    assert "→ read a.py" in captured.err
    assert "… reading" in captured.err
    assert "✓ read" in captured.err
    assert "done" in captured.err


def test_transcript_renderer_uses_custom_message_renderer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def render(custom_type: str, content: str, details: object, expanded: bool) -> str | None:
        assert custom_type == "subagent-notification"
        assert expanded is False
        return "[bold]✓ research done[/bold]"

    renderer = TranscriptRenderer(custom_message_renderer=render)
    renderer.render(
        MessageEndEvent(
            message=CustomMessage(
                custom_type="subagent-notification",
                content="<task-notification>raw xml</task-notification>",
                details={"id": "run-1"},
            )
        )
    )

    captured = capsys.readouterr()
    assert "✓ research done" in captured.err
    assert "raw xml" not in captured.err


def test_transcript_renderer_fails_on_assistant_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    renderer = TranscriptRenderer()

    renderer.render(_error_end("provider failed"))

    captured = capsys.readouterr()
    assert renderer.finish() is False
    assert "Error: provider failed" in captured.err


def test_final_text_renderer_prints_only_final_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    renderer = FinalTextRenderer()
    partial = AssistantMessage(content="ignored")

    renderer.render(
        _assistant_update(TextDeltaEvent(content_index=0, delta="ignored", partial=partial))
    )
    assert renderer.finish() is True
    assert capsys.readouterr().out == ""

    renderer.render(MessageEndEvent(message=AssistantMessage(content="Final answer")))
    assert renderer.finish() is True
    assert capsys.readouterr().out == "Final answer\n"


def test_final_text_renderer_prints_errors_on_finish(capsys: pytest.CaptureFixture[str]) -> None:
    renderer = FinalTextRenderer()

    renderer.render(_error_end("provider failed"))
    assert capsys.readouterr().err == ""
    assert renderer.finish() is False
    assert "Error: provider failed" in capsys.readouterr().err


def test_json_renderer_emits_canonical_jsonl(capsys: pytest.CaptureFixture[str]) -> None:
    renderer = JsonEventRenderer()
    partial = AssistantMessage(content=[TextContent(text="hidden reasoning")])

    renderer.render(MessageStartEvent(message=AssistantMessage()))
    renderer.render(QueueUpdateEvent(steering=("adjust",), follow_up=("after",)))
    renderer.render(
        _assistant_update(
            ThinkingDeltaEvent(
                content_index=0,
                delta="hidden reasoning",
                partial=partial,
            )
        )
    )
    renderer.render(_error_end("provider failed"))

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["type"] == "message_start"
    assert lines[1] == {
        "type": "queue_update",
        "steering": ["adjust"],
        "followUp": ["after"],
    }
    assert lines[2]["type"] == "message_update"
    assert lines[2]["assistantMessageEvent"]["type"] == "thinking_delta"
    assert lines[3]["message"]["stopReason"] == "error"
    assert renderer.finish() is False
