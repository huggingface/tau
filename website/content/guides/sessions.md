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

The `/resume` picker separates projects and recent sessions into two columns.
The project column uses compact folder names; the selected project's full path
appears above the session column. It starts on the current project's sessions.
Press **Left** to move to the project column, use **Up/Down** to choose another
project, then press **Right** to return to its sessions. Press **Enter** (or
click) to resume one.

The search field filters session names and models within the selected project.

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
- **L** — create or edit a bookmark label on the highlighted entry. Submit an
  empty label to clear it.
- **Ctrl+F** — toggle a view containing only labeled entries.
- **Ctrl+L** — show or hide when each visible label was last changed.
- **Ctrl+T** — show or hide tool-call rows.

Labels render as `[label]` before the entry. They are per-entry bookmarks and
remain attached to their entry across branches; they do not rename the session.
Use `/name` for the separate session display title.

If a summary request fails, Tau falls back to a deterministic summary.

The active branch tip is the last session entry written. Plain **Enter**
navigation is in-memory only: quitting before another action and reopening will
return to the last non-legacy-leaf entry in file order. Your next message or
state change uses the selected point as its parent, making the new branch the
active persisted tip. **S** writes its branch summary immediately, so summarized
navigation survives a restart even before another message.

## Recovering older sessions

Older Tau versions could leave malformed tool-call history when a run was
interrupted. Providers reject that history, so every prompt in the resumed
session could fail with a 400 error about a missing tool call or tool output.

Tau validates the active branch during resume and after `/tree` navigation. It
repairs missing, misplaced, duplicate, or orphaned tool results by appending a
provider-safe branch while preserving the original JSONL entries. A durable
session diagnostic records what changed. Repeating resume is idempotent and does
not append another repair when history is already valid.

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

Names and renames are appended to the session JSONL as `session_info` entries
with a `name` field. The latest naming entry in file order wins across all
branches, so copying or exporting the transcript preserves its name without
the original project index. Naming does not add a message to the model context
or rewrite earlier entries.

The project index still caches names for fast `/resume` listings. Tau updates
that cache after a rename and repairs a stale name when loading the session.
Older sessions remain readable: without a naming entry, Tau uses the legacy
index title first, then the original `session_info.title`. For an old name stored
only in the index, run `/name` with that same name before copying the transcript
to persist it in the new format. Simply opening an old session does not rewrite
its history.

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
After a successful `/export`, Tau shows the destination in the TUI transcript.
This status is display-only: it is not saved to session history or sent to the
model as context.

HTML exports are self-contained and include two tabs: **Transcript** preserves
the session tree and entries in storage order, while **Cache** summarizes the
active branch's model requests, including the requests that generate compaction
and branch summaries, prompt caching, output and reasoning tokens, estimated
API-rate cost, tool calls, and compactions. Summary-generation requests are
labeled separately in the request table rather than blended into assistant turns.
Cache charts are
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

New compaction entries store a `first_kept_entry_id` boundary: replay inserts the
summary, then keeps that active-path entry and everything after it. This is a fixed-size,
Pi-compatible replacement for older Tau files' `replaces_entry_ids` arrays. Older arrays
remain readable and take precedence during replay, so exporting or resuming a legacy
session does not change its message history. HTML entry details show the first-kept
boundary for modern compactions and identify unavailable legacy boundaries.

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
  the page, so the download works offline and includes every entry (including
  historical `leaf` records from older Tau versions)

Tool rows are titled `Tool: <name>` (for example, `Tool: read`), and the
session tree labels tool entries with just the tool name for readability.
Resolved bookmark labels also appear as `[label]` prefixes on their target tree
nodes; label change entries remain available in the entry stream for auditing.

Extension-injected model context is stored as a first-class `custom_message`
entry. Its `custom_type` identifies the extension, while `content`, `details`,
and `display` preserve its payload and presentation choice. `display: false`
keeps the content in model context but hides it from the TUI and the visible
HTML transcript; the complete entry remains in JSONL exports. Older Tau files
that stored these as a generic `message` with `role: "custom"`, or as a Tau-v1
user message with `custom_type`, are normalized when loaded and replay the same
context.

Tau's persisted entry wrappers use snake_case names such as `parent_id` and
`custom_type`. The Pi-compatible RPC inspection API projects those fields as
`parentId` and `customType`; see the [RPC reference]({{< relref "../reference/rpc.md" >}}).

## Where sessions live

```text
~/.tau/sessions/<cleaned-path>-<short-hash>/
```

For example, `/Users/you/repos/tau` becomes something like
`repos-tau-a1b2c3`. The original JSONL is append-only. New Tau versions do not
write separate `leaf` pointer records; the last non-`leaf` entry in file order
is the active tip. Older files containing `leaf` records remain readable, but
those records do not override file-order tip selection. Compaction and
branching change the *active* view, never the recorded history. A label change
is stored as `{"type":"label","target_id":"<entry-id>","label":"checkpoint"}`;
`null` or an empty label clears the target's bookmark. Pre-bookmark Tau files
whose label entries lack `target_id` load deterministically as a bookmark on the
earliest branchable entry.

New compaction and branch-summary entries include optional `usage`, `provider`,
`model`, and `response_provider` fields for the model call that generated the
summary. `response_provider` identifies the resolved backend when a routing
service reports one. The `usage` field uses the same shape as assistant messages (`input`, `output`,
`cacheRead`, `cacheWrite`, optional `cacheWrite1H` and `reasoning`, `totalTokens`,
and `cost`). If more than one completion contributes to a summary, Tau stores
the field-wise total. Older entries and heuristic branch-summary fallbacks omit
`usage`; they continue to load normally and do not add a zero-cost request to
usage analytics.

See
[Configuration]({{< relref "../reference/configuration.md#sessions" >}}) for the exact layout.
