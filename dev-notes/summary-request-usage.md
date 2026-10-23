# Persist summary-request usage

## What changed

Compaction and branch-summary entries now have optional `usage`, `provider`,
`model`, and `response_provider` fields. `usage` uses `tau_agent.messages.Usage`,
the same strict token-and-cost model stored on assistant messages. The coding
session captures the final provider event's usage when it generates a summary
and persists it with the exact logical provider, model, and resolved routing
provider used for that request.
Multiple completion events are combined field by field so the entry represents
the total cost of producing the summary.

Model-assisted branch summaries retain usage. If generation fails or produces
no usable text, Tau's deterministic heuristic remains the fallback and the
entry records `usage=None` because no successful model summary was used.

The HTML export's usage collector treats persisted summary usage as a real,
separately labeled request. The cumulative session-statistics collector does the
same for the TUI sidebar. Both include those tokens in prompt, cache, output,
hit-rate, and estimated-cost totals. New entries provide the exact persisted
provider/model and, when reported, the resolved provider used by a routing
service; legacy entries fall back to preceding model-change or assistant
metadata. Summary requests are recorded before the compaction or branch event
that they produce, so event markers remain attached to the next context request.
The shared Pi-compatible `Usage` object itself intentionally contains
only tokens and cost. RPC entry projections include `usage` where present, and the
transcript detail panel displays the raw usage object.

## Why it exists

Summary generation can reread most of a session and may be one of its largest
requests. Previously Tau discarded its final usage event, so session exports
systematically underreported token consumption and estimated cost. Persisting
usage on the session entry follows Pi's separation: the portable session schema
owns durable request facts, while the coding-session layer captures them and UI
analytics consume them.

## Session compatibility

The new fields default to `None` and JSONL serialization excludes null values.
Therefore session files written before this change load unchanged, and newly
written heuristic summaries have the same shape as legacy summaries. Analytics
skip missing usage rather than creating a synthetic zero-token request.

## Validation

Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
cd website && hugo --minify && npx --yes pagefind@latest --site public
```

Tests cover model-event capture for both summary paths, fallback and legacy
`None` behavior, JSONL aliases/round trips, aggregate analytics, RPC projection,
and HTML export rendering.
