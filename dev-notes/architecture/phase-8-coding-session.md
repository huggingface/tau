---
title: "Phase 8: Coding Session Wrapper"
---

Phase 8 adds Tau's first coding-session environment wrapper.

The implementation lives in:

```text
src/tau_coding/session.py
```

## What was added

Tau now has a `CodingSession` that combines:

- `AgentHarness`
- append-only session storage
- restored transcript messages
- built-in coding tools
- model/session metadata
- a minimal slash-command seam

This is the first layer that starts to resemble Pi's `AgentSession`, but it remains intentionally small.

## Why this lives in `tau_coding`

`tau_agent` owns reusable primitives:

- messages
- events
- tools as an abstraction
- the pure loop
- `AgentHarness`
- low-level session entries/storage/replay

`tau_coding` owns the coding-agent environment around those primitives:

- local cwd
- built-in coding tools
- storage choices
- slash commands
- prompt/resource loading later
- print and interactive frontends later

So `CodingSession` belongs in `tau_coding`.

## Loading a session

A session is loaded from a `SessionStorage` implementation:

```python
from pathlib import Path
from tau_coding import CodingSession, CodingSessionConfig
from tau_agent.session import JsonlSessionStorage

session = await CodingSession.load(
    CodingSessionConfig(
        provider=provider,
        model="gpt-4.1-mini",
        system=system_prompt,
        storage=JsonlSessionStorage("session.jsonl"),
        cwd=Path.cwd(),
    )
)
```

Loading performs these steps:

1. read all session entries
2. initialize metadata for an empty session
3. replay entries into `SessionState`
4. create an `AgentHarness` with restored messages
5. register built-in coding tools unless custom tools are supplied

For a new empty session, Tau appends:

- `SessionInfoEntry`
- `ModelChangeEntry`

## Prompt and continue

`CodingSession.prompt()` runs a new user prompt through the harness:

```python
async for event in session.prompt("Read README.md"):
    ...
```

After the run completes, all new harness messages are appended as `MessageEntry` records. Tau also appends a `LeafEntry` pointing at the newest message entry.

`CodingSession.continue_()` resumes from restored state without appending a new user message first.

## Persistence model

Phase 8 persists messages after a run completes. This keeps the implementation simple and avoids double-saving messages while `AgentHarness` mutates its in-memory transcript.

Later phases can make persistence more incremental if Tau needs crash recovery during a streaming response.

## Minimal commands

`CodingSession.handle_command()` currently supports only:

- `/help`
- `/exit`

Unknown slash commands are handled with an explanatory message. Normal prompts return `handled=False`.

This is a small seam for a future command registry. Full commands such as `/model`, `/sessions`, `/fork`, and `/compact` are intentionally deferred.

## Non-goals

Phase 8 does not add:

- automatic print-mode session persistence
- session directory discovery
- model switching commands
- direct bash command prefixes
- project instruction loading
- skills or prompt templates
- Rich rendering
- Textual UI
- compaction

Those belong to later phases.

## Tests

The phase is covered by:

```text
tests/test_coding_session.py
```

The tests verify:

- empty session metadata initialization
- prompt persistence
- transcript restore
- continuation persistence
- tool result persistence
- minimal command handling

## Next phase

The next roadmap phase is skills, prompt templates, and system prompt assembly groundwork. The coding-session wrapper created here gives those later systems a stable place to plug in.
