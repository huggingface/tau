---
title: Sessions
description: Resume past conversations, branch from any point in history, rename sessions, and export them.
---

Every Tau conversation is a **session**, saved to disk so you can come back to
it. Sessions are stored as append-only JSONL under `~/.tau/sessions/`, organized
per working directory, so resume flows focus on the project you're in.

## Listing sessions

```bash
tau sessions
```

Each row shows the session id, title, model, and working directory.

## Resuming

From the shell:

```bash
tau --session <session-id>
```

From inside the TUI:

```text
/resume            # open a picker of past sessions
/resume <id>       # resume a specific session
```

The `/resume` picker has a search field that filters by session name or model.
Start typing to narrow the list, then use the arrow keys and Enter (or click) to
pick a session.

To deliberately start fresh instead of resuming, use `tau --new-session` (or
`/new` in the TUI).

When you quit the TUI and the session was persisted, Tau prints a reminder of
the exact command to resume it:

```text
To resume this session: tau --session <session-id>
```

## Branching from history (`/tree`)

A session is a *tree*, not just a line — so you can go back and try a different
path without losing what you had.

Run `/tree` to open the session tree, then select an earlier entry:

- **Enter** — continue from that point, preserving the existing branch.
- **S** — ask the active model for a structured summary of the messages you're
  leaving behind before moving the active point.
- **C** — provide custom focus instructions for that one summary.

If a summary request fails, Tau falls back to a deterministic summary.

## Renaming

New sessions are automatically given a short name from the first message when
Tau can generate one. Tau shows the confirmed message first—including the
expanded text from a prompt-template slash command—then performs naming without
holding up that transcript update. The name appears anywhere session names are
already shown, including the `/resume` picker and id completions.

Auto-naming makes one high-level provider request. The provider adapter may retry
transient failures according to its configured `max_retries`. If those attempts
are exhausted, or the response is not a usable title, Tau does not start another
naming request: the session continues normally and uses a short local fallback
when possible.

```text
/name My refactor session
```

Use `/name` at any time to manually override the automatic name. Tau will not
replace a name you set yourself.

## Exporting

Export a session to a shareable file:

```text
/export                              # HTML, into the current directory
/export --format jsonl               # raw JSONL
/export --format html report.html    # explicit destination
```

Or from the shell:

```bash
tau export <session-id>                     # HTML (default)
tau export <session-id> session.html
tau export <session-id> --format jsonl
```

The source can be an indexed session id **or** a path to a JSONL session file.
HTML exports are self-contained and include two tabs: **Transcript** preserves
the session tree and entries in storage order, while **Cache** summarizes the
active branch's model requests, prompt caching, output and reasoning tokens,
estimated API-rate cost, tool calls, and compactions. Cache charts are
interactive—hover for exact values and select a legend item to hide a
series—and can be downloaded as static PNG images with white backgrounds. The
export follows Tau's themes: tau-light in light mode and tau-dark in dark mode,
with charts recoloring live when you toggle the theme. When `/export` creates
HTML from the live session, it also includes the current
system prompt in a separate, collapsed **System Prompt** section. Review that
section before sharing: the prompt may expose project instructions, skill
guidance, paths, or other local context. Offline
`tau export` of an indexed session or arbitrary JSONL file omits this section
because session JSONL does not persist the prompt.

The system prompt is display-only export metadata, not a transcript entry.
Direct JSONL exports and JSONL downloaded from the HTML remain entry-only and do
not contain it.

Every transcript entry is a compact accordion row
(icon, title, one-line preview, timestamp) that expands to reveal the full
content; thinking blocks, tool-call arguments, and tool-result details are
nested accordions. The export header includes controls to:

- show or hide tool calls and tool results in both the transcript and session
  tree—the chip filters show how many entries of each kind the session contains
- expand or collapse every accordion in the transcript with one button
- hide session events—such as session info, model and thinking changes,
  compactions, labels, and custom entries—to focus on user and assistant messages
- download the session as a JSONL file—the complete entry data is embedded in
  the page, so the download works offline and includes every entry

Tool rows are titled `Tool: <name>` (for example, `Tool: read`), and the
session tree labels tool entries with just the tool name for readability.

## Where sessions live

```text
~/.tau/sessions/<cleaned-path>-<short-hash>/
```

For example, `/Users/you/repos/tau` becomes something like
`repos-tau-a1b2c3`. The original JSONL is append-only — compaction and branching
change the *active* view, never the recorded history. See
[Configuration]({{< relref "../reference/configuration.md#sessions" >}}) for the exact layout.
