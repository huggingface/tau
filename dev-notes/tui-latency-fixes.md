# TUI and harness latency fixes

## What changed

Two symptoms prompted a profiling pass: a submitted prompt took about half a
second to appear in the transcript, and `tau` took one to two seconds to render.
Profiling the real TUI headlessly showed that the agent loop was not the cause
(about 70 ms of overhead across 800 streamed events). The cause was synchronous
disk work on the event loop, plus two bugs. The fixes:

| Area | Before | After |
|---|---|---|
| Session title lookup (`SessionManager.get_session`) | Re-read and validated every project `index.jsonl` on each call (10–15 ms); called on every spinner tick and ~20× per submit | Parsed indexes are cached by file stamp; hot paths search the session's own project index first; the terminal title is cached between chrome refreshes |
| Rows appended after a streamed reply | `finish_assistant_message` left `_window_end` stale, so the next row remounted the whole transcript window | The window end advances past the bound item |
| Submit handler | Textual's eager task factory ran the session's synchronous setup before the submitted prompt was painted | The prompt worker waits for one screen refresh before calling `session.prompt` |
| First prompt of a new session | Auto-naming (a full provider request) ran before the reply's request was sent | Naming runs as a task alongside the reply and is awaited before the run settles |
| Persisting each message | Re-read and re-parsed the whole session JSONL (~160 ms on a 16.8 MB session) | `JsonlSessionStorage` keeps parsed entries and extends them on its own appends (~0.25 ms) |
| Sidebar | Every message remounted the context, skill, and prompt lists and ran `git branch --show-current` | Resource lists remount only when resources change; the branch is cached and re-read when `HEAD` changes |
| Streaming | One Markdown append and layout pass per delta, over a 200-item window | Appends are batched to at most one per 100 ms; the window is 80 items |
| Startup catalog loading | `effective_catalog()` parsed the user catalog and models.dev cache twice | Memoized by file stamps |
| Startup update check | Blocking PyPI request (1.5 s timeout) before the TUI, with failures never cached | The TUI reads only the cache and runs a due check in a background daemon thread; failures are cached for an hour |
| Restoring read calls | Every `read` call resolved every skill path | Resolved skill paths are cached per skills tuple |

Measured headlessly with a fake provider that streams 400 deltas 5 ms apart
(2 s of ideal streaming), using a copy of a real Tau home with 62 project
directories:

| Scenario | Before | After |
|---|---|---|
| New session, Enter → first frame with the prompt | 216 / 291 / 360 ms | 48 / 51 / 67 ms |
| New session, first turn | 7.0 s | 3.3 s |
| Resumed 16.8 MB session, Enter → first frame | 518 / 1910 / 1586 ms | 59 / 86 / 84 ms |
| Resumed 16.8 MB session, one turn | 8.1–10.1 s | 5.2–5.3 s |
| Startup to first frame, new session | ~1.5 s | ~1.3 s |
| Startup to first frame, resumed 16.8 MB session | ~3.3 s | ~2.3 s |

A real terminal adds output cost on top of these numbers.

## Why each fix has the shape it does

**Stamp-validated caches instead of in-memory mirrors.** The session index, the
transcript storage, the effective catalog, and the git branch all keep derived
data keyed by the source file's `(mtime_ns, size, inode)` stamp. Another
process that writes the file changes the stamp and forces a re-read, so the
caches never need explicit invalidation hooks. A process's own writes go
through the same object and update the cache directly. That covers two
rewrites landing in one coarse timestamp tick with the same size.

**Storage parses back what it wrote.** `JsonlSessionStorage._extend_parsed`
parses the JSON lines it just appended rather than caching the caller's
objects. Cached reads therefore match a full re-read exactly, and the cache
never aliases live message objects. Appends that could change how earlier
lines parse (a target-less legacy label followed by the transcript's first
branchable entry) drop the cache instead.

**The harness stays pull-based.** Auto-naming moved to a task rather than
changing the harness. `CodingSession.prompt` yields the confirmed prompt,
starts the naming task, yields one loop iteration so the naming request is
issued first (provider call order stays deterministic), and awaits the task
before the run settles. Closing the prompt stream early cancels the task.
Concurrent provider calls were already safe: OAuth refreshes are serialized
by `_refresh_lock`.

**`wait_for_refresh` is avoided on purpose.** `App.wait_for_refresh` asserts
`App._task`, which Textual only sets in `run_test`. Under `run_async`, the
path `tau` uses, it raises. `_run_prompt_after_refresh` uses
`call_after_refresh` with an `asyncio.Event` instead, and
`test_submitted_prompt_runs_under_real_app_run_loop` drives the app through
`run_async` to keep that path covered.

**The update check uses a daemon thread.** `asyncio.to_thread` would make
interpreter shutdown join the worker, so quitting right after launch on a slow
network would wait for the request timeout. If the check finishes while an
agent turn is streaming, the notice is shown as a toast: appending a
transcript row would finalize the streaming reply.

## Not changed

- The built-in provider catalog is still loaded when `tau_coding` is
  imported. The TUI needs it during startup anyway, so loading it lazily
  would not shorten time to first paint.
- Appends still `fsync` on the event loop. That costs about 1.2 ms on ext4
  here, and moving it to a thread would complicate cancellation.
- The naming request still uses the session's provider and thinking level.
  Thinking is configured on the provider instance, and naming no longer
  delays the reply.

## How this maps to Pi

Pi keeps the same layering: the harness emits events and frontends render
them. These changes stay in `tau_coding` (session, index, catalog, TUI) and in
`tau_agent.session.storage`. Event semantics and the harness loop are
unchanged.

## How to test

```bash
uv run pytest tests/test_session_manager.py tests/test_session.py \
  tests/test_coding_session.py tests/test_tui_app.py tests/test_tui_adapter.py \
  tests/test_provider_catalog.py tests/test_update_check.py tests/test_cli.py
```

Each fix has a regression test that fails without it. Examples:
`test_rows_after_streamed_assistant_reply_do_not_redraw_history`,
`test_session_auto_naming_does_not_delay_the_agent_request`, and
`test_jsonl_storage_reuses_parsed_entries_after_own_appends`.
