from tau_agent import (
    AssistantMessage,
    LeafEntry,
    MessageEntry,
    TextContent,
    ToolCall,
    Usage,
    UserMessage,
)
from tau_coding.session_analysis import analyze_session


def test_analyze_session_reports_usage_for_active_branch_only() -> None:
    root = MessageEntry(id="root", message=UserMessage(content="Start"))
    kept = MessageEntry(
        id="kept",
        parent_id=root.id,
        message=AssistantMessage(
            provider="openai",
            model="gpt-test",
            content=[
                TextContent(text="Done"),
                ToolCall(id="call-1", name="read", arguments={"path": "README.md"}),
            ],
            usage=Usage(input=100, cache_read=300, cache_write=125, output=20, reasoning=5),
        ),
    )
    abandoned = MessageEntry(
        id="abandoned",
        parent_id=root.id,
        message=AssistantMessage(
            provider="openai",
            model="gpt-test",
            usage=Usage(input=9_000, output=1),
        ),
    )
    entries = [root, kept, abandoned, LeafEntry(id="leaf", entry_id=kept.id)]

    analysis = analyze_session(
        entries,
        pricing=lambda _provider, _model, _input: {
            "input": 1.0,
            "output": 2.0,
            "cacheRead": 0.5,
            "cacheWrite": 0.25,
        },
    )

    assert len(analysis.requests) == 1
    assert analysis.requests[0].prompt == 525
    assert analysis.requests[0].hit_rate == 300 / 525
    assert analysis.requests[0].reasoning == 5
    assert analysis.total_output == 20
    assert analysis.estimated_cost == 0.00032125
    assert analysis.tool_counts == (("read", 1),)
