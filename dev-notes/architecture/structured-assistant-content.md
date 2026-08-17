# Structured assistant content and persisted thinking

## What changed

Tau now keeps a provider response as ordered assistant content blocks:

- text
- thinking/reasoning
- tool calls

The finalized Pi-shaped `AssistantMessage.content` array is persisted in session
JSONL. Its `text`, `thinking_text`, and `tool_calls` properties provide convenient
views for current application and extension consumers.

Legacy Tau session rows that used string `content` and separate `tool_calls` are
migrated at the JSONL storage boundary into the canonical Pi message shape.

## Why

Thinking used to exist only as transient `ThinkingDeltaEvent` values. It could be
shown during a live response, but was lost at `message_end`. That made resumed
transcripts incomplete, prevented faithful export and provider replay, and forced the
TUI to infer where thinking belonged.

Pi keeps thinking, text, and tool calls in one ordered assistant content array. Tau
now follows the same core invariant while retaining compatibility properties for its
existing Python API and extension system.

## Provider behavior

Provider adapters expose Pi-compatible `AssistantMessageEvent` streams. Nested
text/thinking updates support responsive frontends; the final response is authoritative
and includes ordered blocks and opaque replay metadata where available:

- OpenAI-compatible chat preserves the reasoning field name.
- Responses/Codex preserve serialized reasoning items when the transport supplies
  them.
- Anthropic preserves thinking signatures.
- Google preserves thought signatures.
- Mistral preserves reasoning text.

History serializers replay supported metadata on later tool-loop requests.

## Frontend and extension behavior

The Textual state loader projects persisted blocks in order, so resumed thinking is
available to Ctrl+T. Live provisional rows are replaced by final structured blocks at
`message_end` through the normal transcript redraw path. That path still resolves
extension custom-message, tool-call, and tool-result renderers.

Extensions receive the same Pi-shaped messages and nested event protocol as the rest
of Tau. Extension-driven custom messages and TUI render hooks remain separate from
assistant content and continue through the normal redraw path.

## Validation

Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
