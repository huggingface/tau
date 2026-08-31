# OpenAI-compatible protocol selection

Issue #546 ports Pi's explicit custom-model API selection into Tau. The upstream
reference used for this implementation is Pi v0.74.0 (`1eee081`), specifically
`packages/ai/src/types.ts`, `packages/coding-agent/src/core/model-config.ts`,
`packages/coding-agent/src/core/provider-composer.ts`, and
`packages/coding-agent/docs/models.md`.

## Pi to Tau mapping

| Pi | Tau |
| --- | --- |
| resolved `Model.api` | resolved provider/model catalog API |
| provider `api` | `ProviderCatalogEntry.api` |
| custom-model `api` | `ModelCatalogMetadata.api` |
| `definition.api ?? provider.api` | model metadata, then provider metadata |
| `getApiProvider(model.api)` | exact OpenAI-compatible transport branch |
| `models.json` | `~/.tau/catalog.toml` |

Pi treats the API as model capability metadata. Tau now follows the same rule:
`openai-completions` always uses `/chat/completions`, and `openai-responses`
always uses `/responses`. The model ID is request data, not routing policy.

`/login custom` and `tau setup --api` save the provider default in
`catalog.toml`. A model-level `api` in `model_metadata` overrides it.
`providers.json` remains preference-only and session JSONL remains unchanged.

API-less legacy OpenAI-compatible entries resolve to `openai-completions`. This
is a deterministic compatibility default, not inference. Existing entries that
depended on a `gpt-5` or `codex` model name to reach Responses must add
`api = "openai-responses"`; Tau does not rewrite them by guessing from names.

## Verification

```bash
uv run pytest tests/test_tau_ai.py tests/test_provider_config.py \
  tests/test_provider_catalog.py tests/test_cli.py tests/test_tui_app.py
uv run ruff check src/tau_coding/cli.py src/tau_coding/tui/app.py \
  tests/test_provider_catalog.py tests/test_cli.py tests/test_tui_app.py
```

For manual verification, configure a misleading model name once with each API:
`company/openai/gpt-5.6` plus `openai-responses` must use `/responses`, while
`gpt-5.5-proxy` plus `openai-completions` must use `/chat/completions`.
