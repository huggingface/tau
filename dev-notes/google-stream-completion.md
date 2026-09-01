# Google stream completion validation

## What changed

Tau's native Google Generative AI adapter now requires the stream to contain a
Google `finishReason` before it emits a successful response-end event. A clean
HTTP close without that field produces a provider error while preserving any
partial text, thinking, or tool-call content.

An empty stream that closes cleanly is retried using the provider's configured
retry policy. Tau does not retry after model output starts because replaying the
request could duplicate visible output or tool calls. Malformed JSON stream
chunks are terminal provider errors rather than ignored input.

## Why

Google normally supplies `finishReason` in its final streamed candidate. The old
parser converted a missing value to `stop`, so a response interrupted during a
thinking block looked successfully complete. Agent sessions—and in-process
subagents—could consequently end with no final text and no visible provider
failure.

The provider-neutral stream bridge already preserves partial output on an error.
The Google adapter now reports the missing terminal marker instead of masking it,
allowing the agent loop to stop before executing a tool call from an incomplete
response.

## How this maps to Tau's architecture

The Google-specific terminal-marker rule stays in `tau_ai.google`. It emits the
existing provider-neutral error event, and `tau_ai.stream` converts that into the
canonical assistant error consumed by `tau_agent`. No Google protocol knowledge
is added to the reusable agent loop or coding UI.

## Validation

Focused regressions in `tests/test_tau_ai.py` cover:

- clean close during thinking, preserving partial thinking;
- incomplete text/tool output, including preventing tool execution;
- retry and exhaustion for an empty clean close;
- valid `STOP` and `MAX_TOKENS` completion reasons; and
- malformed/truncated JSON chunks.

Run them with:

```bash
uv run pytest tests/test_tau_ai.py -k google_provider
```
