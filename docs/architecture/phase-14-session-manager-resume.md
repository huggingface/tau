# Phase 14: Session Manager and Resume

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

Session metadata is stored at:

```text
~/.tau/sessions/index.jsonl
```

Project-specific transcript files live under:

```text
~/.tau/sessions/<project-hash>/
```

The default project session remains:

```text
~/.tau/sessions/<project-hash>/default.jsonl
```

but it is now indexed with a stable id:

```text
default-<project-hash>
```

New sessions are stored as:

```text
~/.tau/sessions/<project-hash>/<session-id>.jsonl
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

The older explicit `tui` argument and the explicit new-session flag are also
accepted for clarity:

```bash
tau --new-session
```

Resume an indexed session:

```bash
tau --resume <session-id>
```

If the session id is unknown, Tau exits with a clear error.

## CLI session listing

Tau can list indexed sessions:

```bash
tau sessions
```

The first implementation prints tab-separated rows:

```text
<id>    <title>    <model>    <cwd>
```

A richer session picker belongs in a later TUI polish phase.

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
