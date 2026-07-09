---
title: "Phase 7: Session Tree and JSONL Persistence"
---

Phase 7 adds Tau's first durable session layer.

The session primitives live in:

```text
src/tau_agent/session/
```

## What was added

Tau now has:

- typed append-only session entries
- JSONL serialization helpers
- local JSONL session storage
- in-memory session replay
- branch path traversal helpers

## Why sessions are append-only

Tau follows Pi's core session idea: persisted state is not edited in place. Instead, Tau appends immutable entries and reconstructs the current view by replaying them.

This makes session files:

- easy to inspect
- easy to append to safely
- suitable for branching/forking later
- able to preserve history even after compaction or summaries land

A session file is one JSON object per line:

```jsonl
{"type":"message","id":"...","message":{"role":"user","content":"Hello"}}
{"type":"message","id":"...","parent_id":"...","message":{"role":"assistant","content":"Hi"}}
{"type":"label","id":"...","label":"Greeting"}
```

## Entry types

Phase 7 defines these entries:

- `message`
- `model_change`
- `thinking_level_change`
- `compaction`
- `branch_summary`
- `label`
- `leaf`
- `session_info`
- `custom`

Some entries, such as `compaction` and `branch_summary`, were introduced as
placeholders for later phases. Phase 22 makes `compaction` replay-aware while
preserving the append-only session file.

## JSONL storage

`JsonlSessionStorage` provides the first local storage backend:

```python
from tau_agent.session import JsonlSessionStorage, MessageEntry
from tau_agent import UserMessage

storage = JsonlSessionStorage("session.jsonl")
await storage.append(MessageEntry(message=UserMessage(content="Hello")))
entries = await storage.read_all()
```

Missing files read as empty sessions. Parent directories are created automatically when appending.

## Replay

`SessionState.from_entries()` reconstructs an in-memory state:

```python
from tau_agent.session import SessionState

state = SessionState.from_entries(entries)
```

The reconstructed state includes:

- transcript messages
- active model
- thinking level
- label
- active leaf id
- session info
- custom entries

## Tree paths

Every entry has:

```python
id: str
parent_id: str | None
```

This gives Tau enough structure for branch reconstruction:

```python
state = SessionState.from_entries(entries, leaf_id="entry-id")
```

When `leaf_id` is provided, only the root-to-leaf path is replayed. This prevents sibling branches from leaking into the active transcript.

## Boundary

The session layer lives in `tau_agent` because it is part of the reusable agent brain. It does not know about CLI arguments, Textual widgets, Rich rendering, slash commands, or local Tau resource directories.

A later `tau_coding` session wrapper will decide when to append entries, where to store session files, and how commands like `/sessions` or `/fork` should behave.

## Tests

The phase is covered by:

```text
tests/test_session.py
```

The tests verify:

- entry JSONL round-tripping
- malformed JSONL errors
- append/read storage behavior
- missing-file behavior
- linear state replay
- branch path reconstruction
- missing parent validation

## Next phase

The next roadmap phase is the Tau coding session wrapper. That layer can combine session persistence, slash commands, prompt expansion, resources, and print/interactive frontends on top of this append-only foundation.
