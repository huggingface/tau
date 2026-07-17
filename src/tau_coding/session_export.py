"""Session export helpers for human-readable transcript views."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import JsonLexer

from tau_agent.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    message_text,
)
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
    visible_entries = _visible_entries(entry_list)
    tree_html = _render_tree(visible_entries, active_path_ids, active_leaf_id)
    details_html = _render_entry_details(visible_entries, active_path_ids, active_leaf_id)
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
      color-scheme: light;
      --canvas: #ffffff;
      --surface: #ffffff;
      --surface-muted: #f6f8fc;
      --text: #13213c;
      --muted: #54607a;
      --line: #dce4f2;
      --line-strong: #c9d6ee;
      --accent: #1b3fa0;
      --code-bg: #f6f8fc;
      --serif: Charter, "Iowan Old Style", Georgia, ui-serif, serif;
      --sans: "Space Grotesk", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      font-family: var(--serif);
    }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) {{
        color-scheme: dark;
        --canvas: #0f1420;
        --surface: #141a29;
        --surface-muted: #1a2133;
        --text: #e7ecf7;
        --muted: #9aa5c0;
        --line: #262f47;
        --line-strong: #333f5c;
        --accent: #7fa0f0;
        --code-bg: #171e30;
      }}
    }}
    :root[data-theme="dark"] {{
      color-scheme: dark;
      --canvas: #0f1420;
      --surface: #141a29;
      --surface-muted: #1a2133;
      --text: #e7ecf7;
      --muted: #9aa5c0;
      --line: #262f47;
      --line-strong: #333f5c;
      --accent: #7fa0f0;
      --code-bg: #171e30;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background: var(--canvas);
      color: var(--text);
      line-height: 1.55;
    }}
    header {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px clamp(18px, 4vw, 48px) 20px;
    }}
    h1, h2, h3, h4 {{ margin: 0; line-height: 1.25; font-family: var(--sans); }}
    h1 {{
      font-size: clamp(1.5rem, 2.4vw, 1.9rem);
      font-weight: 500;
      letter-spacing: -0.01em;
    }}
    h2 {{
      color: var(--muted);
      font-size: 0.7rem;
      font-weight: 500;
      letter-spacing: 0.12em;
      margin-bottom: 12px;
      text-transform: uppercase;
    }}
    h3 {{
      font-size: 0.66rem;
      font-weight: 500;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    h4 {{
      font-size: 0.7rem;
      font-weight: 500;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-top: 16px;
    }}
    code, pre {{
      font-family: var(--mono);
      font-size: 0.85em;
    }}
    p {{ margin: 0; }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: var(--code-bg);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 12px 14px;
      margin: 10px 0 0;
    }}
    .header-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .eyebrow {{
      font-family: var(--sans);
      color: var(--muted);
      font-size: 0.7rem;
      font-weight: 500;
      letter-spacing: 0.14em;
      margin: 0;
      text-transform: uppercase;
    }}
    .theme-toggle {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      padding: 0;
      color: var(--muted);
      background: none;
      border: 1px solid var(--line-strong);
      border-radius: 50%;
      cursor: pointer;
      transition: color .15s, border-color .15s;
    }}
    .theme-toggle:hover {{ color: var(--accent); border-color: var(--accent); }}
    .theme-toggle .icon {{ width: 14px; height: 14px; }}
    .theme-toggle .theme-icon-dark {{ display: none; }}
    :root[data-theme="dark"] .theme-toggle .theme-icon-light {{ display: none; }}
    :root[data-theme="dark"] .theme-toggle .theme-icon-dark {{ display: inline-block; }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) .theme-toggle .theme-icon-light {{ display: none; }}
      :root:not([data-theme="light"]) .theme-toggle .theme-icon-dark {{ display: inline-block; }}
    }}
    .source, .generated {{
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 0.85rem;
      font-family: var(--sans);
    }}
    .export-meta {{
      border-top: 1px solid var(--line);
      display: flex;
      flex-wrap: wrap;
      gap: 4px 18px;
      margin-top: 20px;
      padding-top: 14px;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(240px, 320px) minmax(0, 1fr);
      gap: 40px;
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
      padding: 2px 16px 4px 0;
      border-right: 1px solid var(--line);
    }}
    .icon {{
      display: inline-block;
      flex: 0 0 auto;
      width: 13px;
      height: 13px;
      color: var(--muted);
    }}
    .icon svg {{ display: block; width: 100%; height: 100%; }}
    article {{
      margin: 0;
      padding: 18px 0;
      border-bottom: 1px solid var(--line);
    }}
    article:first-child {{ padding-top: 0; }}
    article:last-child {{ border-bottom: 0; }}
    article.active-entry {{
      background: var(--surface-muted);
      margin: 0 -16px;
      padding: 18px 16px;
    }}
    article.active-entry:first-child {{ padding-top: 18px; }}
    .entry-index {{
      display: flex;
      align-items: center;
      gap: 7px;
      font-family: var(--sans);
      font-size: 0.68rem;
      font-weight: 500;
      letter-spacing: 0.08em;
      color: var(--muted);
      text-transform: uppercase;
    }}
    .entry-index .icon {{ color: var(--muted); }}
    .entry-status {{
      margin-left: auto;
      color: var(--accent);
      font-weight: 500;
      letter-spacing: 0.04em;
      text-transform: none;
    }}
    .tree {{
      list-style: none;
      margin: 0;
      padding-left: 0;
    }}
    .tree .tree {{
      margin-left: 8px;
      padding-left: 13px;
      border-left: 1px solid var(--line);
    }}
    .tree li {{ margin: 1px 0; }}
    .node-link {{
      display: flex;
      align-items: center;
      gap: 7px;
      color: var(--text);
      text-decoration: none;
      border-radius: 4px;
      padding: 5px 8px;
    }}
    .node-link:hover {{ background: var(--surface-muted); }}
    .active-path > .node-link {{ color: var(--accent); }}
    .active-leaf > .node-link {{
      background: var(--surface-muted);
      font-weight: 500;
    }}
    .node-link .icon {{ color: var(--muted); }}
    .active-path > .node-link .icon {{ color: var(--accent); }}
    .node-type {{
      display: block;
      font-family: var(--sans);
      font-size: 0.76rem;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .entry-meta {{
      display: grid;
      grid-template-columns: max-content minmax(0, 1fr);
      gap: 3px 10px;
      margin: 10px 0 0;
      font-family: var(--sans);
      color: var(--muted);
      font-size: 0.78rem;
    }}
    .entry-meta dt {{
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-size: 0.65rem;
      align-self: baseline;
      padding-top: 2px;
    }}
    .entry-meta dd {{ margin: 0; overflow-wrap: anywhere; }}
    .message-role {{
      display: flex;
      align-items: center;
      gap: 6px;
      margin: 0 0 4px;
      font-family: var(--sans);
      font-size: 0.7rem;
      font-weight: 500;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .empty {{
      color: var(--muted);
      font-style: italic;
    }}
    pre.highlight {{ padding: 12px 14px; }}
    .highlight .p {{ color: var(--muted); }}
    .highlight .nt {{ color: var(--accent); }}
    .highlight .s2, .highlight .s1 {{ color: #2f7a4f; }}
    .highlight .mi, .highlight .mf {{ color: #a05a12; }}
    .highlight .kc {{ color: #a02f6b; font-weight: 500; }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) .highlight .s2,
      :root:not([data-theme="light"]) .highlight .s1 {{ color: #7fd08a; }}
      :root:not([data-theme="light"]) .highlight .mi,
      :root:not([data-theme="light"]) .highlight .mf {{ color: #e0a95e; }}
      :root:not([data-theme="light"]) .highlight .kc {{ color: #e58fc0; }}
    }}
    :root[data-theme="dark"] .highlight .s2,
    :root[data-theme="dark"] .highlight .s1 {{ color: #7fd08a; }}
    :root[data-theme="dark"] .highlight .mi,
    :root[data-theme="dark"] .highlight .mf {{ color: #e0a95e; }}
    :root[data-theme="dark"] .highlight .kc {{ color: #e58fc0; }}
    @media (max-width: 820px) {{
      main {{ grid-template-columns: 1fr; }}
      aside {{
        position: static;
        max-height: none;
        border-right: 0;
        border-bottom: 1px solid var(--line);
        padding: 2px 0 20px;
      }}
      article.active-entry {{ margin: 0 -18px; padding: 18px 18px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-top">
      <p class="eyebrow">Tau session export</p>
      <button
        type="button"
        class="theme-toggle"
        id="themeToggle"
        aria-label="Toggle light/dark theme"
      >
        <span class="icon theme-icon-light">{_ICON_SUN}</span>
        <span class="icon theme-icon-dark">{_ICON_MOON}</span>
      </button>
    </div>
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
      <h2>Session</h2>
      {tree_html}
    </aside>
    <section class="entry-stream" aria-label="Session entries">
      <h2>Transcript</h2>
      {details_html}
    </section>
  </main>
  <script>
    (function () {{
      var root = document.documentElement;
      var stored = null;
      try {{
        stored = window.localStorage.getItem("tau-session-export-theme");
      }} catch (err) {{
        stored = null;
      }}
      if (stored === "light" || stored === "dark") {{
        root.setAttribute("data-theme", stored);
      }}
      var toggle = document.getElementById("themeToggle");
      if (!toggle) {{
        return;
      }}
      toggle.addEventListener("click", function () {{
        var prefersDark =
          window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        var current = root.getAttribute("data-theme") || (prefersDark ? "dark" : "light");
        var next = current === "dark" ? "light" : "dark";
        root.setAttribute("data-theme", next);
        try {{
          window.localStorage.setItem("tau-session-export-theme", next);
        }} catch (err) {{
          /* localStorage unavailable; theme choice just won't persist. */
        }}
      }});
    }})();
  </script>
</body>
</html>
"""


def _visible_entries(entries: Sequence[SessionEntry]) -> list[SessionEntry]:
    """Filter out entries that are pointers/plumbing rather than transcript content.

    Leaf pointer entries only record which entry is the current tip of a branch;
    that information is already conveyed by the active-path/active-leaf styling,
    so showing them as their own rows would just add noise to the export.
    """
    return [entry for entry in entries if not isinstance(entry, LeafEntry)]


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
        _render_tree_chain(
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
        _render_tree_chain(
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
            '<span class="node-link"><span class="node-type">Unreachable entries</span></span>'
            f'<ol class="tree">{"".join(dangling_nodes)}</ol>'
            "</li>"
        )

    return f'<ol class="tree">{"".join(rendered_nodes)}</ol>'


def _render_tree_chain(
    start: SessionEntry,
    children_by_parent: dict[str | None, list[SessionEntry]],
    active_path_ids: set[str],
    active_leaf_id: str | None,
    *,
    ancestors: set[str],
    rendered_ids: set[str],
) -> str:
    """Render `start` and its unbranched descendants as flat sibling `<li>`s.

    Session history is usually a straight line, so a naive tree renders one
    nested level per entry. Instead, follow single-child chains at the same
    list level and only introduce a nested `<ol>` where the history actually
    forks (a node with more than one child).
    """
    chain: list[SessionEntry] = []
    fork_children: list[SessionEntry] = []
    current: SessionEntry | None = start
    chain_ancestors = set(ancestors)
    while current is not None:
        rendered_ids.add(current.id)
        chain.append(current)
        chain_ancestors.add(current.id)
        children = [
            child
            for child in children_by_parent.get(current.id, [])
            if child.id not in chain_ancestors
        ]
        if len(children) == 1:
            current = children[0]
            continue
        fork_children = children
        current = None

    li_html_parts = []
    for position, node in enumerate(chain):
        nested_html = ""
        if position == len(chain) - 1 and fork_children:
            nested_html = "".join(
                _render_tree_chain(
                    child,
                    children_by_parent,
                    active_path_ids,
                    active_leaf_id,
                    ancestors=chain_ancestors,
                    rendered_ids=rendered_ids,
                )
                for child in fork_children
                if child.id not in rendered_ids
            )
            nested_html = f'<ol class="tree">{nested_html}</ol>'
        li_html_parts.append(_render_tree_node(node, nested_html, active_path_ids, active_leaf_id))
    return "".join(li_html_parts)


def _render_tree_node(
    entry: SessionEntry,
    nested_html: str,
    active_path_ids: set[str],
    active_leaf_id: str | None,
) -> str:
    classes = ["tree-node"]
    if entry.id in active_path_ids:
        classes.append("active-path")
    if entry.id == active_leaf_id:
        classes.append("active-leaf")
    summary = _entry_summary(entry)
    label = f"{_entry_title(entry)}: {summary}" if summary else _entry_title(entry)
    return (
        f'<li class="{" ".join(c for c in classes if c)}">'
        f'<a class="node-link" href="#entry-{_attr(entry.id)}">'
        f'<span class="icon">{_entry_icon(entry)}</span>'
        f'<span class="node-type">{_escape(label)}</span>'
        "</a>"
        f"{nested_html}"
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
    status_bits = []
    if entry.id in active_path_ids:
        status_bits.append("active path")
    if entry.id == active_leaf_id:
        status_bits.append("active leaf")
    if status_bits:
        classes.append("active-entry")
    status_html = (
        f'<span class="entry-status">{_escape(" · ".join(status_bits))}</span>'
        if status_bits
        else ""
    )
    body = _render_entry_body(entry)
    return (
        f'<article id="entry-{_attr(entry.id)}" class="{" ".join(c for c in classes if c)}">'
        f'<p class="entry-index"><span class="icon">{_entry_icon(entry)}</span>'
        f"{index:02d} · {_escape(_entry_title(entry))}{status_html}</p>"
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
            f"{_render_json_block(entry.data)}"
        )
    return f"<pre>{_escape(entry.model_dump_json(indent=2))}</pre>"


def _render_message_entry(entry: MessageEntry) -> str:
    message = entry.message
    if isinstance(message, UserMessage):
        return (
            f'<p class="message-role"><span class="icon">{_ICON_USER}</span>user</p>'
            f"<pre>{_escape(message.text)}</pre>"
        )
    if isinstance(message, AssistantMessage):
        blocks: list[str] = []
        for block in message.content:
            if isinstance(block, ThinkingContent):
                blocks.append(f"<h4>Thinking</h4><pre>{_escape(block.thinking)}</pre>")
            elif isinstance(block, TextContent):
                blocks.append(f"<pre>{_escape(block.text)}</pre>")
            elif isinstance(block, ToolCall):
                blocks.append(
                    "<h4>Tool call</h4><ul><li>"
                    f"<code>{_escape(block.name)}</code> "
                    f"<code>{_escape(block.id)}</code>"
                    f"{_render_json_block(block.arguments)}"
                    "</li></ul>"
                )
        content = "".join(blocks) or "<pre>(no assistant text)</pre>"
        return (
            f'<p class="message-role"><span class="icon">{_ICON_ASSISTANT}</span>assistant</p>'
            f"{content}"
        )
    if isinstance(message, ToolResultMessage):
        metadata = [
            ("tool", message.tool_name),
            ("tool_call_id", message.tool_call_id),
            ("is_error", str(message.is_error)),
        ]
        body = (
            f'<p class="message-role"><span class="icon">{_ICON_TOOL}</span>tool result</p>'
            f"{_render_metadata(metadata)}"
            f"<pre>{_escape(message.text)}</pre>"
        )
        if isinstance(message.details, dict):
            body += f"<h4>Details</h4>{_render_json_block(message.details)}"
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


_ICON_USER = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<circle cx="8" cy="5" r="2.75" fill="none" stroke="currentColor" stroke-width="1.3"/>'
    '<path d="M2.5 14c.6-3 2.9-4.5 5.5-4.5s4.9 1.5 5.5 4.5" fill="none"'
    ' stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>'
    "</svg>"
)
_ICON_ASSISTANT = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<rect x="2.5" y="3.5" width="11" height="8" rx="1.8" fill="none"'
    ' stroke="currentColor" stroke-width="1.3"/>'
    '<path d="M5.5 7.2h0M10.5 7.2h0" stroke="currentColor" stroke-width="1.6"'
    ' stroke-linecap="round"/>'
    '<path d="M8 1.5v2M5.5 13.5v1M10.5 13.5v1" stroke="currentColor" stroke-width="1.3"'
    ' stroke-linecap="round"/>'
    "</svg>"
)
_ICON_TOOL = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<rect x="8.7" y="1.6" width="3.2" height="5.6" rx=".7" transform="rotate(45 10.3 4.4)"'
    ' fill="none" stroke="currentColor" stroke-width="1.2"/>'
    '<path d="M8.1 6.8 3 11.9c-.6.6-.6 1.5.0 2.1.6.6 1.5.6 2.1.0l5.1-5.1" fill="none"'
    ' stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>'
    "</svg>"
)
_ICON_BRANCH = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<circle cx="4.5" cy="3.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.2"/>'
    '<circle cx="4.5" cy="12.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.2"/>'
    '<circle cx="11.5" cy="8" r="1.5" fill="none" stroke="currentColor" stroke-width="1.2"/>'
    '<path d="M4.5 5v3.5c0 1.1.9 2 2 2h3.5M4.5 8.5V5" fill="none" stroke="currentColor"'
    ' stroke-width="1.2"/>'
    "</svg>"
)
_ICON_LABEL = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<path d="M2.5 4.2c0-.9.8-1.7 1.7-1.7h4.4c.5.0.9.2 1.2.5l4 4c.6.6.6 1.7.0 2.4l-4.4 4.4'
    "c-.6.6-1.7.6-2.4.0"
    'l-4-4c-.3-.3-.5-.7-.5-1.2Z" fill="none" stroke="currentColor" stroke-width="1.2"'
    ' stroke-linejoin="round"/>'
    '<circle cx="5.6" cy="5.6" r="1" fill="currentColor"/>'
    "</svg>"
)
_ICON_INFO = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<circle cx="8" cy="8" r="5.75" fill="none" stroke="currentColor" stroke-width="1.2"/>'
    '<path d="M8 7.2v3.4M8 5.2h0" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>'
    "</svg>"
)
_ICON_MODEL = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<path d="M8 1.8 13.5 5v6L8 14.2 2.5 11V5Z" fill="none" stroke="currentColor"'
    ' stroke-width="1.2" stroke-linejoin="round"/>'
    '<path d="M2.5 5 8 8l5.5-3M8 8v6.2" fill="none" stroke="currentColor" stroke-width="1.2"/>'
    "</svg>"
)
_ICON_GENERIC = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<rect x="2.5" y="2.5" width="11" height="11" rx="1.6" fill="none" stroke="currentColor"'
    ' stroke-width="1.2"/>'
    '<path d="M5 5.5h6M5 8h6M5 10.5h4" stroke="currentColor" stroke-width="1.1"'
    ' stroke-linecap="round"/>'
    "</svg>"
)
_ICON_SUN = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" stroke-width="1.2"/>'
    '<path d="M8 1.6v2M8 12.4v2M1.6 8h2M12.4 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4'
    'M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4" stroke="currentColor" stroke-width="1.2"'
    ' stroke-linecap="round"/>'
    "</svg>"
)
_ICON_MOON = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<path d="M13.2 9.8A5.6 5.6 0 0 1 6.2 2.8a5.6 5.6 0 1 0 7 7Z" fill="none"'
    ' stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>'
    "</svg>"
)


def _entry_icon(entry: SessionEntry) -> str:
    if isinstance(entry, MessageEntry):
        message = entry.message
        if isinstance(message, UserMessage):
            return _ICON_USER
        if isinstance(message, AssistantMessage):
            return _ICON_ASSISTANT
        if isinstance(message, ToolResultMessage):
            return _ICON_TOOL
        return _ICON_GENERIC
    if isinstance(entry, ModelChangeEntry | ThinkingLevelChangeEntry):
        return _ICON_MODEL
    if isinstance(entry, CompactionEntry | BranchSummaryEntry):
        return _ICON_BRANCH
    if isinstance(entry, LabelEntry):
        return _ICON_LABEL
    if isinstance(entry, SessionInfoEntry):
        return _ICON_INFO
    return _ICON_GENERIC


def _entry_parent_html(entry: SessionEntry) -> str:
    if entry.parent_id is None:
        return '<span class="empty">root</span>'
    return f'<a href="#entry-{_attr(entry.parent_id)}"><code>{_escape(entry.parent_id)}</code></a>'


def _entry_title(entry: SessionEntry) -> str:
    if isinstance(entry, MessageEntry):
        return entry.message.role
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
            return f"{message.tool_name}: {_summarize_text(message.text)}"
        if isinstance(message, AssistantMessage) and message.tool_calls:
            tool_names = ", ".join(call.name for call in message.tool_calls)
            text = _summarize_text(message.text) or "tool call"
            return f"{text} [{tool_names}]"
        return _summarize_text(message_text(message))
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


_JSON_LEXER = JsonLexer()
_HIGHLIGHT_FORMATTER = HtmlFormatter(nowrap=True)


def _render_json_block(value: dict[str, JSONValue]) -> str:
    """Render a JSON payload as a syntax-highlighted, self-contained <pre> block."""
    source = _json_dump(value)
    try:
        highlighted = highlight(source, _JSON_LEXER, _HIGHLIGHT_FORMATTER)
    except Exception:  # noqa: BLE001 - fall back to plain escaped text
        return f"<pre>{_escape(source)}</pre>"
    return f'<pre class="highlight">{highlighted}</pre>'


def _format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).replace(microsecond=0).isoformat()


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def _attr(value: object) -> str:
    return html.escape(str(value), quote=True)
