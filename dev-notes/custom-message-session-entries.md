# First-class custom message session entries

GitHub issue #704 aligns Tau's persisted extension messages with Pi's session
entry model without changing what providers receive.

## What changed

Runtime extension context is still represented by `CustomMessage(role="custom")`.
Persistence now stores it as a dedicated `CustomMessageEntry`:

```json
{"type":"custom_message","custom_type":"extension:status","content":"working","display":true}
```

Tau session wrappers use snake_case (`parent_id`, `custom_type`). Nested content
blocks retain their Pi-compatible aliases, such as `mimeType`. RPC is a separate
compatibility boundary and projects the same entry with Pi's `parentId` and
`customType` names.

`CodingSession` chooses the entry type at the durable `MessageEndEvent`
boundary. Retry state retains that exact entry and ID, so an append that reaches
storage before raising is detected and is not duplicated. Tool-history repair
uses the same entry factory when it copies a custom message onto a repaired
branch.

## Replay and compatibility

`SessionState` turns `custom_message` entries back into `CustomMessage` values.
Providers continue to receive the same user-role content through
`message_to_user()`; `display` and `details` never enter the provider payload.
The entry timestamp is derived from the runtime message timestamp and converted
back to milliseconds on replay.

The JSONL migration boundary accepts both historical Tau forms:

- a generic `message` entry whose nested role is `custom`
- Tau-v1 `role="user"` messages carrying `custom_type` or `customType`

Both normalize in memory to `custom_message`. The migration moves the nested
message timestamp to the entry timestamp so replay preserves the original
runtime message, including content, type, details, display, and timestamp. It
also accepts `customType` on an incoming dedicated entry, but canonical Tau
persistence writes `custom_type`.

## Display behavior

A hidden custom message (`display=false`) remains in replayed model context but
is omitted from live and restored TUI transcripts and from the visible HTML
export. The HTML export's embedded JSONL download still contains every entry.
Visible custom messages render their raw content and details in static exports;
live extension renderers remain responsible for richer TUI/print formatting.

## Validation

Focused coverage includes schema round trips, both legacy migrations, replay,
provider conversion through the extension prompt path, retry idempotence, RPC
projection, HTML visibility, and live/restored TUI visibility. Run all project
checks with:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
