# Claude Opus 5 model support

## What changed

Tau's direct Anthropic catalog now includes `claude-opus-5` with metadata from
Anthropic's July 24, 2026 launch documentation:

- 1,000,000-token context window
- 128,000-token maximum output
- text and image input
- $5 / million input tokens and $25 / million output tokens
- adaptive thinking with `low`, `medium`, `high`, `xhigh`, and `max` effort

Tau retains `claude-sonnet-4-6` as Anthropic's default model. Users opt into
Opus 5 through `/model` or `--provider anthropic -m claude-opus-5`.

## Thinking compatibility

Tau has six UI levels while Opus 5 has five enabled-thinking effort levels plus
a disabled mode. `minimal` is unavailable for this model. Tau maps `xhigh` to
Anthropic's `max` wire value because both represent Tau's top reasoning tier.
`off` now sends `thinking: {"type": "disabled"}` explicitly; omitting the field
would not work because Opus 5 enables adaptive thinking by default.

## Architecture

This remains a catalog and provider-adapter change:

- `tau_coding` owns model discovery, metadata, and thinking-level mapping.
- `tau_ai` serializes the resulting Anthropic Messages API request.
- `tau_agent` remains provider-independent.

## Verification

Run:

```bash
uv run pytest tests/test_provider_catalog.py tests/test_provider_config.py tests/test_tau_ai.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Official sources:

- https://platform.claude.com/docs/en/about-claude/models/whats-new-opus-5
- https://platform.claude.com/docs/en/about-claude/models/overview
- https://platform.claude.com/docs/en/release-notes/overview
