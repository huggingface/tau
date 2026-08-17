import base64
import re
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
from tau_agent.messages import Usage, assistant_content
from tau_coding.session_export import export_session_html, render_session_html
from tau_coding.tui.themes import TAU_DARK_THEME, TAU_LIGHT_THEME


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
    assert "active-path" in html
    assert "active-leaf" in html
    assert "Replaces entries" in html


def test_render_session_html_uses_static_document_layout() -> None:
    entries = [MessageEntry(id="root", message=UserMessage(content="Export layout"))]

    html = render_session_html(entries, title="Layout Export")

    assert '<p class="eyebrow">Tau session export</p>' in html
    assert '<main class="session-shell"' in html
    assert '<aside class="tree-rail">' in html
    assert '<section class="entry-stream" aria-label="Session entries">' in html
    assert 'class="entry active-entry"' in html
    assert "Session" in html
    assert "Transcript" in html
    assert "border-right: 1px solid var(--line);" in html
    assert 'id="themeToggle"' in html
    assert "<link" not in html.lower()
    assert "http://" not in html and "https://" not in html


def test_render_session_html_includes_escaped_readable_system_prompt() -> None:
    entries = [MessageEntry(id="root", message=UserMessage(content="Hello"))]
    system_prompt = "First line\n  indented <script>alert('x')</script>\n" + "long-word-" * 40

    html = render_session_html(entries, system_prompt=system_prompt)

    assert '<details class="system-prompt">' in html
    assert "System Prompt" in html
    assert "May include project instructions" in html
    assert "First line\n  indented &lt;script&gt;alert('x')&lt;/script&gt;\n" in html
    assert "<script>alert('x')</script>" not in html
    assert "white-space: pre-wrap" in html
    assert "overflow-wrap: anywhere" in html
    assert html.index('class="system-prompt"') < html.index('class="session-shell"')


def test_render_session_html_omits_unavailable_system_prompt() -> None:
    html = render_session_html([])

    assert '<details class="system-prompt">' not in html
    assert "May include project instructions" not in html


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


def test_render_session_html_includes_filter_bar_and_accordions() -> None:
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

    # Chip-style filters with entry counts.
    assert 'id="showTools"' in html
    assert 'id="showEvents"' in html
    assert 'class="filter-bar"' in html
    assert 'Tools <span class="chip-count">1</span>' in html
    assert 'Events <span class="chip-count">2</span>' in html
    # One button expands/compacts every accordion.
    assert 'id="accordionToggle"' in html
    assert "Expand all" in html
    assert 'querySelectorAll(".entry-stream details")' in html
    # Entries and tool calls render as closed accordions.
    assert 'id="entry-tool-result" class="entry active-entry" data-entry-kind="tool"' in html
    assert 'id="entry-model" class="entry active-entry" data-entry-kind="event"' in html
    assert '<details class="block tool-call tool-content">' in html
    assert 'root.classList.toggle("hide-tools"' in html
    assert 'root.classList.toggle("messages-only"' in html
    # Tool result rows use a short title with the tool name.
    assert '<span class="entry-title">03 · Tool: read</span>' in html
    # The sidebar shows only the tool name for tool entries.
    assert '<span class="node-type">read</span>' in html
    # The tool icon is a claw hammer.
    assert "m15 12-8.373" in html


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

    assert 'id="entry-tool-only" class="entry active-entry" data-entry-kind="tool"' in html
    assert '<span class="entry-preview">read</span>' in html
    assert '<span class="node-type">read</span>' in html


def test_render_session_html_marks_error_tool_results() -> None:
    entries = [
        MessageEntry(
            id="failure",
            message=ToolResultMessage(
                tool_call_id="call-1",
                tool_name="bash",
                content=[TextContent(text="command failed")],
                is_error=True,
            ),
        )
    ]

    html = render_session_html(entries)

    assert 'id="entry-failure" class="entry active-entry is-error"' in html
    assert '<span class="error-flag">error</span>' in html


def test_render_session_html_includes_jsonl_download() -> None:
    entries = [
        MessageEntry(id="root", message=UserMessage(content="Hello")),
        MessageEntry(id="reply", parent_id="root", message=AssistantMessage(content="Hi")),
        LeafEntry(id="leaf", parent_id="reply", entry_id="reply"),
    ]

    html = render_session_html(
        entries,
        title="Download Export",
        source="/home/user/.tau/sessions/abc123.jsonl",
        system_prompt="Private live prompt",
    )

    assert 'id="downloadJsonl"' in html
    assert 'id="sessionJsonlData"' in html
    assert 'link.download = "abc123.jsonl";' in html
    match = re.search(
        r'<script id="sessionJsonlData" type="application/octet-stream">([^<]*)</script>',
        html,
    )
    assert match is not None
    decoded = base64.b64decode(match.group(1)).decode("utf-8")
    lines = decoded.splitlines()
    # The download embeds every entry, including leaf pointers filtered from the view,
    # but keeps the live prompt outside persisted transcript data.
    assert len(lines) == 3
    assert '"id":"leaf"' in lines[2]
    assert "Private live prompt" not in decoded
    assert "system_prompt" not in decoded


def test_render_session_html_jsonl_filename_falls_back_to_title_slug() -> None:
    entries = [MessageEntry(id="root", message=UserMessage(content="Hello"))]

    html = render_session_html(entries, title="Fix Login Redirect Bug!")

    assert 'link.download = "fix-login-redirect-bug.jsonl";' in html


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


def test_render_session_html_includes_usage_tab() -> None:
    entries = [
        MessageEntry(id="root", message=UserMessage(content="Hello")),
        MessageEntry(
            id="reply",
            parent_id="root",
            message=AssistantMessage(
                content="Hi",
                provider="anthropic",
                model="claude-sonnet-4-5",
                usage=Usage(input=120, output=30, cache_read=880, cache_write=40),
            ),
        ),
        LeafEntry(id="leaf", parent_id="reply", entry_id="reply"),
    ]

    html = render_session_html(entries, title="Usage Export")

    assert 'id="panel-usage"' in html
    assert 'class="usage-chart"' in html
    assert 'class="png-button"' in html
    assert "Cache hit rate" in html
    assert "Estimated cost" in html
    # Tabs use focusable buttons and implement the ARIA keyboard interaction.
    assert '<button\n        type="button"\n        class="tab"\n        id="tab-usage"' in html
    assert 'event.key !== "ArrowLeft" && event.key !== "ArrowRight"' in html
    assert "tabs[next].focus()" in html
    assert 'currentHash === "#usage" || currentHash === "#cache"' in html
    assert 'window.location.hash === "#usage" || window.location.hash === "#cache"' in html


def test_render_session_html_uses_tau_theme_palette() -> None:
    entries = [
        MessageEntry(id="root", message=UserMessage(content="Hello")),
        MessageEntry(
            id="reply",
            parent_id="root",
            message=AssistantMessage(content="Hi", usage=Usage(input=10, output=5)),
        ),
    ]

    html = render_session_html(entries)

    assert f"--accent: {TAU_LIGHT_THEME.accent}" in html
    assert f"--accent: {TAU_DARK_THEME.accent}" in html
    assert f"--bg: {TAU_LIGHT_THEME.screen_background}" in html
    assert f"--bg: {TAU_DARK_THEME.screen_background}" in html
    assert ":root.theme-dark" in html
    assert 'id="tab-usage"' in html
    assert "tau-themechange" in html
    assert f'data-dark="{TAU_DARK_THEME.error}"' in html
    assert f'data-light="{TAU_LIGHT_THEME.error}"' in html
