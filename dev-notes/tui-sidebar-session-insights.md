---
title: "TUI sidebar session insights"
---

## What changed

Tau's interactive TUI now uses the sidebar as the detailed session summary and
removes Textual's top header. The terminal tab title still follows the generated
or user-assigned session name, so removing the header recovers a row without
losing session identity.

The sidebar now shows:

- the session name
- user turns and assistant tool calls on the active branch
- cumulative provider-reported input and output tokens
- estimated cost when complete pricing is available
- automatic-compaction status and threshold
- context files, tools, skills, prompt templates, and loaded extensions

Provider, model, thinking level, and duplicate resource counts were removed from
the sidebar because the compact line below the prompt already presents the active
model state.

## Display choices

Tools, skills, prompts, and extensions are short names, so they render as wrapping
comma-separated lists. Context files remain bullets because paths are longer and
need clear boundaries. Paths inside the working directory are project-relative;
paths outside it are absolute so user-level instructions are unambiguous.

A compact divider separates each section without adding blank padding. This keeps
the expanded set of useful facts visually distinct while still fitting typical
terminal heights.

## Activity and usage semantics

Statistics come from original message entries on the active root-to-leaf branch,
not only from the compacted model context. Consequently, compaction does not erase
activity or billed usage. A turn is a user or extension-authored custom message.
A tool call is each tool-call block requested by an assistant message.

Input totals include fresh input, cache reads, and cache writes. Output totals use
the provider's reported output count. Cost is calculated per assistant response
from that response's provider/model metadata, including tiered rates and separate
input, output, cache-read, and cache-write prices. If any billed response lacks
pricing, Tau labels the branch cost unavailable rather than showing a misleading
partial estimate.

## Architecture

Lifetime aggregation lives in `tau_coding.session_stats`; it consumes durable
session entries and remains independent of Textual. `CodingSession` supplies the
active branch and resolves configured pricing. The TUI widget only formats the
result. This preserves Tau's boundary between session behavior and frontend
rendering.

## Verification

Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For manual verification, open a named TUI session in a wide terminal, run several
prompts that call tools, and confirm that activity, usage, and cost update. Run
`/compact` and verify that lifetime totals remain unchanged while the current
context indicator shrinks. Resize the terminal until the sidebar disappears and
confirm there is no top header and the terminal tab retains the session name.
