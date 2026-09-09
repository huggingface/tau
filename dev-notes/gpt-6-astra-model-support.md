# GPT-6 Astra model support

## What changed

Tau now includes `gpt-6-astra` in the checked-in OpenAI API and Codex
subscription catalogs. The bundled models.dev snapshot adds only the OpenAI
Astra row required for direct API selection. The broader upstream inventory
change is intentionally excluded here and tracked in catalog refresh PR #699.
Runtime refresh can still expose Astra on other built-in providers when
models.dev advertises it.

The fallback metadata follows Pi's published provider entries:

- text and image input;
- a 272,000-token session context window and 128,000-token output limit;
- `low`, `medium`, `high`, `xhigh`, and `max` reasoning effort;
- Codex `minimal` maps to the model's `low` effort;
- base pricing of $10 input, $50 output, $1 cache read, and $12.50 cache write
  per million tokens, with Pi's published long-context tier retained in model
  metadata.

Codex's authenticated model discovery remains authoritative for each account.
The static row provides an offline fallback and does not make Astra available to
an account whose live Codex catalog omits it.

## Why

Pi's model catalog supports GPT-6 Astra for both OpenAI API keys and Codex
subscriptions. Adding the same verified fallback lets Tau users select Astra
without hand-editing `~/.tau/catalog.toml`, while preserving Tau's existing live
Codex rollout checks.

## Validation

Run:

```bash
uv run pytest tests/test_provider_catalog.py tests/test_provider_config.py tests/test_models_dev.py
uv run ruff check .
uv run mypy
```

Manual Codex validation still requires an eligible account: authenticate with
`/login openai-codex`, open `/model`, and confirm `gpt-6-astra` remains visible
after the live refresh.

## Sources

- https://pi.dev/models/openai/gpt-6-astra
- https://pi.dev/models/openai-codex/gpt-6-astra
- https://models.dev/api.json
