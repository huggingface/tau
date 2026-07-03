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
tau --resume <session-id>
```

From inside the TUI:

```text
/resume            # open a picker of past sessions
/resume <id>       # resume a specific session
```

To deliberately start fresh instead of resuming, use `tau --new-session` (or
`/new` in the TUI).

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
Tau can generate one. The name appears anywhere session names are already shown,
including the `/resume` picker and id completions. If naming fails, the session
continues normally and Tau falls back to a short local name when possible.

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
HTML exports are self-contained and include the preserved session tree plus the
transcript in storage order.

## Where sessions live

```text
~/.tau/sessions/<cleaned-path>-<short-hash>/
```

For example, `/Users/you/repos/tau` becomes something like
`repos-tau-a1b2c3`. The original JSONL is append-only — compaction and branching
change the *active* view, never the recorded history. See
[Configuration]({{< relref "../reference/configuration.md#sessions" >}}) for the exact layout.
