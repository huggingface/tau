# Transcript duplicate rendering diagnostics (issue #426)

## What changed

`TranscriptView._redraw()` now accepts a `reason` argument that labels the trigger
that caused the redraw (one of `window_shift`, `state_update`,
`append_page_to_latest`, `append_overscan_shift`, `structured_finalization`).

When the `TAU_DEBUG_TRANSCRIPT_DUP` environment variable is set, `_redraw` schedules
a post-refresh check (`_schedule_duplicate_diagnostic`) that inspects the mounted
children and logs a `WARNING` on `tau.tui.transcript_dup` whenever two or more
visible widgets wrap the same canonical `ChatItem` (by `id(item)`). The log records
the duplicate item ids and roles, the widget types, the current window bounds
(`window_start..window_end`), the total item count, the active streaming item id,
and a stack trace of the redraw trigger.

No behavior changes unless the variable is set: the diagnostic never mutates the
DOM or control flow.

## Why it exists

Issue #426 reports an assistant response rendered twice in a long transcript
(>200 mounted items, structured finalization). The durable JSONL and event pipeline
contain a single copy, so the symptom is presentation-only. The issue's
"Suggested next steps" ask for temporary diagnostics that assert or log when
multiple visible widgets represent the same canonical assistant item, and capture
what immediately preceded the symptom (full redraw, structured finalization, window
shift, resize, or duplicate end event). This instrumentation serves that ask and is
intended to be removed once #426 is resolved.

## Architecture

The duplicate check runs `call_after_refresh` so it observes the DOM after Textual
has processed the asynchronous `Prune` of the rows removed by `remove_children()`.
In normal operation the stale rows are already gone by then, so the logger stays
silent; a lingering duplicate (the reported race) is what emits the warning. Each
`_redraw` caller passes its trigger as `reason`, which is the signal the issue asks
us to capture.

## How to test / use

```bash
# Enable diagnostics for a session, then reproduce a long transcript (>200 items)
# with structured (thinking + text) assistant finalization:
TAU_DEBUG_TRANSCRIPT_DUP=1 tau chat <session>

# Watch for warnings:
tau chat <session> 2>&1 | grep "tau.tui.transcript_dup"
```

When a duplicate is observed, capture the full warning (including
`trigger_stack`) and attach it to issue #426. The `reason` field tells us which
transition (e.g. `structured_finalization`) preceded the duplicate.
