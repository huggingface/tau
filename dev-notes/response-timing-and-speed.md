# Response timing and effective output speed

## What changed

Tau now measures every provider response at three boundaries in the portable
agent loop:

1. request stream consumption starts;
2. the first text, thinking, or tool-call output arrives;
3. the response completes or fails.

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

It intentionally includes request startup, provider queueing, prefill, and time
to first output. The session value is token-weighted:

```text
sum(timed output tokens) / sum(timed response durations)
```

This avoids over-weighting short responses. Untimed older messages remain in
usage and cost totals but do not enter the speed denominator.

## Architecture mapping

- `tau_agent.messages.ResponseTiming` owns the provider-neutral wire shape.
- `tau_agent.loop` captures monotonic boundaries and attaches timing to the
  final assistant message.
- `tau_coding.session_stats` aggregates latest and active-branch values.
- `tau_coding.tui.widgets` renders speed and latest time to first output.

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
