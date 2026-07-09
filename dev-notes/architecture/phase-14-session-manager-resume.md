---
title: "Phase 14: Session Manager and Resume"
---

Phase 14 makes Tau sessions first-class records stored under Tau home.

The implementation lives in:

```text
src/tau_coding/session_manager.py
```

## What was added

Tau now has a `SessionManager` that can:

- create new sessions
- index sessions in user-home metadata
- list sessions newest-first
- look up sessions by id
- touch sessions after new messages are persisted
- return a default project session for existing TUI behavior

Session metadata is represented by:

```python
CodingSessionRecord
```

with:

- `id`
- `path`
- `cwd`
- `model`
- `title`
- `created_at`
- `updated_at`

## Storage layout

Session transcripts remain append-only JSONL files.

Session metadata was originally stored in a global compatibility index:

```text
~/.tau/sessions/index.jsonl
```

Current session metadata and transcript files live under a project-specific
directory:

```text
~/.tau/sessions/<cleaned-path-suffix>-<short-hash>/
```

The default project session remains:

```text
~/.tau/sessions/<cleaned-path-suffix>-<short-hash>/default.jsonl
```

but it is now indexed with a stable id:

```text
default-<cleaned-path-suffix>-<short-hash>
```

New sessions are stored as:

```text
~/.tau/sessions/<cleaned-path-suffix>-<short-hash>/<session-id>.jsonl
```

## CodingSession integration

`CodingSessionConfig` now accepts:

```python
session_id: str | None
session_manager: SessionManager | None
```

When a session persists new messages, it touches the session manager record so `updated_at` stays current.

This keeps the session transcript and session index loosely coupled: `tau_agent` still only knows about append-only session storage, while `tau_coding` owns user-facing session metadata.

## TUI integration

The TUI now creates sessions through `SessionManager`.

Default behavior:

```bash
tau
```

creates a new session.

The explicit new-session flag is also accepted for clarity:

```bash
tau --new-session
```

Resume an indexed session:

```bash
tau --resume <session-id>
```

If the session id is unknown, Tau exits with a clear error.

Later TUI polish added in-process session switching through:

```text
/resume <session-id>
```

Plain `/resume` opens the project-scoped session picker. `/resume <session-id>`
reloads the selected indexed session and rebuilds the visible transcript without
restarting Tau.

## CLI session listing

Tau can list indexed sessions:

```bash
tau sessions
```

The first implementation prints tab-separated rows:

```text
<id>    <title>    <model>    <cwd>
```

The richer modal session picker now uses the same project-scoped session records
as `/resume`.

## Interrupted tool-call repair on resume

Tau repairs cancelled tool calls by adding a synthetic tool result:

```text
Tool call interrupted by user
```

This keeps OpenAI-compatible transcripts valid, because those providers reject
any history where an assistant tool call has no matching tool output. A subtle
resume bug existed in older builds: the repair could be added to the in-memory
`AgentHarness`, letting the live session continue, but not written back to the
append-only JSONL file. After `/resume`, Tau rebuilt the branch from disk without
that synthetic tool result, so the next provider request failed with errors like:

```text
No tool output found for function call call_...
```

`CodingSession.load()` now checks the active branch for unmatched assistant tool
calls before returning a resumed session. If it finds one, it appends durable
repair entries and advances the leaf to the repaired branch. This also covers
historical corrupted sessions where a user message was already appended after the
dangling tool call.

## Tests

The phase is covered by:

```text
tests/test_session_manager.py
tests/test_coding_session.py
tests/test_cli.py
```

The tests verify:

- session creation and indexing
- default session records
- newest-first listing
- metadata updates after prompt persistence
- TUI resume/new-session CLI wiring
- session listing CLI output

## Next phase

The next phase should replace hardcoded slash-command handling with a command registry. That registry can then power TUI autocomplete and session commands such as `/sessions`, `/resume`, and `/new`.
