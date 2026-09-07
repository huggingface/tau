---
title: "Phase 20.4: Session Export and Visualization"
---

Phase 20.4 adds a durable way to inspect Tau sessions outside the TUI.

## What was added

Tau can export any indexed session id or JSONL session file to a standalone
HTML document:

```bash
tau export <session-id>
tau export <session-id> session.html
tau export <session-id> --format jsonl
tau export ~/.tau/sessions/<project>/<session-id>.jsonl
```

When no destination is provided, `tau export` writes to the current working
directory instead of Tau's internal session storage directory. Interactive
sessions expose the same export flow through:

```text
/export [--format html|jsonl] [destination]
```

The export contains two coordinated views:

- a session tree that preserves parent-child relationships, branches, leaf
  pointers, and the active branch path
- a storage-order transcript/details view for messages, tool calls, tool
  results, compactions, labels, model changes, thinking changes, and custom
  entries

The export header also includes a compact summary of the visible session data:
total entries, messages, tools, session events, and elapsed duration. A
timestamp timeline groups message, tool, and event markers into separate lanes;
selecting a marker jumps to that entry. These summaries give readers an
immediate sense of the session before they inspect the tree or expand any
accordions. Because session entries currently record timestamps rather than
independent end times, the timeline shows event positions and session elapsed
time, not per-entry execution bars.

The generated file is self-contained HTML, CSS, and JavaScript, so it can be
opened without running Tau or the Textual app. Transcript entries render as
compact, collapsed accordion rows (icon, title, one-line preview, timestamp),
with thinking blocks, tool-call arguments, and result details nested as inner
accordions. Tool rows are titled `Tool: <name>`, and the session tree labels
tool entries with just the tool name for readability. Chip-style header
filters—with entry counts—can hide tool calls/results in both views or drop
non-message session events for a user/assistant-focused transcript, and a
single button expands or collapses every accordion at once. A download button
reproduces the JSONL export from the entry data embedded (base64-encoded) in
the page, so the HTML file alone round-trips the full session. The filters
change only the exported view; the complete session remains embedded in the
document.

## Why it exists

Tau sessions are append-only trees, not a single flat chat log. That matters for
future fork and branch workflows because multiple candidate branches can share
the same root. A plain transcript would hide that shape and make it hard to
debug replay, compaction, or branch selection.

The exporter keeps the visualization in `tau_coding` because it is an
application workflow over persisted session data. The reusable `tau_agent`
session models remain provider-neutral and frontend-neutral.

## How it maps to Pi

Pi has an HTML session export flow for inspecting conversation state outside the
interactive interface. Tau mirrors the core product behavior while keeping the
implementation smaller: the exporter renders static HTML from the existing
`SessionEntry` JSONL records instead of adding a separate client-side app.

## How to test it

Run the focused tests:

```bash
uv run pytest tests/test_session_export.py tests/test_cli.py -k export
```

Run the full gate before shipping:

```bash
uv run pytest
uv run ruff check src tests
uv run mypy
uv run --group docs mkdocs build --strict
```
