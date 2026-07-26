# Exit resume hint

## What

When the interactive TUI exits and the session was persisted, Tau now prints
a one-line reminder of how to resume it:

```text
To resume this session: tau --session <session-id>
```

This mirrors Pi's exit-time hint (`To resume this session: pi --session
[session id]`) using Tau's `--session` flag, which was renamed from
`--resume` in [issue #439](https://github.com/huggingface/tau/issues/439)
to match Pi's naming (see `dev-notes/cli-mirror-pi-flags.md`).

## Why

Closing the TUI previously gave no indication of how to pick the
conversation back up. New users in particular had to already know about
`tau --session <id>` or `tau sessions`. A short, low-friction hint at exit
closes that gap without adding new UI surface.

See [issue #438](https://github.com/huggingface/tau/issues/438) for the
original request and discussion.

## How it maps to the architecture

Per `AGENTS.md`, `tau_agent` stays UI-agnostic; this hint is purely a
`tau_coding` (CLI/TUI) concern:

- `src/tau_coding/tui/app.py`: `run_tui_app` now returns `str | None` — the
  active session id, but only if that session is actually persisted/indexed
  (`manager.get_session(active_session_id) is not None`). Ephemeral or
  never-persisted sessions return `None`, and the hint is suppressed.
- `src/tau_coding/cli.py`: `run_openai_tui` forwards that return value.
  `main()` captures it from `anyio.run(...)` as `resumable_session_id` and,
  after the TUI has fully exited (session/provider cleanup complete), prints
  the hint via `typer.echo` before raising `typer.Exit()`.

No new state or side channel was introduced — the resumable session id is
threaded back through existing return values.

## Scope

This only covers the interactive TUI exit path. Print mode
(`tau -p`/`run_openai_print_mode`) is a single-shot, already-scripted
invocation and was left out of scope — see the issue for that discussion.

## How to test/use it

- Automated: `tests/test_cli.py::test_cli_prints_resume_hint_after_tui_exit`
  and `test_cli_suppresses_resume_hint_without_persisted_session` exercise
  the CLI-level behavior with a fake `run_openai_tui`.
- Manual: run `tau`, send at least one message (so the session persists),
  then quit (`Ctrl+C` / `/quit`). The shell should print the resume hint
  with the real session id. Starting `tau` and exiting immediately, before
  any session-worthy activity, should print nothing.
