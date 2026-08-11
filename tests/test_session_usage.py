from tau_agent import AssistantMessage, CompactionEntry, MessageEntry, ToolCall, UserMessage
from tau_agent.messages import Usage, UsageCost, assistant_content
from tau_coding.session_usage import collect_session_usage, render_usage_dashboard


def _assistant(
    entry_id: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-5",
    usage: Usage | None = None,
    tools: list[ToolCall] | None = None,
) -> MessageEntry:
    return MessageEntry(
        id=entry_id,
        message=AssistantMessage(
            content=assistant_content("ok", tools or []),
            provider=provider,
            model=model,
            usage=usage or Usage(),
        ),
    )


def test_collect_session_usage_aggregates_requests_tools_and_compactions() -> None:
    entries = [
        MessageEntry(id="user", message=UserMessage(content="hi")),
        _assistant(
            "a1",
            usage=Usage(input=100, output=20, cache_read=900, cache_write=50),
            tools=[ToolCall(id="c1", name="read", arguments={})],
        ),
        CompactionEntry(id="compact", summary="summary", replaces_entry_ids=["user"]),
        _assistant(
            "a2",
            usage=Usage(input=50, output=10, cache_read=1950, cache_write=0, reasoning=5),
            tools=[ToolCall(id="c2", name="read", arguments={})],
        ),
    ]

    usage = collect_session_usage(entries)

    assert [item.number for item in usage.requests] == [1, 2]
    assert usage.total_prompt == 3050
    assert usage.total_cached == 2850
    assert usage.total_output == 30
    assert usage.requests[1].reasoning == 5
    assert usage.tool_calls == (("read", 2),)
    assert usage.compactions == 1
    assert usage.hit_rate == 2850 / 3050


def test_collect_session_usage_estimates_cost_from_catalog() -> None:
    known = collect_session_usage([_assistant("a1", usage=Usage(input=1_000_000, output=0))])
    unknown = collect_session_usage(
        [_assistant("a1", provider="mystery", model="x", usage=Usage(input=1_000_000))]
    )

    assert known.total_cost is not None and known.total_cost > 0
    assert unknown.total_cost is None
    assert unknown.requests[0].estimated_cost is None


def test_collect_session_usage_falls_back_to_reported_cost_and_keeps_partial_total() -> None:
    usage = collect_session_usage(
        [
            _assistant(
                "a1",
                provider="mystery",
                model="priced-by-provider",
                usage=Usage(input=10, cost=UsageCost(total=0.42)),
            ),
            _assistant("a2", provider="mystery", model="unpriced", usage=Usage(input=10)),
        ]
    )

    assert usage.requests[0].estimated_cost == 0.42
    assert usage.requests[1].estimated_cost is None
    assert usage.total_cost == 0.42


def test_render_usage_dashboard_renders_charts_and_table() -> None:
    usage = collect_session_usage(
        [_assistant("a1", usage=Usage(input=10, output=5, cache_read=90, cache_write=0))]
    )

    markup = render_usage_dashboard(usage)

    assert markup.count('class="usage-chart"') == 3
    assert markup.count('class="png-button"') == 3
    assert "Prompt input by request" in markup
    assert "Cache hit rate" in markup
    assert "claude-sonnet-4-5" in markup


def test_render_usage_dashboard_without_cache_activity_shows_na() -> None:
    usage = collect_session_usage([_assistant("a1", usage=Usage(input=10, output=5))])

    markup = render_usage_dashboard(usage)

    assert usage.hit_rate is None
    assert "Cache hit rate</span><strong>N/A</strong>" in markup
    assert markup.count('class="usage-chart"') == 2
    assert 'data-labels="0.0%"' not in markup


def test_render_usage_dashboard_keeps_large_sessions_compact() -> None:
    usage = collect_session_usage(
        [_assistant(f"a{index}", usage=Usage(input=index, output=1)) for index in range(1, 400)]
    )

    markup = render_usage_dashboard(usage)

    assert 'class="point"' not in markup
    assert markup.count('class="hover-point"') == 5
    assert len(markup.encode()) < 200_000


def test_render_usage_dashboard_without_requests() -> None:
    markup = render_usage_dashboard(collect_session_usage([]))

    assert "No assistant responses" in markup
