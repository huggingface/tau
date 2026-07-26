---
title: "TUI sidebar session insights"
---

## What changed

Tau's interactive TUI now uses the sidebar as the detailed session summary and
removes Textual's top header and shortcut footer. The terminal tab title still
follows the generated or user-assigned session name, so removing the chrome
recovers two rows without losing session identity or keyboard functionality.

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

Tools, prompts, and extensions are short names, so they render as wrapping
comma-separated lists. Skills and context files remain bullets so each loaded
instruction source has a clear row. Paths inside the working directory are
project-relative; paths outside it are absolute so user-level instructions are
unambiguous.

Spaced dividers separate each section. Section headings use the bright prompt
text color while values use the quieter metadata gray. The sidebar is wider and
borderless, uses a comfortable left content inset and the same theme variable as
the prompt field for its background, and hides on shorter terminals rather than
clipping the expanded content. The
versioned `τ = 2π` brand is a separate bottom-aligned widget, so it stays at the
lower edge regardless of content height.

## Activity and usage semantics

Statistics come from original message entries on the active root-to-leaf branch,
not only from the compacted model context. Consequently, compaction does not erase
activity or billed usage. A turn is a user or extension-authored custom message.
A tool call is each tool-call block requested by an assistant message.

Input totals include fresh input, cache reads, and cache writes. Output totals use
the provider's reported output count. Cost is calculated per assistant response
from that response's provider/model metadata, including tiered rates and separate
input, output, cache-read, and cache-write prices. If any billed response lacks
pricing, Tau displays `$N/A` rather than showing a misleading partial estimate.

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
confirm there is no top header or shortcut footer and the terminal tab retains
the session name. In a tall window, confirm the versioned Tau mark stays at the
bottom of the wider sidebar. The session name uses bold accent styling without a
redundant `session` section heading, so it stands apart from other values.

The compact status block below the prompt places `provider:model (thinking)` on
its first line and context consumption as only `used/limit` on its second. It
styles the parent portion of the working-directory path and Git branch as metadata
while keeping the directory basename prominent.
The prompt editor keeps only its left border; focus, shell-mode, and activity
colors update that edge without surrounding the input on all four sides. Vertical
padding replaces the removed top and bottom border space, preserving the original
block height and full background area. User transcript blocks share that prompt
background in every built-in theme, matching both the composer and sidebar. A
small vertical inset gives each submitted message a block silhouette instead of
making only its text line appear highlighted. Markdown blocks disable the default
resting underline and apply underline as a link-hover style; clickable spans remain
bounded to their exact link text so the decoration cannot run across the row.

Theme selection colors now have one source of truth: autocomplete derives its
selected-row foreground and background from the same `highlight_text` and
`highlight_background` values used by picker `ListView`s such as `/resume`. The
dark theme promotes its existing aqua highlight (`#a7f3f0`) to the global accent,
replacing the previous orange across headings, bullets, prompt activity, and other
accent-driven UI.
