# Export destination transcript notice

## What changed

A successful TUI `/export` now adds the exported file location as a status row in
the visible transcript instead of using Textual's temporary lower-right toast.
Export failures remain error notifications.

## Why

The destination is useful after a toast disappears. Keeping it in display state
makes it easy to find and copy while preserving the boundary between TUI output
and model context.

## Context and persistence

The status row uses the same non-persistent command-output path as `/reload`.
It is not added to `CodingSession.messages`, written to session JSONL, or sent to
the model.

## Test

```bash
uv run pytest tests/test_tui_app.py -k export_command
```
