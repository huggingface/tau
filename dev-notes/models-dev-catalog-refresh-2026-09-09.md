# models.dev catalog refresh (2026-09-09)

## What changed

The bundled models.dev snapshot was regenerated from the live source on
2026-09-09:

```bash
uv run python scripts/generate_models.py
```

The snapshot now contains 758 eligible models across 25 Tau providers. Notable
inventory changes include GPT-6 Astra, Claude Fable 5.1, Gemini 3.8 Flash,
Qwen3.8, GLM-5.3, and current OpenCode models. Models no longer present or
eligible upstream were removed, including retired NVIDIA inventory and the
OpenAI `gpt-5.2-chat-latest` and `gpt-5.3-chat-latest` rows.

The refresh also updates upstream-owned metadata such as names, limits,
modalities, reasoning options, and pricing. Tests that intentionally pin current
NVIDIA and MiniMax metadata were updated to match the generated snapshot.

## Why

Tau bundles a generated snapshot so model selection remains useful offline and
before the first background refresh. Periodically regenerating it keeps that
offline baseline aligned with models.dev while preserving Tau's provider
transport, authentication, defaults, and explicit catalog corrections.

This is separate from the focused GPT-6 Astra support change so reviewers can
audit broad upstream catalog churn independently.

## Validation

Run:

```bash
uv run pytest tests/test_provider_catalog.py tests/test_provider_config.py tests/test_models_dev.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Source

- https://models.dev/api.json
