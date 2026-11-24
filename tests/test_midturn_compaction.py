"""Adversarial session-level tests for the mid-run request transform (issue #805).

Boundary compaction (`_try_auto_compact`) only fires before a prompt and
after a turn, so a single run of tool calls can grow past the auto-compaction
threshold unchecked. These tests drive `CodingSession._transform_request_context`
through real sessions and attack: mid-run firing, request-scoped durability,
best-effort failure, cancellation, and the never-split-a-tool-call invariant
of the composed payload.
"""

from pathlib import Path

import pytest

from pi_event_helpers import assistant_done, assistant_start, tool_call_end
from tau_agent import (
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    SimpleCancellationToken,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.messages import message_text
from tau_agent.session import JsonlSessionStorage
from tau_ai import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding.context_window import SUMMARIZATION_SYSTEM_PROMPT

SMALL_RESULT = "small output"
LARGE_RESULT = "tool output " * 8_000  # ~24k tokens, well past the keep window


def _dump_tool() -> AgentTool:
    async def execute(
        tool_call_id: str,
        arguments: dict,
        signal=None,  # noqa: ANN001
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        size = arguments.get("size")
        return AgentToolResult(content=LARGE_RESULT if size == "big" else SMALL_RESULT)

    return AgentTool(
        name="dump",
        label="Dump",
        description="Return a canned result sized by the 'size' argument.",
        parameters={
            "type": "object",
            "properties": {"size": {"type": "string", "enum": ["small", "big"]}},
        },
        execute_fn=execute,
    )


async def _drain(stream) -> list:  # noqa: ANN001
    return [event async for event in stream]


def _assert_no_dangling_tool_results(payload: list[AgentMessage]) -> None:
    """Every tool result in a payload must follow its call inside the payload."""
    pending: set[str] = set()
    for message in payload:
        if isinstance(message, AssistantMessage):
            pending.update(call.id for call in message.tool_calls)
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id is not None:
            assert tool_call_id in pending, "payload splits a tool call from its result"
            pending.discard(tool_call_id)


@pytest.mark.anyio
async def test_midturn_compaction_fires_inside_a_single_tool_run(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    call_1 = ToolCall(id="call-1", name="dump", arguments={"size": "small"})
    call_2 = ToolCall(id="call-2", name="dump", arguments={"size": "big"})
    small_assistant = AssistantMessage(content=[call_1], model="fake")
    large_assistant = AssistantMessage(content=[call_2], model="fake")
    final = AssistantMessage(content="done", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call_1), assistant_done(small_assistant)],
            [assistant_start(), tool_call_end(call_2), assistant_done(large_assistant)],
            [
                assistant_start(),
                assistant_done(
                    AssistantMessage(content="Generated automatic summary", model="fake")
                ),
            ],
            [assistant_start(), assistant_done(final)],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            tools=[_dump_tool()],
            auto_compact_token_threshold=200,
        )
    )
    await _drain(session.prompt("Run dump twice: small then big."))

    # Call order: run1, run2, transform summary generation, final request,
    # post-run boundary summary generation.
    assert len(provider.calls) == 5
    final_request = provider.calls[3][2]
    assert any(
        "Previous conversation summary" in (message.text or "")
        for message in final_request
        if hasattr(message, "text")
    )
    # The compacted prefix is gone; the kept window keeps its own tool
    # results (call b + its large result travel together by design).
    assert not any(
        "small output" in (message.text or "")
        for message in final_request
        if hasattr(message, "text")
    )
    assert any(
        "tool output" in (message.text or "")
        for message in final_request
        if hasattr(message, "text")
    )
    # The composed payload never splits a tool call from its result.
    _assert_no_dangling_tool_results(final_request)


@pytest.mark.anyio
async def test_midturn_compaction_is_best_effort_on_summary_failure(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    call_1 = ToolCall(id="call-1", name="dump", arguments={"size": "small"})
    call_2 = ToolCall(id="call-2", name="dump", arguments={"size": "big"})
    small_assistant = AssistantMessage(content=[call_1], model="fake")
    large_assistant = AssistantMessage(content=[call_2], model="fake")
    final = AssistantMessage(content="done", model="fake")

    class SummaryExplodes(FakeProvider):
        def stream_response(self, **kwargs):  # noqa: ANN003
            # Summarization requests carry the dedicated summarization system
            # prompt; explode on ALL of them (transform + boundary) so the
            # test proves the turn survives with zero durable compaction.
            if kwargs.get("system") == SUMMARIZATION_SYSTEM_PROMPT:
                raise RuntimeError("summarizer down")
            return super().stream_response(**kwargs)

    provider = SummaryExplodes(
        [
            [assistant_start(), tool_call_end(call_1), assistant_done(small_assistant)],
            [assistant_start(), tool_call_end(call_2), assistant_done(large_assistant)],
            [assistant_start(), assistant_done(final)],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            tools=[_dump_tool()],
            auto_compact_token_threshold=200,
        )
    )
    await _drain(session.prompt("Run dump twice: small then big."))

    entries = await storage.read_all()
    assert [entry for entry in entries if entry.type == "compaction"] == []
    # The turn survived with the original context: the final request still has
    # the raw tool result and the model answered.
    final_request = provider.calls[-1][2]
    assert any(
        "tool output" in (message.text or "")
        for message in final_request
        if hasattr(message, "text")
    )
    _assert_no_dangling_tool_results(final_request)
    assert message_text(session.state.messages[-1]) == "done"


@pytest.mark.anyio
async def test_transform_honours_cancellation(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider([])
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            auto_compact_token_threshold=1,
        )
    )
    messages: list[AgentMessage] = [
        UserMessage(content="a" * 4_000),
        UserMessage(content="b" * 4_000),
    ]
    token = SimpleCancellationToken()
    token.cancel()

    result = await session._transform_request_context(messages, token)
    assert result is messages


@pytest.mark.anyio
async def test_transform_is_noop_under_threshold(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider([])
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            auto_compact_token_threshold=100_000,
        )
    )
    messages: list[AgentMessage] = [UserMessage(content="tiny")]
    result = await session._transform_request_context(messages, None)
    assert result == messages


@pytest.mark.anyio
async def test_payload_plan_never_splits_a_tool_call_pair(tmp_path: Path) -> None:
    """The boundary walks back to the earliest dangling call, not past it."""
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider([])
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            auto_compact_token_threshold=1,
        )
    )
    call_a = ToolCall(id="a", name="dump", arguments={})
    call_b = ToolCall(id="b", name="dump", arguments={})
    messages: list[AgentMessage] = [
        UserMessage(content="prompt"),
        AssistantMessage(content=[call_a], model="fake"),
        ToolResultMessage(tool_call_id="a", tool_name="dump", content="result a"),
        AssistantMessage(content=[call_b], model="fake"),
        ToolResultMessage(tool_call_id="b", tool_name="dump", content="tool output " * 8_000),
    ]
    plan = session._plan_payload_compaction(messages)
    assert plan is not None
    summarized = messages[: plan.first_kept_index]
    kept = messages[plan.first_kept_index :]
    # Call b and its huge result stay together in the kept window.
    assert any(
        isinstance(message, AssistantMessage) and any(c.id == "b" for c in message.tool_calls)
        for message in kept
    )
    assert not any(getattr(message, "tool_call_id", None) == "b" for message in summarized)
    _assert_no_dangling_tool_results(list(kept))
