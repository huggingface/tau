# OpenAI reasoning summary boundaries

OpenAI Responses models can stream several `reasoning_summary_text` parts during
one assistant turn. Each part is followed by a
`response.reasoning_summary_part.done` event. Tau previously forwarded only the
text deltas, so Markdown summaries such as `**First step**` and `**Second step**`
were concatenated into `**First step****Second step**` in live display, persisted
messages, and replay.

Both Responses parsers now translate each completed summary-part boundary into a
blank-line thinking delta. The canonical stream therefore retains separate
Markdown paragraphs without inventing or exposing private reasoning content.
This applies to the OpenAI-compatible Responses transport and the ChatGPT Codex
subscription transport.

The fix belongs in `tau_ai` rather than the TUI: canonical `ThinkingContent` must
already contain the correct boundaries so every frontend, session file, export,
and replay observes the same text. Existing sessions remain unchanged because
Tau does not rewrite durable history; newly streamed responses preserve the
boundaries.

Tests in `tests/test_tau_ai.py` cover both transports and assert the individual
thinking deltas plus the final canonical message.

Run:

```bash
uv run pytest tests/test_tau_ai.py -k reasoning_summary
```
