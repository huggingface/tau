# Subscription-backed pricing

## What changed

Tau no longer applies catalog API rates to assistant responses made through
subscription-backed OAuth credentials. Those responses still contribute token,
cache, timing, and tool-usage totals, but their dollar estimate is unavailable.
The TUI and HTML usage dashboard therefore show no API-equivalent cost for them.

The session marks each persisted assistant response with `usage.pricing_mode =
"subscription"` or `"api"`. This keeps later views from changing newly recorded
responses when a session is resumed with a different credential. Provider-neutral
agent messages leave the field unset and retain the existing catalog-pricing
behavior.

## Why

Catalog rates describe metered API list prices. They do not describe a ChatGPT,
Claude, or other subscription's quota accounting. Authentication and pricing
are consequently resolved separately: API-key requests may use catalog rates,
while OAuth-backed subscription requests do not.

Anthropic is supported in both modes. Tau checks the credential actually used,
not merely whether the provider supports OAuth. OpenAI Codex remains
subscription-backed by provider type.

## Validation

```bash
uv run pytest tests/test_session_stats.py tests/test_session_usage.py tests/test_coding_session.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
