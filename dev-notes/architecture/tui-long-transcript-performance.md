---
title: "Bounded TUI Transcript Rendering"
---

Tau's Textual transcript now keeps a bounded window of message widgets mounted instead of
letting the Textual DOM grow with every message in a session.

## Why this exists

The durable conversation and TUI display state were already separate, but the original
`TranscriptView` mounted one Textual widget tree for every `ChatItem`. Long sessions therefore
made unrelated interactions expensive: Textual had to visit thousands of message pumps during
layout and refresh work even when the user was only typing in the prompt.

A full transcript refresh was more expensive still because it removed and recreated every
message widget. Tool completion, structured thinking responses, result visibility toggles, and
terminal resize could all reach that path.

## Architecture

The complete display projection remains in `TuiState.items`. `TranscriptView` now mounts only a
contiguous frontend window:

```text
CodingSession / durable messages
              ↓
TuiState.items (complete display history)
              ↓
TranscriptView window (bounded Textual DOM)
```

The latest 200 items are mounted initially. Small boundary rows indicate when earlier or later
items are outside the window. Reaching a boundary moves the window by a smaller page while
keeping an existing message as the scroll anchor. The state is never truncated, and session
persistence is unchanged.

This is deliberately a Textual adapter optimization. No windowing, rendering, or widget policy
was added to `tau_agent` or `CodingSession`.

## Incremental hot paths

Common event paths no longer rebuild transcript history:

- tool completion updates the existing tool row;
- terminal commands append and complete one row;
- final ordered thinking/text blocks replace only the provisional assistant tail;
- thinking and tool-result visibility update only affected mounted rows;
- terminal resize relies on native Textual reflow;
- transcript item and tool-call lookup use frontend indexes rather than repeated scans.

The prompt activity animation remains smooth, but fixed-size animation frames skip layout and
tool elapsed-time rows update at most once per second.

## Tradeoffs

Native Textual Markdown, selection, streaming, and per-message rendering remain intact for the
mounted window. Moving between windows clears widgets outside the viewport, so a mouse selection
cannot span a paging boundary. The complete transcript remains available by continuing to scroll,
and exports/session replay still use the full durable history.

## Validation

Automated tests cover:

- a bounded mounted-widget count with complete state retained;
- paging in both transcript directions;
- scroll anchoring and streaming behavior;
- incremental tool, structured assistant, result-toggle, and resize updates;
- throttled activity/timer layout work.

Run the project checks with:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
