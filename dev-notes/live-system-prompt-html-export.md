# Live system prompts in HTML session exports

## What changed

HTML created by a live `CodingSession` now contains its current
`CodingSession.system_prompt`. The prompt appears in a labeled, collapsed
**System Prompt** section above the transcript controls. It is escaped as plain
text, keeps indentation and line breaks, and wraps long lines.

Stored session JSONL still contains only session entries. Consequently:

- `/export --format jsonl` does not add the prompt.
- the JSONL download embedded in HTML does not add the prompt.
- `tau export <session-id>` and `tau export <path.jsonl>` omit the HTML section,
  because these offline paths only have stored entries and cannot recover the
  live prompt.

## Why the prompt is separate

A system prompt configures provider requests; it is not a user, assistant, tool,
or custom transcript event. Passing it as optional rendering context keeps the
append-only session format unchanged and avoids inventing a synthetic entry.

This behavior belongs in `tau_coding`: `CodingSession.export()` supplies the
live value to the application-level HTML renderer. `tau_agent` remains unaware
of CLI and HTML export policy.

## Sharing and safety

System prompts may include project instruction files, skill guidance, working
directory paths, and other local context. The exported section warns about
project instructions, but users should open and review live HTML before sharing
it. HTML escaping prevents prompt text from becoming executable markup.

## How to test

Focused checks:

```bash
uv run pytest tests/test_session_export.py tests/test_coding_session.py tests/test_cli.py -k export
```

Full project checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
