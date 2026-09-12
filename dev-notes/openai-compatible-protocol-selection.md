# OpenAI-compatible protocol selection

## Context

Custom-provider setup accepted a base URL and model without asking which OpenAI
API the server supports. The provider adapter could then use the model name to
choose between Chat Completions and Responses. Proxy names and local model names
made that behavior hard to predict.

Issue #546 adds the API choice to `tau setup` and `/login custom`. Tau saves the
selection with the existing provider metadata in `catalog.toml` and uses it for
every request.

## What changed

`openai-completions` selects `/chat/completions`, while `openai-responses`
selects `/responses`. A model-level setting overrides its provider setting. The
model ID remains unchanged in the request payload.

New providers created through either setup flow always save an API value.
`providers.json` remains preference-only, and session JSONL remains unchanged.

## Pi to Tau mapping

| Pi | Tau |
| --- | --- |
| resolved `Model.api` | resolved provider/model catalog API |
| provider `api` | `ProviderCatalogEntry.api` |
| custom-model `api` | `ModelCatalogMetadata.api` |
| `definition.api ?? provider.api` | model metadata, then provider metadata |
| `getApiProvider(model.api)` | exact OpenAI-compatible transport branch |
| `models.json` | `~/.tau/catalog.toml` |

The upstream reference is Pi v0.74.0 at `1eee081`. The relevant sources are
`packages/ai/src/types.ts`, `packages/coding-agent/src/core/model-config.ts`,
`packages/coding-agent/src/core/provider-composer.ts`, and
`packages/coding-agent/docs/models.md`.

## Compatibility

API-less legacy OpenAI-compatible entries resolve to `openai-completions`. This
preserves providers configured before the API field was available. Entries that
depended on a `gpt-5` or `codex` model name to reach Responses should add
`api = "openai-responses"`.

## Verification

```bash
uv run pytest tests/test_tau_ai.py tests/test_provider_config.py \
  tests/test_provider_catalog.py tests/test_cli.py tests/test_tui_app.py
uv run ruff check src/tau_coding/cli.py src/tau_coding/tui/app.py \
  tests/test_provider_catalog.py tests/test_cli.py tests/test_tui_app.py
```

For a manual check, configure `company/openai/gpt-5.6` with
`openai-responses` and confirm that Tau uses `/responses`. Then configure
`gpt-5.5-proxy` with `openai-completions` and confirm that Tau uses
`/chat/completions`.
