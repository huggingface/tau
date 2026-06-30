"""Session export helpers for human-readable transcript views."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from tau_agent.messages import AssistantMessage, ToolResultMessage, UserMessage
from tau_agent.session import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionEntry,
    SessionInfoEntry,
    SessionTreeError,
    ThinkingLevelChangeEntry,
    path_to_entry,
)
from tau_agent.types import JSONValue


class SessionExportError(ValueError):
    """Raised when a session cannot be exported."""


def default_session_export_path(session_path: Path) -> Path:
    """Return the default HTML export path for a JSONL session file."""
    return session_path.with_suffix(".html")


def default_session_export_artifact_path(
    session_path: Path,
    *,
    destination_dir: Path,
    format: str = "html",
) -> Path:
    """Return the default user-facing export artifact path."""
    suffix = _export_suffix(format)
    return destination_dir / f"{session_path.stem}{suffix}"


def export_session_jsonl(entries: Sequence[SessionEntry], output_path: Path) -> Path:
    """Write session entries to a JSONL export and return its path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [entry.model_dump_json() for entry in entries]
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return output_path


def export_session_html(
    entries: Sequence[SessionEntry],
    output_path: Path,
    *,
    title: str = "Tau Session Export",
    source: str | None = None,
) -> Path:
    """Write a self-contained HTML session export and return its path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_session_html(entries, title=title, source=source),
        encoding="utf-8",
    )
    return output_path


def export_session_artifact(
    entries: Sequence[SessionEntry],
    output_path: Path,
    *,
    title: str = "Tau Session Export",
    source: str | None = None,
    format: str | None = None,
) -> Path:
    """Write a session export in the requested or inferred format."""
    export_format = normalize_export_format(format or output_path.suffix.removeprefix("."))
    if export_format == "jsonl":
        return export_session_jsonl(entries, output_path)
    return export_session_html(entries, output_path, title=title, source=source)


def normalize_export_format(value: str | None) -> str:
    """Normalize a session export format name."""
    normalized = (value or "html").strip().lower().removeprefix(".")
    if normalized in {"htm", "html"}:
        return "html"
    if normalized == "jsonl":
        return "jsonl"
    raise SessionExportError(f"Unsupported export format: {value}")


def _export_suffix(format: str) -> str:
    return ".jsonl" if normalize_export_format(format) == "jsonl" else ".html"


def render_session_html(
    entries: Sequence[SessionEntry],
    *,
    title: str = "Tau Session Export",
    source: str | None = None,
) -> str:
    """Render a session transcript/tree as standalone HTML."""
    entry_list = list(entries)
    active_leaf_id = _active_leaf_id(entry_list)
    active_path_ids = _active_path_ids(entry_list, active_leaf_id)
    tree_html = _render_tree(entry_list, active_path_ids, active_leaf_id)
    details_html = _render_entry_details(entry_list, active_path_ids, active_leaf_id)
    source_html = f'<p class="source">Source: <code>{_escape(source)}</code></p>' if source else ""
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --canvas: #f4f6f8;
      --surface: #ffffff;
      --surface-muted: #eef3f7;
      --text: #16181c;
      --muted: #65707c;
      --line: #d9e0e7;
      --accent: #0f766e;
      --accent-warm: #b45309;
      --accent-soft: #dff5f1;
      --code-bg: #eef2f6;
      font-family:
        Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
        sans-serif;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --canvas: #111418;
        --surface: #171b20;
        --surface-muted: #202832;
        --text: #edf1f5;
        --muted: #a4afbb;
        --line: #303a45;
        --accent: #5eead4;
        --accent-warm: #fbbf24;
        --accent-soft: #123b37;
        --code-bg: #1d2530;
      }}
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background: var(--canvas);
      color: var(--text);
      line-height: 1.5;
    }}
    header {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px clamp(18px, 4vw, 48px) 20px;
    }}
    h1, h2, h3, h4 {{ margin: 0; line-height: 1.2; }}
    h1 {{ font-size: clamp(1.85rem, 3vw, 2.45rem); font-weight: 750; }}
    h2 {{
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0;
      margin-bottom: 14px;
      text-transform: uppercase;
    }}
    h3 {{ font-size: 1.02rem; font-weight: 720; }}
    h4 {{ font-size: 0.9rem; margin-top: 16px; }}
    code, pre {{
      font-family:
        "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      font-size: 0.9em;
    }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: var(--code-bg);
      border: 0;
      border-left: 3px solid var(--line);
      border-radius: 4px;
      padding: 12px 14px;
      margin: 10px 0 0;
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 0.8rem;
      font-weight: 760;
      margin: 0 0 8px;
      text-transform: uppercase;
    }}
    .source, .generated {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .export-meta {{
      border-top: 1px solid var(--line);
      display: flex;
      flex-wrap: wrap;
      gap: 6px 18px;
      margin-top: 18px;
      padding-top: 14px;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(260px, 360px) minmax(0, 1fr);
      gap: 34px;
      max-width: 1280px;
      margin: 0 auto;
      padding: 18px clamp(18px, 4vw, 48px) 56px;
    }}
    aside {{
      position: sticky;
      top: 18px;
      align-self: start;
      max-height: calc(100vh - 32px);
      overflow: auto;
      padding: 4px 0 4px 16px;
      border-left: 1px solid var(--line);
    }}
    article {{
      position: relative;
      margin: 0;
      padding: 0 0 28px 28px;
      border-left: 1px solid var(--line);
    }}
    article + article {{ padding-top: 28px; }}
    article::before {{
      content: "";
      position: absolute;
      left: -5px;
      top: 3px;
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--surface);
      border: 2px solid var(--accent);
    }}
    article.active-entry::before {{
      border-color: var(--accent-warm);
      box-shadow: 0 0 0 4px var(--accent-soft);
    }}
    .tree {{
      list-style: none;
      margin: 0;
      padding-left: 0;
    }}
    .tree .tree {{
      margin-left: 10px;
      padding-left: 12px;
      border-left: 1px solid var(--line);
    }}
    .tree li {{ margin: 6px 0; }}
    .node-link {{
      display: block;
      color: var(--text);
      text-decoration: none;
      border-radius: 4px;
      padding: 7px 9px;
    }}
    .node-link:hover {{ background: var(--surface-muted); }}
    .active-path > .node-link {{
      background: var(--accent-soft);
    }}
    .active-leaf > .node-link {{
      box-shadow: inset 3px 0 0 var(--accent-warm);
    }}
    .node-type {{
      display: block;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .node-meta {{
      display: block;
      color: var(--muted);
      font-size: 0.82rem;
      overflow-wrap: anywhere;
    }}
    .entry-meta {{
      display: grid;
      grid-template-columns: max-content minmax(0, 1fr);
      gap: 4px 10px;
      margin: 12px 0 0;
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .entry-meta dt {{ font-weight: 700; color: var(--text); }}
    .entry-meta dd {{ margin: 0; overflow-wrap: anywhere; }}
    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }}
    .badge {{
      background: var(--surface-muted);
      border-radius: 999px;
      padding: 2px 8px;
      color: var(--accent-warm);
      font-size: 0.78rem;
      font-weight: 700;
    }}
    .message-role {{
      margin-top: 14px;
      font-weight: 700;
      text-transform: capitalize;
    }}
    .empty {{
      color: var(--muted);
      font-style: italic;
    }}
    @media (max-width: 820px) {{
      main {{ grid-template-columns: 1fr; }}
      aside {{ position: static; max-height: none; }}
      article, article + article {{ padding-left: 20px; }}
    }}
  </style>
</head>
<body>
  <header>
    <p class="eyebrow">Tau session export</p>
    <h1>{_escape(title)}</h1>
    <div class="export-meta">
      {source_html}
      <p class="generated">
        Generated: <time datetime="{_attr(generated_at)}">{_escape(generated_at)}</time>
      </p>
    </div>
  </header>
  <main class="session-shell">
    <aside class="tree-rail">
      <h2>Session Map</h2>
      {tree_html}
    </aside>
    <section class="entry-stream" aria-label="Session entries">
      <h2>Transcript Stream</h2>
      {details_html}
    </section>
  </main>
</body>
</html>
"""


def _active_leaf_id(entries: Sequence[SessionEntry]) -> str | None:
    for entry in reversed(entries):
        if isinstance(entry, LeafEntry):
            return entry.entry_id
    if entries:
        return entries[-1].id
    return None


def _active_path_ids(entries: list[SessionEntry], active_leaf_id: str | None) -> set[str]:
    if active_leaf_id is None:
        return set()
    try:
        return {entry.id for entry in path_to_entry(entries, active_leaf_id)}
    except SessionTreeError:
        return {active_leaf_id}


def _render_tree(
    entries: list[SessionEntry],
    active_path_ids: set[str],
    active_leaf_id: str | None,
) -> str:
    if not entries:
        return '<p class="empty">No entries.</p>'

    entry_ids = {entry.id for entry in entries}
    children_by_parent: dict[str | None, list[SessionEntry]] = defaultdict(list)
    for entry in entries:
        children_by_parent[entry.parent_id].append(entry)

    roots = [
        entry for entry in entries if entry.parent_id is None or entry.parent_id not in entry_ids
    ]
    if not roots:
        roots = list(entries)

    rendered_ids: set[str] = set()
    rendered_nodes = [
        _render_tree_node(
            root,
            children_by_parent,
            active_path_ids,
            active_leaf_id,
            ancestors=set(),
            rendered_ids=rendered_ids,
        )
        for root in roots
        if root.id not in rendered_ids
    ]

    dangling_nodes = [
        _render_tree_node(
            entry,
            children_by_parent,
            active_path_ids,
            active_leaf_id,
            ancestors=set(),
            rendered_ids=rendered_ids,
        )
        for entry in entries
        if entry.id not in rendered_ids
    ]
    if dangling_nodes:
        rendered_nodes.append(
            "<li>"
            '<span class="node-link"><span class="node-type">Unreachable entries</span>'
            '<span class="node-meta">Entries with cyclic or duplicate tree links.</span></span>'
            f'<ol class="tree">{"".join(dangling_nodes)}</ol>'
            "</li>"
        )

    return f'<ol class="tree">{"".join(rendered_nodes)}</ol>'


def _render_tree_node(
    entry: SessionEntry,
    children_by_parent: dict[str | None, list[SessionEntry]],
    active_path_ids: set[str],
    active_leaf_id: str | None,
    *,
    ancestors: set[str],
    rendered_ids: set[str],
) -> str:
    rendered_ids.add(entry.id)
    classes = ["tree-node"]
    if entry.id in active_path_ids:
        classes.append("active-path")
    if entry.id == active_leaf_id:
        classes.append("active-leaf")
    child_entries = [
        child for child in children_by_parent.get(entry.id, []) if child.id not in ancestors
    ]
    child_html = ""
    if child_entries:
        next_ancestors = {*ancestors, entry.id}
        child_html = (
            '<ol class="tree">'
            + "".join(
                _render_tree_node(
                    child,
                    children_by_parent,
                    active_path_ids,
                    active_leaf_id,
                    ancestors=next_ancestors,
                    rendered_ids=rendered_ids,
                )
                for child in child_entries
                if child.id not in rendered_ids
            )
            + "</ol>"
        )

    return (
        f'<li class="{" ".join(classes)}">'
        f'<a class="node-link" href="#entry-{_attr(entry.id)}">'
        f'<span class="node-type">{_escape(_entry_title(entry))}</span>'
        f'<span class="node-meta">{_escape(_entry_summary(entry))}</span>'
        "</a>"
        f"{child_html}"
        "</li>"
    )


def _render_entry_details(
    entries: Sequence[SessionEntry],
    active_path_ids: set[str],
    active_leaf_id: str | None,
) -> str:
    if not entries:
        return '<article><p class="empty">No session entries were found.</p></article>'

    return "".join(
        _render_entry_detail(
            index,
            entry,
            active_path_ids,
            active_leaf_id,
        )
        for index, entry in enumerate(entries, start=1)
    )


def _render_entry_detail(
    index: int,
    entry: SessionEntry,
    active_path_ids: set[str],
    active_leaf_id: str | None,
) -> str:
    classes = ["entry-card"]
    badges = []
    if entry.id in active_path_ids:
        badges.append("active path")
    if entry.id == active_leaf_id:
        badges.append("active leaf")
    if badges:
        classes.append("active-entry")
    badge_html = (
        '<div class="badges">'
        + "".join(f'<span class="badge">{_escape(badge)}</span>' for badge in badges)
        + "</div>"
        if badges
        else ""
    )
    body = _render_entry_body(entry)
    return (
        f'<article id="entry-{_attr(entry.id)}" class="{" ".join(classes)}">'
        f"<h3>{index}. {_escape(_entry_title(entry))}</h3>"
        f"{badge_html}"
        '<dl class="entry-meta">'
        "<dt>id</dt>"
        f"<dd><code>{_escape(entry.id)}</code></dd>"
        "<dt>parent</dt>"
        f"<dd>{_entry_parent_html(entry)}</dd>"
        "<dt>timestamp</dt>"
        f"<dd>{_escape(_format_timestamp(entry.timestamp))}</dd>"
        "</dl>"
        f"{body}"
        "</article>"
    )


def _render_entry_body(entry: SessionEntry) -> str:
    if isinstance(entry, MessageEntry):
        return _render_message_entry(entry)
    if isinstance(entry, ModelChangeEntry):
        return f"<p>Model changed to <code>{_escape(entry.model)}</code>.</p>"
    if isinstance(entry, ThinkingLevelChangeEntry):
        level = entry.thinking_level if entry.thinking_level is not None else "off"
        return f"<p>Thinking level changed to <code>{_escape(level)}</code>.</p>"
    if isinstance(entry, CompactionEntry):
        return (
            "<p>Compaction summary:</p>"
            f"<pre>{_escape(entry.summary)}</pre>"
            f"{_render_list('Replaces entries', entry.replaces_entry_ids)}"
        )
    if isinstance(entry, BranchSummaryEntry):
        branch_root = entry.branch_root_id or "none"
        return (
            f"<p>Branch root: <code>{_escape(branch_root)}</code></p>"
            f"<pre>{_escape(entry.summary)}</pre>"
        )
    if isinstance(entry, LabelEntry):
        return f"<p>Session label: <strong>{_escape(entry.label)}</strong></p>"
    if isinstance(entry, LeafEntry):
        leaf = entry.entry_id or "none"
        return f"<p>Active leaf pointer: <code>{_escape(leaf)}</code></p>"
    if isinstance(entry, SessionInfoEntry):
        return (
            f"<p>Title: <strong>{_escape(entry.title or 'Untitled')}</strong></p>"
            f"<p>Working directory: <code>{_escape(entry.cwd or 'unknown')}</code></p>"
            f"<p>Created: {_escape(_format_timestamp(entry.created_at))}</p>"
        )
    if isinstance(entry, CustomEntry):
        return (
            f"<p>Custom namespace: <code>{_escape(entry.namespace)}</code></p>"
            f"<pre>{_escape(_json_dump(entry.data))}</pre>"
        )
    return f"<pre>{_escape(entry.model_dump_json(indent=2))}</pre>"


def _render_message_entry(entry: MessageEntry) -> str:
    message = entry.message
    if isinstance(message, UserMessage):
        return f'<p class="message-role">user</p><pre>{_escape(message.content)}</pre>'
    if isinstance(message, AssistantMessage):
        tool_calls = ""
        if message.tool_calls:
            tool_calls = (
                "<h4>Tool calls</h4>"
                "<ul>"
                + "".join(
                    "<li>"
                    f"<code>{_escape(call.name)}</code> "
                    f"<code>{_escape(call.id)}</code>"
                    f"<pre>{_escape(_json_dump(call.arguments))}</pre>"
                    "</li>"
                    for call in message.tool_calls
                )
                + "</ul>"
            )
        content = message.content or "(no assistant text)"
        return f'<p class="message-role">assistant</p><pre>{_escape(content)}</pre>{tool_calls}'
    if isinstance(message, ToolResultMessage):
        metadata = [
            ("tool", message.name),
            ("tool_call_id", message.tool_call_id),
            ("ok", str(message.ok)),
        ]
        if message.error:
            metadata.append(("error", message.error))
        body = (
            '<p class="message-role">tool result</p>'
            f"{_render_metadata(metadata)}"
            f"<pre>{_escape(message.content)}</pre>"
        )
        if message.data is not None:
            body += f"<h4>Data</h4><pre>{_escape(_json_dump(message.data))}</pre>"
        if message.details is not None:
            body += f"<h4>Details</h4><pre>{_escape(_json_dump(message.details))}</pre>"
        return body
    return f"<pre>{_escape(entry.model_dump_json(indent=2))}</pre>"


def _render_metadata(items: Iterable[tuple[str, str]]) -> str:
    return (
        '<dl class="entry-meta">'
        + "".join(
            f"<dt>{_escape(key)}</dt><dd><code>{_escape(value)}</code></dd>" for key, value in items
        )
        + "</dl>"
    )


def _render_list(title: str, values: Sequence[str]) -> str:
    if not values:
        return ""
    return (
        f"<h4>{_escape(title)}</h4>"
        "<ul>" + "".join(f"<li><code>{_escape(value)}</code></li>" for value in values) + "</ul>"
    )


def _entry_parent_html(entry: SessionEntry) -> str:
    if entry.parent_id is None:
        return '<span class="empty">root</span>'
    return f'<a href="#entry-{_attr(entry.parent_id)}"><code>{_escape(entry.parent_id)}</code></a>'


def _entry_title(entry: SessionEntry) -> str:
    if isinstance(entry, MessageEntry):
        return f"message:{entry.message.role}"
    if isinstance(entry, ModelChangeEntry):
        return "model change"
    if isinstance(entry, ThinkingLevelChangeEntry):
        return "thinking level change"
    if isinstance(entry, CompactionEntry):
        return "compaction"
    if isinstance(entry, BranchSummaryEntry):
        return "branch summary"
    if isinstance(entry, LabelEntry):
        return "label"
    if isinstance(entry, LeafEntry):
        return "leaf pointer"
    if isinstance(entry, SessionInfoEntry):
        return "session info"
    if isinstance(entry, CustomEntry):
        return f"custom:{entry.namespace}"
    return entry.type


def _entry_summary(entry: SessionEntry) -> str:
    if isinstance(entry, MessageEntry):
        message = entry.message
        if isinstance(message, ToolResultMessage):
            return f"{message.name}: {_summarize_text(message.content)}"
        if isinstance(message, AssistantMessage) and message.tool_calls:
            tool_names = ", ".join(call.name for call in message.tool_calls)
            text = _summarize_text(message.content) or "tool call"
            return f"{text} [{tool_names}]"
        return _summarize_text(message.content)
    if isinstance(entry, ModelChangeEntry):
        return entry.model
    if isinstance(entry, ThinkingLevelChangeEntry):
        return entry.thinking_level or "off"
    if isinstance(entry, CompactionEntry):
        return _summarize_text(entry.summary)
    if isinstance(entry, BranchSummaryEntry):
        return _summarize_text(entry.summary)
    if isinstance(entry, LabelEntry):
        return entry.label
    if isinstance(entry, LeafEntry):
        return entry.entry_id or "none"
    if isinstance(entry, SessionInfoEntry):
        return entry.title or entry.cwd or "session metadata"
    if isinstance(entry, CustomEntry):
        return f"{len(entry.data)} field(s)"
    return entry.id


def _summarize_text(text: str, *, limit: int = 92) -> str:
    summary = " ".join(text.split())
    if len(summary) <= limit:
        return summary
    return summary[: limit - 3].rstrip() + "..."


def _json_dump(value: dict[str, JSONValue]) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).replace(microsecond=0).isoformat()


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def _attr(value: object) -> str:
    return html.escape(str(value), quote=True)
