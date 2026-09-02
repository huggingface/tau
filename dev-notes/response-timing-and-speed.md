# Response timing and effective output speed

## What changed

Tau now measures every provider response at three boundaries in the portable
agent loop:

1. waiting for provider stream events starts;
2. the first text, thinking, or tool-call output arrives;
3. the response completes or fails.

Only time spent awaiting the provider iterator is accumulated. Time spent by an
upstream consumer rendering, persisting, or diagnosing an event before requesting
the next event is excluded.

The loop uses a monotonic clock and stores two compact durations on the final
`AssistantMessage`: `timeToFirstOutputMs` and `totalDurationMs`. Because session
persistence already writes final assistant messages, timing is automatically
saved beside provider-reported usage in JSONL. The optional field keeps older
sessions readable; old messages simply have no timing statistics.

## Why it exists

The enclosing session-entry timestamp records persistence time, not request
start, so historical JSONL could not reliably calculate response speed. Pairing
monotonic durations with each response's output-token count makes the metric
replayable after resume and robust against wall-clock changes.

Tau calls the sidebar metric **effective output speed**:

```text
output tokens / total response duration
```

It includes provider queueing, network waits, prefill, and time to first output,
but excludes Tau's work between stream pulls. The session value is token-weighted:

```text
sum(timed output tokens) / sum(timed response durations)
```

This avoids over-weighting short responses. Untimed older messages remain in
usage and cost totals but do not enter the speed denominator.

## Architecture mapping

- `tau_agent.messages.ResponseTiming` owns the provider-neutral wire shape.
- `tau_agent.loop` accumulates monotonic provider-await durations and attaches
  timing to the final assistant message.
- `tau_coding.session_stats` aggregates token-weighted TPS and arithmetic-mean TTFT.
- `tau_coding.tui.widgets` renders average TPS and average TTFT.

No Textual dependency enters `tau_agent`, and provider adapters need no custom
timing implementation.

## Validation

```bash
uv run pytest tests/test_agent_loop.py tests/test_agent_types.py tests/test_session.py \
  tests/test_session_stats.py tests/test_tui_app.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
