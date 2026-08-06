# Pi-versus-Tau cache benchmark

`scripts/cache_benchmark.py` runs three repetitions of the same moderately
complex, read-only exploration prompt through Pi and Tau. Both use
`openai-codex/gpt-5.6-luna` at medium reasoning. Tau's home is isolated per
repetition; the harness copies credentials/settings without changing the real
Tau home and overrides only Luna's thinking default to `medium`.

Run from the Tau worktree:

```bash
uv run python scripts/cache_benchmark.py --run --target "$PWD"
```

The result directory preserves native JSONL sessions, stdout transcripts, the
prompt, request-level CSV, and an SVG plot. The analyzer understands Tau's
snake_case usage fields and Pi's camelCase usage fields.

Comparability is limited: Pi and Tau have different system prompts, tool
schemas, context serialization, request retry behavior, and session metadata.
The plot compares provider-reported cache reuse per request, not identical
token sequences or total cost. The target worktree should be clean before and
after a run; the prompt restricts both agents to read-only tools, and Pi is
additionally tool-allowlisted to `read,grep,find,ls`.
