from pathlib import Path

from tau_agent import (
    AssistantMessage,
    CompactionEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionInfoEntry,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.messages import assistant_content
from tau_coding.session_export import export_session_html, render_session_html


def test_render_session_html_preserves_branch_tree() -> None:
    entries = [
        MessageEntry(id="root", message=UserMessage(content="Start <session>")),
        MessageEntry(
            id="left",
            parent_id="root",
            message=AssistantMessage(content="Left branch"),
        ),
        MessageEntry(
            id="right",
            parent_id="root",
            message=AssistantMessage(
                content=assistant_content(
                    "Right branch",
                    [ToolCall(id="call-1", name="read", arguments={"path": "README.md"})],
                )
            ),
        ),
        MessageEntry(
            id="tool",
            parent_id="right",
            message=ToolResultMessage(
                tool_call_id="call-1",
                tool_name="read",
                content=[TextContent(text="File contents")],
                details={"bytes": 13},
            ),
        ),
        CompactionEntry(
            id="compact",
            parent_id="tool",
            summary="The right branch was compacted.",
            replaces_entry_ids=["root", "right", "tool"],
        ),
        LeafEntry(id="leaf", parent_id="compact", entry_id="compact"),
    ]

    html = render_session_html(entries, title="Test Export", source="/tmp/session.jsonl")

    assert "<title>Test Export</title>" in html
    assert "Source: <code>/tmp/session.jsonl</code>" in html
    assert 'id="entry-root"' in html
    assert 'id="entry-left"' in html
    assert 'id="entry-right"' in html
    assert 'id="entry-compact"' in html
    assert "Start &lt;session&gt;" in html
    assert "Right branch" in html
    assert "[read]" in html
    assert "active-path" in html
    assert "active-leaf" in html
    assert "Replaces entries" in html


def test_render_session_html_uses_static_document_layout() -> None:
    entries = [MessageEntry(id="root", message=UserMessage(content="Export layout"))]

    html = render_session_html(entries, title="Layout Export")

    assert '<p class="eyebrow">Tau session export</p>' in html
    assert '<main class="session-shell">' in html
    assert '<aside class="tree-rail">' in html
    assert '<section class="entry-stream" aria-label="Session entries">' in html
    assert 'class="entry-card active-entry"' in html
    assert "Session" in html
    assert "Transcript" in html
    assert "border-right: 1px solid var(--line);" in html
    assert 'id="themeToggle"' in html
    assert "<link" not in html.lower()
    assert "http://" not in html and "https://" not in html


def test_render_session_html_syntax_highlights_tool_call_arguments() -> None:
    entries = [
        MessageEntry(
            id="root",
            message=AssistantMessage(
                content=assistant_content(
                    "Reading a file",
                    [ToolCall(id="call-1", name="read", arguments={"path": "README.md"})],
                )
            ),
        ),
    ]

    html = render_session_html(entries, title="Highlight Export")

    assert 'class="highlight"' in html
    assert '<span class="nt">' in html or '<span class="s2">' in html


def test_render_session_html_includes_filter_controls_and_tool_accordions() -> None:
    entries = [
        SessionInfoEntry(id="info", title="Filtered export", cwd="/tmp"),
        MessageEntry(
            id="tool-call",
            parent_id="info",
            message=AssistantMessage(
                content=assistant_content(
                    "Reading a file",
                    [ToolCall(id="call-1", name="read", arguments={"path": "README.md"})],
                )
            ),
        ),
        MessageEntry(
            id="tool-result",
            parent_id="tool-call",
            message=ToolResultMessage(
                tool_call_id="call-1",
                tool_name="read",
                content=[TextContent(text="File contents")],
            ),
        ),
        ModelChangeEntry(id="model", parent_id="tool-result", model="example/model"),
    ]

    html = render_session_html(entries, title="Filter Export")

    assert 'id="showTools"' in html
    assert 'id="showEvents"' in html
    assert 'id="toolDetailsToggle"' in html
    assert 'details class="tool-details tool-content" open' in html
    assert 'details class="tool-details" open' in html
    assert 'id="entry-tool-result" class="entry-card active-entry" data-entry-kind="tool"' in html
    assert 'id="entry-model" class="entry-card active-entry" data-entry-kind="event"' in html
    assert 'data-entry-kind="tool"' in html
    assert 'data-entry-kind="event"' in html
    assert 'root.classList.toggle("hide-tools"' in html
    assert 'root.classList.toggle("messages-only"' in html
    assert 'querySelectorAll("details.tool-details")' in html


def test_render_session_html_marks_tool_only_assistant_messages_as_tools() -> None:
    entries = [
        MessageEntry(
            id="tool-only",
            message=AssistantMessage(
                content=[ToolCall(id="call-1", name="read", arguments={"path": "README.md"})]
            ),
        )
    ]

    html = render_session_html(entries)

    assert 'id="entry-tool-only" class="entry-card active-entry" data-entry-kind="tool"' in html


def test_render_session_html_includes_theme_toggle_script() -> None:
    entries = [MessageEntry(id="root", message=UserMessage(content="Hello"))]

    html = render_session_html(entries, title="Toggle Export")

    assert 'id="themeToggle"' in html
    assert "localStorage" in html
    assert "data-theme" in html


def test_export_session_html_writes_file(tmp_path: Path) -> None:
    entries = [MessageEntry(id="root", message=UserMessage(content="Hello"))]
    output_path = tmp_path / "session.html"

    result = export_session_html(entries, output_path, title="Session")

    assert result == output_path
    assert output_path.read_text(encoding="utf-8").startswith("<!doctype html>")
