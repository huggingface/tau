---
title: "04 — Sessions"
---

Sessions preserve conversations and agent state across runs.

## Design

Tau uses an append-only session tree. Instead of mutating old state, Tau appends entries and reconstructs state by replaying them.

The low-level implementation lives in:

```text
src/tau_agent/session/
```

## Entry types

- `message`
- `model_change`
- `thinking_level_change`
- `compaction`
- `branch_summary`
- `label`
- `leaf`
- `session_info`
- `custom`

## Current capabilities

Tau can now:

- serialize and deserialize session entries as JSONL
- append entries to local session files
- read session files in order
- reconstruct linear session state
- reconstruct a root-to-leaf branch path
- load a `tau_coding.CodingSession` that restores messages and persists new runs

## Durable message boundary

`CodingSession` treats `MessageEndEvent` as the durable-message boundary. When the harness emits a completed message, the coding session appends that message to storage immediately instead of waiting for the whole agent run to finish.

This mirrors Pi's session model and matters for interactive UIs:

- the first user prompt is branchable while the assistant is still responding
- queued steering and follow-up messages become durable when they are injected
- cancellation or process failure preserves completed messages
- the TUI can read tree state that matches the active run

Each persisted message is followed by a `leaf` entry pointing at that message. The leaf entries form an append-only history of the active branch pointer.

Empty sessions are still deferred: loading a new session prepares initial metadata in memory, but Tau does not create the transcript file until the first durable session entry is appended. The first append materializes the pending `session_info`, model, and thinking-level entries before writing the message.

## System prompt persistence

Tau does not currently persist the resolved system prompt in the session JSONL file.

A resumed session restores the saved conversation branch, model changes, thinking-level changes, compactions, branch summaries, labels, and custom entries from the append-only session file. The system prompt is rebuilt at load time from the current coding-agent configuration and resources, including the current cwd, tools, loaded skills, context files such as `AGENTS.md`, and any custom or appended system prompt settings.

This means `/resume` should be understood as:

```text
saved conversation branch
+
current resolved system prompt
```

not:

```text
saved conversation branch
+
original system prompt snapshot from session creation
```

If system-prompt inputs change after a session was created, future turns in that resumed session may run with a different system prompt than earlier turns. This is useful when sessions should pick up improved Tau instructions, updated project context, changed skills, or changed tool guidance. The tradeoff is that the JSONL file is not a complete audit snapshot of exactly what prompt text was sent on every historical turn.

This matches Pi's current behavior. Pi's JSONL session schema stores the conversation tree and session state entries, but it does not store a dedicated resolved-system-prompt entry. Pi rebuilds its base system prompt from the active resource loader when creating or resuming an agent session. Pi can display/export the current runtime system prompt in some UI/export paths, but that prompt comes from live agent state rather than from a persisted JSONL snapshot.

A future Tau enhancement could add an explicit metadata entry for resolved system prompt snapshots or system-prompt revisions. If added, that should likely remain session metadata rather than a normal branchable transcript message, because the harness treats the system prompt separately from user, assistant, and tool messages.

## Boundary

Low-level session primitives belong in `tau_agent`. File locations, slash commands, and coding-agent workflows belong in `tau_coding`.

`CodingSession` is the first `tau_coding` layer on top of the low-level primitives. It wires storage, `AgentHarness`, cwd, and coding tools together while leaving richer commands and resource loading for later phases.
