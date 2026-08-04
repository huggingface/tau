from tau_agent.messages import (
    AssistantMessage,
    CustomMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from tau_agent.session import CompactionEntry, MessageEntry
from tau_coding.session_stats import SessionStats, calculate_session_stats


def test_cache_hit_rate_is_hidden_when_no_provider_reported_cache_usage() -> None:
    """Backends without prompt caching must not show a permanent 0%."""
    stats = SessionStats(input_tokens=5_000, output_tokens=100)

    assert stats.cache_hit_rate is None


def test_cache_hit_rate_is_zero_when_a_write_happened_but_nothing_was_read() -> None:
    """A cold first turn genuinely is 0% cached, and saying so is useful."""
    stats = SessionStats(input_tokens=5_000, cache_write_tokens=4_000)

    assert stats.cache_hit_rate == 0.0


def test_cache_hit_rate_is_none_without_billed_input() -> None:
    assert SessionStats().cache_hit_rate is None


def test_cache_hit_rate_divides_reads_by_total_prompt_tokens() -> None:
    stats = SessionStats(input_tokens=1_000, cached_input_tokens=950, cache_write_tokens=50)

    assert stats.cache_hit_rate == 0.95


def test_latest_cache_hit_rate_uses_latest_request_tokens() -> None:
    stats = SessionStats(
        input_tokens=2_000,
        cached_input_tokens=1_400,
        latest_prompt_tokens=1_000,
        latest_cached_input_tokens=950,
    )

    assert stats.latest_cache_hit_rate == 0.95


def test_latest_cache_hit_rate_is_hidden_without_reported_cache_activity() -> None:
    stats = SessionStats(
        input_tokens=1_000,
        latest_prompt_tokens=1_000,
    )

    assert stats.latest_cache_hit_rate is None


def test_calculate_session_stats_uses_latest_tool_continuation_cache_rate() -> None:
    user = MessageEntry(message=UserMessage(content="Inspect it"))
    tool_request = MessageEntry(
        parent_id=user.id,
        message=AssistantMessage(
            provider="anthropic",
            model="claude-test",
            content=[ToolCall(id="call-1", name="read", arguments={})],
            usage=Usage(input=100, cache_write=100),
        ),
    )
    tool_result = MessageEntry(
        parent_id=tool_request.id,
        message=ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read",
            content="result",
        ),
    )
    continuation = MessageEntry(
        parent_id=tool_result.id,
        message=AssistantMessage(
            provider="anthropic",
            model="claude-test",
            usage=Usage(input=10, cache_read=190),
        ),
    )

    stats = calculate_session_stats(
        [user, tool_request, tool_result, continuation],
        pricing=lambda _provider, _model, _input: {},
    )

    assert stats.cache_hit_rate == 0.475
    assert stats.latest_cache_hit_rate == 0.95


def test_latest_cache_hit_rate_reports_miss_after_earlier_cache_activity() -> None:
    first = MessageEntry(
        message=AssistantMessage(
            provider="anthropic",
            model="claude-test",
            usage=Usage(input=100, cache_write=100),
        )
    )
    latest = MessageEntry(
        parent_id=first.id,
        message=AssistantMessage(
            provider="anthropic",
            model="claude-test",
            usage=Usage(input=200),
        ),
    )

    stats = calculate_session_stats(
        [first, latest],
        pricing=lambda _provider, _model, _input: {},
    )

    assert stats.latest_cache_hit_rate == 0.0


def test_calculate_session_stats_keeps_compacted_active_branch_usage() -> None:
    user = MessageEntry(message=UserMessage(content="Fix it"))
    assistant = MessageEntry(
        parent_id=user.id,
        message=AssistantMessage(
            provider="openai",
            model="gpt-test",
            content=[
                TextContent(text="Working"),
                ToolCall(id="call-1", name="read", arguments={}),
                ToolCall(id="call-2", name="edit", arguments={}),
            ],
            usage=Usage(input=1_000_000, output=100_000, cache_read=500_000),
        ),
    )
    extension_turn = MessageEntry(
        parent_id=assistant.id,
        message=CustomMessage(custom_type="test:status", content="Continue"),
    )
    compaction = CompactionEntry(
        parent_id=extension_turn.id,
        summary="Earlier work",
        replaces_entry_ids=[user.id, assistant.id],
    )

    stats = calculate_session_stats(
        [user, assistant, extension_turn, compaction],
        pricing=lambda provider, model, input_tokens: {
            "input": 2.0,
            "output": 8.0,
            "cacheRead": 0.5,
            "cacheWrite": 0.0,
        },
    )

    assert stats.turn_count == 2
    assert stats.tool_call_count == 2
    assert stats.input_tokens == 1_500_000
    assert stats.output_tokens == 100_000
    assert stats.estimated_cost == 3.05


def test_calculate_session_stats_marks_cost_unavailable_when_pricing_is_missing() -> None:
    entry = MessageEntry(
        message=AssistantMessage(
            provider="custom",
            model="unknown",
            usage=Usage(input=100, output=20),
        )
    )

    stats = calculate_session_stats([entry], pricing=lambda _provider, _model, _input: None)

    assert stats.input_tokens == 100
    assert stats.output_tokens == 20
    assert stats.estimated_cost is None
