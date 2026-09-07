"""Session export helpers for human-readable transcript views."""

from __future__ import annotations

import base64
import html
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeGuard

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
from tau_coding.session_usage import (
    USAGE_SCRIPT,
    USAGE_STYLES,
    collect_session_usage,
    render_usage_dashboard,
)
from tau_coding.tui.themes import TAU_DARK_THEME, TAU_LIGHT_THEME, TuiTheme


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
    output_path.write_text(_session_jsonl_text(entries), encoding="utf-8")
    return output_path


def _session_jsonl_text(entries: Sequence[SessionEntry]) -> str:
    """Serialize session entries to JSONL text (one JSON object per line)."""
    lines = [entry.model_dump_json() for entry in entries]
    return "\n".join(lines) + ("\n" if lines else "")


def export_session_html(
    entries: Sequence[SessionEntry],
    output_path: Path,
    *,
    title: str = "Tau Session Export",
    source: str | None = None,
    system_prompt: str | None = None,
) -> Path:
    """Write a self-contained HTML session export and return its path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_session_html(
            entries,
            title=title,
            source=source,
            system_prompt=system_prompt,
        ),
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
    system_prompt: str | None = None,
) -> Path:
    """Write a session export in the requested or inferred format."""
    export_format = normalize_export_format(format or output_path.suffix.removeprefix("."))
    if export_format == "jsonl":
        return export_session_jsonl(entries, output_path)
    return export_session_html(
        entries,
        output_path,
        title=title,
        source=source,
        system_prompt=system_prompt,
    )


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


def _jsonl_filename(title: str, source: str | None) -> str:
    """Return the filename used by the in-page JSONL download button."""
    if source:
        stem = Path(source).stem
        if stem:
            return f"{stem}.jsonl"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{slug or 'tau-session'}.jsonl"


def _export_theme_css(theme: TuiTheme) -> str:
    """Map a built-in TUI theme to the export's semantic CSS variables."""
    return "\n".join(
        (
            f"      --bg: {theme.screen_background};",
            f"      --canvas: {theme.chrome_background};",
            f"      --surface: {theme.prompt_background};",
            f"      --surface-2: {theme.markdown_code_block_background};",
            f"      --text: {theme.screen_text};",
            f"      --bright: {theme.prompt_text};",
            f"      --muted: {theme.muted_text};",
            f"      --line: {theme.border};",
            f"      --line-strong: {theme.prompt_border};",
            f"      --accent: {theme.accent};",
            f"      --accent-text: {theme.highlight_text};",
            f"      --accent-soft: {theme.highlight_background};",
            f"      --danger: {theme.error};",
            f"      --code-bg: {theme.markdown_code_block_background};",
        )
    )


def render_session_html(
    entries: Sequence[SessionEntry],
    *,
    title: str = "Tau Session Export",
    source: str | None = None,
    system_prompt: str | None = None,
) -> str:
    """Render a session transcript/tree as standalone HTML."""
    entry_list = list(entries)
    active_leaf_id = _active_leaf_id(entry_list)
    active_path_ids = _active_path_ids(entry_list, active_leaf_id)
    visible_entries = _visible_entries(entry_list)
    tree_html = _render_tree(visible_entries, active_path_ids, active_leaf_id)
    details_html = _render_entry_details(visible_entries, active_path_ids, active_leaf_id)
    source_html = f'<p class="source">Source: <code>{_escape(source)}</code></p>' if source else ""
    system_prompt_html = _render_system_prompt(system_prompt)
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    jsonl_b64 = base64.b64encode(_session_jsonl_text(entry_list).encode("utf-8")).decode("ascii")
    jsonl_filename = _jsonl_filename(title, source)
    tool_count = sum(1 for entry in visible_entries if _entry_filter_kind(entry) == "tool")
    event_count = sum(1 for entry in visible_entries if _entry_filter_kind(entry) == "event")
    usage_html = render_usage_dashboard(
        collect_session_usage(
            [entry for entry in visible_entries if entry.id in active_path_ids]
            or list(visible_entries)
        )
    )
    light_theme_css = _export_theme_css(TAU_LIGHT_THEME)
    dark_theme_css = _export_theme_css(TAU_DARK_THEME)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
{light_theme_css}
      --mono: "JetBrains Mono", "SFMono-Regular", Consolas, Menlo, monospace;
      font-family: var(--mono);
    }}
    :root.theme-dark {{
      color-scheme: dark;
{dark_theme_css}
    }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]):not(.theme-light) {{
        color-scheme: dark;
{dark_theme_css}
      }}
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--bg); scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      line-height: 1.55;
      font-variant-numeric: tabular-nums;
    }}
    ::selection {{ background: var(--accent); color: var(--bg); }}
    header {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 34px clamp(16px, 4vw, 38px) 0;
    }}
    h1, h2, h3, h4 {{ margin: 0; line-height: 1.25; }}
    h1 {{
      margin: 8px 0 10px;
      color: var(--bright);
      font-size: 24px;
      font-weight: 600;
    }}
    h2 {{
      color: var(--muted);
      font-size: 0.68rem;
      font-weight: 500;
      letter-spacing: 0.12em;
      margin-bottom: 10px;
      text-transform: uppercase;
    }}
    h4 {{
      font-size: 0.66rem;
      font-weight: 500;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin: 12px 0 4px;
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
      padding: 9px 12px;
      margin: 6px 0 0;
      font-size: 0.82rem;
      line-height: 1.5;
    }}
    ul {{ margin: 4px 0 0; padding-left: 20px; }}
    .header-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 0.74rem;
      font-weight: 600;
      margin: 0;
      text-transform: lowercase;
    }}
    .eyebrow::before {{ content: "$ "; color: var(--muted); }}
    .theme-toggle {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      padding: 0;
      color: var(--muted);
      background: var(--surface);
      border: 1px solid var(--line-strong);
      border-radius: 50%;
      cursor: pointer;
      transition: color .15s, border-color .15s;
    }}
    .theme-toggle:hover {{ color: var(--accent); border-color: var(--accent); }}
    .theme-toggle .icon {{ width: 14px; height: 14px; }}
    .theme-toggle .theme-icon-dark {{ display: none; }}
    :root.theme-dark .theme-toggle .theme-icon-light {{ display: none; }}
    :root.theme-dark .theme-toggle .theme-icon-dark {{ display: inline-block; }}
    :root[data-theme="dark"] .theme-toggle .theme-icon-light {{ display: none; }}
    :root[data-theme="dark"] .theme-toggle .theme-icon-dark {{ display: inline-block; }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) .theme-toggle .theme-icon-light {{ display: none; }}
      :root:not([data-theme="light"]) .theme-toggle .theme-icon-dark {{ display: inline-block; }}
    }}
    .source, .generated {{
      margin: 0;
      color: var(--muted);
      font-size: 0.72rem;
      overflow-wrap: anywhere;
    }}
    .source::before {{ content: "# "; color: var(--accent); }}
    .generated::before {{ content: "# "; color: var(--accent); }}
    .export-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 4px 18px;
      margin-top: 8px;
    }}
    details.system-prompt {{
      margin-top: 16px;
      background: var(--surface);
      border: 1px solid var(--line);
    }}
    .system-prompt-summary {{
      display: flex;
      align-items: baseline;
      gap: 10px;
      padding: 8px 12px;
      cursor: pointer;
      font-family: var(--mono);
      font-size: 0.78rem;
      font-weight: 600;
    }}
    .system-prompt-warning {{
      color: var(--muted);
      font-size: 0.68rem;
      font-weight: 400;
    }}
    .system-prompt-body {{ padding: 0 12px 12px; }}
    .system-prompt-body pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    [hidden] {{ display: none !important; }}
    .tab-bar {{
      display: flex;
      gap: 4px;
      margin-top: 16px;
      border-bottom: 1px solid var(--line);
    }}
    .tab {{
      padding: 7px 14px;
      color: var(--muted);
      background: var(--surface);
      border: 1px solid var(--line);
      border-bottom: 0;
      border-radius: 0;
      cursor: pointer;
      font-family: var(--mono);
      font-size: 0.68rem;
      font-weight: 600;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      user-select: none;
      transition: color .15s, background .15s;
    }}
    .tab:hover {{ color: var(--bright); background: var(--surface-2); }}
    .tab[aria-selected="true"] {{
      color: var(--accent);
      background: var(--bg);
      border-bottom-color: var(--bg);
    }}
    .tab:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
    .usage-shell {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 22px clamp(16px, 4vw, 38px) 60px;
    }}
{USAGE_STYLES}
    .filter-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      margin-top: 16px;
      padding: 8px 12px;
      background: var(--surface);
      border: 1px dashed var(--line);
      font-family: var(--mono);
    }}
    .filter-label {{
      color: var(--muted);
      font-size: 0.64rem;
      font-weight: 500;
      letter-spacing: 0.12em;
      padding: 0 4px 0 2px;
      text-transform: uppercase;
    }}
    .chip {{ position: relative; display: inline-flex; }}
    .chip input {{
      position: absolute;
      inset: 0;
      margin: 0;
      opacity: 0;
      cursor: pointer;
    }}
    .chip-label {{
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 5px 12px;
      color: var(--muted);
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 0;
      font-size: 0.76rem;
      font-weight: 500;
      transition: color .15s, border-color .15s, background .15s;
    }}
    .chip-label:hover {{
      color: var(--bright);
      border-color: var(--line-strong);
    }}
    .chip-label::before {{
      content: "";
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--line-strong);
      transition: background .15s;
    }}
    .chip input:checked + .chip-label {{
      color: var(--accent-text);
      background: var(--accent-soft);
      border-color: var(--accent);
    }}
    .chip input:checked + .chip-label::before {{ background: var(--accent-text); }}
    .chip input:focus-visible + .chip-label {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .chip-count {{
      padding: 0 6px;
      background: var(--surface-2);
      font-size: 0.66rem;
      font-variant-numeric: tabular-nums;
    }}
    .chip input:checked + .chip-label .chip-count {{ background: var(--accent-soft); }}
    .filter-spacer {{ flex: 1 1 auto; }}
    .jsonl-download {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 12px;
      color: var(--muted);
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 6px;
      font-family: var(--mono);
      font-size: 0.76rem;
      font-weight: 500;
      cursor: pointer;
      transition: color .15s, border-color .15s, background .15s;
    }}
    .jsonl-download:hover {{
      color: var(--bright);
      background: var(--surface-2);
      border-color: var(--line-strong);
    }}
    .jsonl-download .icon {{ width: 12px; height: 12px; }}
    .expand-toggle {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 12px;
      color: var(--accent);
      background: var(--surface);
      border: 1px solid var(--accent);
      border-radius: 6px;
      font-family: var(--mono);
      font-size: 0.76rem;
      font-weight: 500;
      cursor: pointer;
      transition: color .15s, border-color .15s, background .15s;
    }}
    .expand-toggle:hover {{
      color: var(--accent-text);
      background: var(--accent-soft);
      border-color: var(--accent);
    }}

    main {{
      display: grid;
      grid-template-columns: minmax(220px, 300px) minmax(0, 1fr);
      gap: 28px;
      max-width: 1240px;
      margin: 0 auto;
      padding: 18px clamp(16px, 4vw, 38px) 60px;
    }}
    aside {{
      position: sticky;
      top: 14px;
      align-self: start;
      max-height: calc(100vh - 28px);
      overflow: auto;
      padding: 2px 14px 4px 0;
      border-right: 1px solid var(--line);
    }}
    .icon {{
      display: inline-block;
      flex: 0 0 auto;
      width: 14px;
      height: 14px;
      color: var(--muted);
    }}
    .icon svg {{ display: block; width: 100%; height: 100%; }}
    .tree {{
      list-style: none;
      margin: 0;
      padding-left: 0;
    }}
    .tree .tree {{
      margin-left: 8px;
      padding-left: 12px;
      border-left: 1px solid var(--line);
    }}
    .tree li {{ margin: 1px 0; }}
    .node-link {{
      display: flex;
      align-items: center;
      gap: 7px;
      color: var(--text);
      text-decoration: none;
      padding: 4px 8px;
    }}
    .node-link:hover {{ background: var(--surface-2); }}
    .active-path > .node-link {{ color: var(--accent); }}
    .active-leaf > .node-link {{
      background: var(--accent-soft);
      color: var(--bright);
      font-weight: 500;
    }}
    :root.theme-dark .active-leaf > .node-link {{ color: var(--accent); }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]):not(.theme-light) .active-leaf > .node-link {{
        color: var(--accent);
      }}
    }}
    .node-link .icon {{ color: var(--muted); }}
    .active-path > .node-link .icon {{ color: var(--accent); }}
    .node-type {{
      display: block;
      font-family: var(--mono);
      font-size: 0.75rem;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    details.entry {{
      margin: 0 0 8px;
      background: var(--surface);
      border: 1px solid var(--line);
      scroll-margin-top: 14px;
    }}
    details.entry.active-entry {{ background: var(--surface-2); }}
    details.entry.is-error {{ border-color: var(--danger); }}
    details.entry.is-error .entry-title {{ color: var(--danger); }}
    .entry-summary {{
      display: flex;
      align-items: center;
      gap: 9px;
      padding: 8px 14px 8px 12px;
      cursor: pointer;
      list-style: none;
      font-family: var(--mono);
      user-select: none;
    }}
    .entry-summary::-webkit-details-marker {{ display: none; }}
    .entry-summary::before {{
      content: "";
      flex: 0 0 auto;
      width: 6px;
      height: 6px;
      border-right: 1.5px solid var(--muted);
      border-bottom: 1.5px solid var(--muted);
      transform: rotate(-45deg);
      transition: transform .15s;
    }}
    details.entry[open] > .entry-summary::before {{ transform: rotate(45deg); }}
    .entry-summary:hover {{
      background: var(--accent-soft);
      color: var(--accent-text);
    }}
    .entry-heading {{
      display: flex;
      align-items: baseline;
      gap: 9px;
      min-width: 0;
      flex: 1 1 auto;
    }}
    .entry-title {{
      flex: 0 0 auto;
      font-size: 0.78rem;
      font-weight: 600;
      letter-spacing: 0.01em;
      white-space: nowrap;
    }}
    .entry-preview {{
      min-width: 0;
      overflow: hidden;
      color: var(--muted);
      font-size: 0.74rem;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    details.entry[open] > .entry-summary .entry-preview {{ display: none; }}
    .entry-side {{
      display: flex;
      align-items: center;
      flex: 0 0 auto;
      gap: 10px;
    }}
    .entry-status {{
      color: var(--accent);
      font-size: 0.62rem;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .entry-time {{
      color: var(--muted);
      font-size: 0.68rem;
      font-variant-numeric: tabular-nums;
    }}
    .entry-body {{
      padding: 10px 14px 12px 33px;
      border-top: 1px solid var(--line);
    }}
    .entry-body > p {{
      font-size: 0.74rem;
      margin: 2px 0;
    }}
    .entry-meta-line {{
      display: flex;
      flex-wrap: wrap;
      gap: 3px 14px;
      margin: 0 0 6px;
      color: var(--muted);
      font-family: var(--mono);
      font-size: 0.68rem;
    }}
    .entry-meta-line a {{ color: var(--accent); text-decoration: none; }}
    .entry-meta-line a:hover {{ text-decoration: underline; }}
    .entry-meta-line .error-flag {{
      color: var(--danger);
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    details.block {{
      margin-top: 6px;
      background: var(--surface);
      border: 1px solid var(--line);
    }}
    details.entry.active-entry details.block {{ background: var(--surface); }}
    .block-summary {{
      display: flex;
      align-items: center;
      gap: 7px;
      padding: 5px 10px;
      color: var(--muted);
      cursor: pointer;
      list-style: none;
      font-family: var(--mono);
      font-size: 0.74rem;
      font-weight: 500;
      user-select: none;
    }}
    .block-summary::-webkit-details-marker {{ display: none; }}
    .block-summary::before {{
      content: "";
      flex: 0 0 auto;
      width: 5px;
      height: 5px;
      border-right: 1.5px solid var(--muted);
      border-bottom: 1.5px solid var(--muted);
      transform: rotate(-45deg);
      transition: transform .15s;
    }}
    details.block[open] > .block-summary::before {{ transform: rotate(45deg); }}
    .block-summary:hover {{ color: var(--accent); }}
    .block-summary .icon {{ width: 12px; height: 12px; }}
    .block-hint {{
      margin-left: auto;
      font-size: 0.64rem;
      font-weight: 400;
      letter-spacing: 0.04em;
      opacity: 0.75;
    }}
    .block-body {{ padding: 2px 10px 10px; }}
    .block-body > :first-child {{ margin-top: 0; }}
    .call-id {{
      color: var(--muted);
      font-family: var(--mono);
      font-size: 0.68rem;
    }}
    .empty {{
      color: var(--muted);
      font-style: italic;
    }}
    .hide-tools details.entry[data-entry-kind="tool"] {{ display: none; }}
    .hide-tools .tree-node[data-entry-kind="tool"] > .node-link {{ display: none; }}
    .hide-tools .tool-content {{ display: none; }}
    .messages-only details.entry[data-entry-kind="event"] {{ display: none; }}
    .messages-only .tree-node[data-entry-kind="event"] > .node-link {{ display: none; }}
    pre.highlight {{ padding: 9px 12px; }}
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
    :root.theme-dark .highlight .s2,
    :root.theme-dark .highlight .s1 {{ color: #7fd08a; }}
    :root.theme-dark .highlight .mi,
    :root.theme-dark .highlight .mf {{ color: #e0a95e; }}
    :root.theme-dark .highlight .kc {{ color: #e58fc0; }}
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
        padding: 2px 0 16px;
      }}
      .entry-preview {{ display: none; }}
      .entry-body {{ padding-left: 14px; }}
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
    {system_prompt_html}
    <nav class="tab-bar" role="tablist" aria-label="Export views">
      <button
        type="button"
        class="tab"
        id="tab-transcript"
        role="tab"
        aria-controls="panel-transcript"
        aria-selected="true"
        tabindex="0"
      >
        Transcript
      </button>
      <button
        type="button"
        class="tab"
        id="tab-usage"
        role="tab"
        aria-controls="panel-usage"
        aria-selected="false"
        tabindex="-1"
      >
        Usage
      </button>
    </nav>
    <div class="filter-bar" id="filterBar" aria-label="Transcript filters">
      <span class="filter-label">View</span>
      <label class="chip">
        <input type="checkbox" id="showTools" checked>
        <span class="chip-label">Tools <span class="chip-count">{tool_count}</span></span>
      </label>
      <label class="chip">
        <input type="checkbox" id="showEvents" checked>
        <span class="chip-label">Events <span class="chip-count">{event_count}</span></span>
      </label>
      <span class="filter-spacer"></span>
      <button
        type="button"
        class="expand-toggle"
        id="accordionToggle"
        aria-pressed="false"
      >
        Expand all
      </button>
      <button
        type="button"
        class="jsonl-download"
        id="downloadJsonl"
        title="Download the session as a JSONL file"
        aria-label="Download the session as a JSONL file"
      >
        <span class="icon">{_ICON_DOWNLOAD}</span>JSONL
      </button>
    </div>
  </header>
  <main class="session-shell"
    id="panel-transcript"
    role="tabpanel"
    aria-labelledby="tab-transcript"
  >
    <aside class="tree-rail">
      <h2>Session</h2>
      {tree_html}
    </aside>
    <section class="entry-stream" aria-label="Session entries">
      <h2>Transcript</h2>
      {details_html}
    </section>
  </main>
  <section
    class="usage-shell"
    id="panel-usage"
    role="tabpanel"
    aria-labelledby="tab-usage"
    hidden
  >
    {usage_html}
  </section>
  <script id="sessionJsonlData" type="application/octet-stream">{jsonl_b64}</script>
  <script>
    (function () {{
      var root = document.documentElement;
      var stored = null;
      try {{
        stored = window.localStorage.getItem("tau-session-export-theme");
      }} catch (err) {{
        stored = null;
      }}
      function fireThemeChange() {{
        var event;
        try {{
          event = new CustomEvent("tau-themechange");
        }} catch (err) {{
          event = document.createEvent("Event");
          event.initEvent("tau-themechange", false, false);
        }}
        window.dispatchEvent(event);
      }}
      function applyTheme(theme) {{
        root.classList.toggle("theme-dark", theme === "dark");
        root.classList.toggle("theme-light", theme === "light");
        if (theme === "dark") {{
          root.setAttribute("data-theme", "dark");
        }} else if (theme === "light") {{
          root.setAttribute("data-theme", "light");
        }} else {{
          root.removeAttribute("data-theme");
        }}
        fireThemeChange();
      }}
      function currentTheme() {{
        var explicit = root.getAttribute("data-theme");
        if (explicit === "light" || explicit === "dark") {{
          return explicit;
        }}
        var prefersDark =
          window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        return prefersDark ? "dark" : "light";
      }}
      if (stored === "light" || stored === "dark") {{
        root.setAttribute("data-theme", stored);
      }}
      applyTheme(currentTheme());
      if (window.matchMedia) {{
        var themeQuery = window.matchMedia("(prefers-color-scheme: dark)");
        var onSchemeChange = function () {{
          if (!root.getAttribute("data-theme")) {{
            applyTheme(currentTheme());
          }}
        }};
        if (themeQuery.addEventListener) {{
          themeQuery.addEventListener("change", onSchemeChange);
        }} else if (themeQuery.addListener) {{
          themeQuery.addListener(onSchemeChange);
        }}
      }}
      var toggle = document.getElementById("themeToggle");
      if (toggle) {{
        toggle.addEventListener("click", function () {{
          var next = currentTheme() === "dark" ? "light" : "dark";
          applyTheme(next);
          try {{
            window.localStorage.setItem("tau-session-export-theme", next);
          }} catch (err) {{
            /* localStorage unavailable; theme choice just won't persist. */
          }}
        }});
      }}

      var showTools = document.getElementById("showTools");
      var showEvents = document.getElementById("showEvents");
      var accordionToggle = document.getElementById("accordionToggle");
      function applyFilters() {{
        root.classList.toggle("hide-tools", !showTools.checked);
        root.classList.toggle("messages-only", !showEvents.checked);
      }}
      showTools.addEventListener("change", applyFilters);
      showEvents.addEventListener("change", applyFilters);

      var downloadJsonl = document.getElementById("downloadJsonl");
      if (downloadJsonl) {{
        downloadJsonl.addEventListener("click", function () {{
          var encoded = document.getElementById("sessionJsonlData").textContent.trim();
          var binary = window.atob(encoded);
          var bytes = new Uint8Array(binary.length);
          for (var i = 0; i < binary.length; i++) {{
            bytes[i] = binary.charCodeAt(i);
          }}
          var blob = new Blob([bytes], {{ type: "application/jsonl" }});
          var url = URL.createObjectURL(blob);
          var link = document.createElement("a");
          link.href = url;
          link.download = "{_attr(jsonl_filename)}";
          document.body.appendChild(link);
          link.click();
          link.remove();
          URL.revokeObjectURL(url);
        }});
      }}

      function transcriptDetails() {{
        return document.querySelectorAll(".entry-stream details");
      }}
      function syncAccordionToggle() {{
        var anyClosed = Array.prototype.some.call(transcriptDetails(), function (item) {{
          return !item.open;
        }});
        accordionToggle.textContent = anyClosed ? "Expand all" : "Collapse all";
        accordionToggle.setAttribute("aria-pressed", anyClosed ? "false" : "true");
      }}
      accordionToggle.addEventListener("click", function () {{
        var shouldOpen = Array.prototype.some.call(transcriptDetails(), function (item) {{
          return !item.open;
        }});
        Array.prototype.forEach.call(transcriptDetails(), function (item) {{
          item.open = shouldOpen;
        }});
        syncAccordionToggle();
      }});
      document.addEventListener("toggle", function (event) {{
        if (event.target.tagName === "DETAILS") {{
          syncAccordionToggle();
        }}
      }}, true);

      function openEntry(id) {{
        var target = document.getElementById(id);
        if (target && target.tagName === "DETAILS") {{
          target.open = true;
          syncAccordionToggle();
        }}
      }}
      document.querySelectorAll('a[href^="#entry-"]').forEach(function (link) {{
        link.addEventListener("click", function () {{
          openEntry(link.getAttribute("href").slice(1));
        }});
      }});
      if (window.location.hash.indexOf("#entry-") === 0) {{
        openEntry(window.location.hash.slice(1));
      }}
      applyFilters();
      syncAccordionToggle();

      var tabs = [
        document.getElementById("tab-transcript"),
        document.getElementById("tab-usage")
      ];
      var panels = [
        document.getElementById("panel-transcript"),
        document.getElementById("panel-usage")
      ];
      function selectTab(index, updateHash) {{
        tabs.forEach(function (tab, tabIndex) {{
          var selected = tabIndex === index;
          tab.setAttribute("aria-selected", String(selected));
          tab.setAttribute("tabindex", selected ? "0" : "-1");
          panels[tabIndex].hidden = !selected;
        }});
        document.getElementById("filterBar").hidden = index !== 0;
        var currentHash = window.location.hash;
        var tabHash = !currentHash || currentHash === "#transcript"
          || currentHash === "#usage" || currentHash === "#cache";
        if (updateHash && tabHash) {{
          try {{
            window.history.replaceState(null, "", index === 1 ? "#usage" : "#transcript");
          }} catch (err) {{ /* file:// pages may restrict history APIs. */ }}
        }}
      }}
      tabs.forEach(function (tab, index) {{
        tab.addEventListener("click", function () {{ selectTab(index, true); }});
        tab.addEventListener("keydown", function (event) {{
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {{
            return;
          }}
          event.preventDefault();
          var next = event.key === "ArrowRight" ? (index + 1) % tabs.length
            : (index + tabs.length - 1) % tabs.length;
          selectTab(next, true);
          tabs[next].focus();
        }});
      }});
      // Preserve transcript entry deep links. Only tab hashes select a panel.
      selectTab(
        window.location.hash === "#usage" || window.location.hash === "#cache" ? 1 : 0,
        false
      );
    }})();
  </script>
  <script>{USAGE_SCRIPT}</script>
</body>
</html>
"""


def _render_system_prompt(system_prompt: str | None) -> str:
    """Render live request configuration separately from transcript entries."""
    if system_prompt is None:
        return ""
    return (
        '<details class="system-prompt">'
        '<summary class="system-prompt-summary">System Prompt'
        '<span class="system-prompt-warning">May include project instructions</span>'
        "</summary>"
        f'<div class="system-prompt-body"><pre>{_escape(system_prompt)}</pre></div>'
        "</details>"
    )


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
    label = _entry_tree_label(entry)
    return (
        f'<li class="{" ".join(c for c in classes if c)}" '
        f'data-entry-kind="{_entry_filter_kind(entry)}">'
        f'<a class="node-link" href="#entry-{_attr(entry.id)}" '
        f'aria-label="{_attr(label)}">'
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
        return '<p class="empty">No session entries were found.</p>'

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
    classes = ["entry"]
    status_bits = []
    if entry.id in active_path_ids:
        status_bits.append("active path")
    if entry.id == active_leaf_id:
        status_bits.append("active leaf")
    if status_bits:
        classes.append("active-entry")
    if _entry_is_error(entry):
        classes.append("is-error")
    status_html = (
        f'<span class="entry-status">{_escape(" · ".join(status_bits))}</span>'
        if status_bits
        else ""
    )
    body = _render_entry_body(entry)
    timestamp = _format_timestamp(entry.timestamp)
    preview = _entry_preview(entry)
    preview_html = f'<span class="entry-preview">{_escape(preview)}</span>' if preview else ""
    return (
        f'<details id="entry-{_attr(entry.id)}" class="{" ".join(classes)}" '
        f'data-entry-kind="{_entry_filter_kind(entry)}">'
        '<summary class="entry-summary">'
        f'<span class="icon">{_entry_icon(entry)}</span>'
        '<span class="entry-heading">'
        f'<span class="entry-title">{index:02d} · {_escape(_entry_title(entry))}</span>'
        f"{preview_html}"
        "</span>"
        f'<span class="entry-side">{status_html}'
        f'<time class="entry-time" datetime="{_attr(timestamp)}">'
        f"{_escape(_format_time_short(entry.timestamp))}</time></span>"
        "</summary>"
        '<div class="entry-body">'
        '<p class="entry-meta-line">'
        f"<span>id <code>{_escape(entry.id)}</code></span>"
        f"<span>parent {_entry_parent_html(entry)}</span>"
        f"<span>{_escape(timestamp)}</span>"
        "</p>"
        f"{body}"
        "</div>"
        "</details>"
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
        return _render_block(
            f"Data · <code>{_escape(entry.namespace)}</code>",
            _render_json_block(entry.data),
            hint=f"{len(entry.data)} field(s)",
        )
    return f"<pre>{_escape(entry.model_dump_json(indent=2))}</pre>"


def _render_message_entry(entry: MessageEntry) -> str:
    message = entry.message
    if isinstance(message, UserMessage):
        return f"<pre>{_escape(message.text)}</pre>"
    if isinstance(message, AssistantMessage):
        blocks: list[str] = []
        if message.response_provider:
            blocks.append(
                '<p class="entry-meta-line">'
                f"{_escape(message.model)}"
                ' <span class="dim">\u2192</span> '
                f"{_escape(message.response_provider)}"
                "</p>"
            )
        for block in message.content:
            if isinstance(block, ThinkingContent):
                blocks.append(_render_block("Thinking", f"<pre>{_escape(block.thinking)}</pre>"))
            elif isinstance(block, TextContent):
                blocks.append(f"<pre>{_escape(block.text)}</pre>")
            elif isinstance(block, ToolCall):
                hint = "arguments" if block.arguments else ""
                call_body = (
                    f'<p class="call-id">call id <code>{_escape(block.id)}</code></p>'
                    f"{_render_json_block(block.arguments) if block.arguments else ''}"
                )
                blocks.append(
                    _render_block(
                        f"Tool: <strong>{_escape(block.name)}</strong>",
                        call_body,
                        hint=hint,
                        extra_classes="tool-call tool-content",
                        icon=_ICON_TOOL,
                    )
                )
        return "".join(blocks) or "<pre>(no assistant text)</pre>"
    if isinstance(message, ToolResultMessage):
        status = '<span class="error-flag">error</span>' if message.is_error else "<span>ok</span>"
        result_body = (
            '<p class="entry-meta-line">'
            f"<span>tool_call_id <code>{_escape(message.tool_call_id)}</code></span>"
            f"{status}"
            "</p>"
            f"<pre>{_escape(message.text)}</pre>"
        )
        if isinstance(message.details, dict):
            result_body += _render_block("Result details", _render_json_block(message.details))
        return result_body
    return f"<pre>{_escape(entry.model_dump_json(indent=2))}</pre>"


def _render_block(
    title: str,
    body: str,
    *,
    hint: str = "",
    extra_classes: str = "",
    icon: str = "",
) -> str:
    """Render a collapsible content block inside an entry body."""
    classes = f"block {extra_classes}".strip()
    hint_html = f'<span class="block-hint">{_escape(hint)}</span>' if hint else ""
    icon_html = f'<span class="icon">{icon}</span>' if icon else ""
    return (
        f'<details class="{_attr(classes)}">'
        f'<summary class="block-summary">{icon_html}<span>{title}</span>{hint_html}</summary>'
        f'<div class="block-body">{body}</div>'
        "</details>"
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
# Claw-hammer icon adapted from Lucide (https://lucide.dev, ISC license).
_ICON_TOOL = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="m15 12-8.373 8.373a1 1 0 1 1-3-3L12 9"/>'
    '<path d="m18 15 4-4"/>'
    '<path d="m21.5 11.5-1.914-1.914A2 2 0 0 1 19 8.172V7l-2.26-2.26a6 6 0 0'
    " 0-4.202-1.756L9 2.96l.92.82A6.18 6.18 0 0 1 12 8.4V10l2 2h1.172a2 2 0 0 1"
    ' 1.414.586L18.5 14.5"/>'
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
_ICON_DOWNLOAD = (
    '<svg viewBox="0 0 16 16" aria-hidden="true">'
    '<path d="M8 2.5v7M5.3 6.8 8 9.5l2.7-2.7" fill="none" stroke="currentColor"'
    ' stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M2.8 11v1.7c0 .8.7 1.5 1.5 1.5h7.4c.8 0 1.5-.7 1.5-1.5V11" fill="none"'
    ' stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>'
    "</svg>"
)


def _entry_icon(entry: SessionEntry) -> str:
    if isinstance(entry, MessageEntry):
        message = entry.message
        if isinstance(message, UserMessage):
            return _ICON_USER
        if isinstance(message, AssistantMessage):
            return _ICON_TOOL if _is_tool_only_assistant(message) else _ICON_ASSISTANT
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
        message = entry.message
        if isinstance(message, UserMessage):
            return "User"
        if isinstance(message, AssistantMessage):
            return "Assistant"
        if isinstance(message, ToolResultMessage):
            return f"Tool: {message.tool_name}"
        return message.role
    if isinstance(entry, ModelChangeEntry):
        return "Model change"
    if isinstance(entry, ThinkingLevelChangeEntry):
        return "Thinking level change"
    if isinstance(entry, CompactionEntry):
        return "Compaction"
    if isinstance(entry, BranchSummaryEntry):
        return "Branch summary"
    if isinstance(entry, LabelEntry):
        return "Label"
    if isinstance(entry, LeafEntry):
        return "Leaf pointer"
    if isinstance(entry, SessionInfoEntry):
        return "Session info"
    if isinstance(entry, CustomEntry):
        return f"Custom: {entry.namespace}"
    return entry.type


def _entry_preview(entry: SessionEntry) -> str:
    """Return a short one-line preview shown on the collapsed entry row."""
    if isinstance(entry, MessageEntry):
        message = entry.message
        if isinstance(message, ToolResultMessage):
            return _summarize_text(message.text)
        if isinstance(message, AssistantMessage) and _is_tool_only_assistant(message):
            return ", ".join(call.name for call in message.tool_calls)
        return _summarize_text(message_text(message))
    if isinstance(entry, ModelChangeEntry):
        return entry.model
    if isinstance(entry, ThinkingLevelChangeEntry):
        return entry.thinking_level or "off"
    if isinstance(entry, CompactionEntry | BranchSummaryEntry):
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


def _entry_tree_label(entry: SessionEntry) -> str:
    """Return the sidebar label: just the tool name for tool entries."""
    if isinstance(entry, MessageEntry):
        message = entry.message
        if isinstance(message, ToolResultMessage):
            return message.tool_name
        if _is_tool_only_assistant(message):
            return ", ".join(call.name for call in message.tool_calls)
        title = _entry_title(entry).lower()
        summary = _summarize_text(message_text(message))
        return f"{title}: {summary}" if summary else title
    title = _entry_title(entry).lower()
    summary = _entry_preview(entry)
    return f"{title}: {summary}" if summary else title


def _is_tool_only_assistant(message: object) -> TypeGuard[AssistantMessage]:
    """Whether an assistant message carries only tool calls (no text/thinking)."""
    return (
        isinstance(message, AssistantMessage)
        and bool(message.tool_calls)
        and not any(
            (isinstance(block, TextContent) and block.text.strip())
            or (isinstance(block, ThinkingContent) and block.thinking.strip())
            for block in message.content
        )
    )


def _entry_is_error(entry: SessionEntry) -> bool:
    return (
        isinstance(entry, MessageEntry)
        and isinstance(entry.message, ToolResultMessage)
        and entry.message.is_error
    )


def _entry_filter_kind(entry: SessionEntry) -> str:
    if isinstance(entry, MessageEntry):
        message = entry.message
        if isinstance(message, ToolResultMessage):
            return "tool"
        if _is_tool_only_assistant(message):
            return "tool"
        return "message"
    return "event"


def _summarize_text(text: str, *, limit: int = 110) -> str:
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


def _format_time_short(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%H:%M:%S")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def _attr(value: object) -> str:
    return html.escape(str(value), quote=True)
