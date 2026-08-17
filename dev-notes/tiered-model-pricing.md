# Tiered model pricing

Tau's catalog originally stored one flat `cost` mapping per model. That works for most providers, but MiniMax-M3 doubles its standard rates when input grows beyond 512,000 tokens while supporting a one-million-token context window.

Issue #364 adds optional ordered `cost_tiers` to `ModelCatalogMetadata`. Each tier contains the same per-million-token rate fields as `cost` and may set an inclusive `max_input_tokens`. Limits must increase, and the final tier is unbounded. The existing flat `cost` remains the base rate for backward compatibility.

`model_cost_for_input_tokens()` resolves the first tier that includes a given input-token count. Catalog loading, user overlays, TOML serialization, and runtime provider metadata preserve the tiers. MiniMax-M3 now records both its `<=512k` and `>512k` standard rates.

Validate with:

```bash
uv run pytest tests/test_provider_catalog.py tests/test_provider_config.py
uv run ruff check .
uv run mypy
```
