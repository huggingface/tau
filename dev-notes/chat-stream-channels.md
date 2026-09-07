# Independent Chat Completions channels

DeepSeek V4 Flash through Hugging Face/DeepInfra can interleave `content` and
`reasoning_content` fragments, including both fields in one SSE chunk. The
Chat Completions parser already accumulated each field correctly, but the
canonical stream bridge treated every field switch as an ordered block boundary.
That split both reasoning and answer sentences in the persisted transcript.

The OpenAI-compatible adapter now selects independent channel assembly only for
Chat Completions, using the same transport decision as request dispatch. Each
channel keeps a stable content index and emits one start/end pair. Both remain
open until tool calls or response completion; partial/error snapshots retain the
assembled blocks. First-seen channel order is preserved. Responses and other
providers retain sequential ordering; no model-name-specific workaround is used.

The TUI keeps an open thinking widget while answer deltas arrive and closes it
on the explicit thinking-end event. This follows Pi's indexed event lifecycle
without adding frontend concerns to the portable harness. Final messages still
use the normal adapter/persistence path. Existing saved sessions are not rewritten.

Regression checks use mock SSE chunks matching the reported fragment pattern,
including simultaneous fields, stable indices, signatures, immutable snapshots,
and a Textual test for the live widgets. Run:

```sh
uv run pytest tests/test_chat_channels.py tests/test_tui_chat_channels.py tests/test_tau_ai.py
```
