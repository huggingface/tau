# Non-interactive session resume

Tau print mode can now continue an indexed conversation:

```bash
tau --print --session <session-id> "Follow-up message"
```

## Design

The CLI keeps orchestration in `tau_coding`. `SessionManager` resolves the
indexed record, then print mode opens its append-only JSONL transcript and loads
it through `CodingSession`. The reusable `tau_agent` layer remains unaware of CLI
flags and Tau's home-directory layout.

A resumed run uses the record's cwd, model, provider, inference-provider routing,
and active conversation branch. Explicit `--provider` or `--model` options still
override saved selection for that run. New print sessions retain exclusive file
creation; resume never creates a missing id.

`--session` is mutually exclusive with `--new-session` and `--session-id` because
those options request new transcripts. Output remains unchanged across text,
JSON, and transcript modes.

## Testing

```bash
uv run pytest tests/test_cli.py
```

Tests cover CLI forwarding and conflicts, existing/unknown record lookup, and
provider history for a resumed follow-up.
