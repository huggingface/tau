# Adding catalog models safely

Tau's built-in provider catalog (`src/tau_coding/data/catalog.toml`) is user-facing configuration: it drives `/login`, provider setup, model pickers, context-window checks, cost display, and thinking-mode request payloads. Treat catalog changes like runtime behavior changes, not just data updates.

## Checklist

1. **Use provider-owned identifiers.** Copy model IDs from the provider's API or official docs. Do not infer IDs from another router unless the new provider documents the same ID.
2. **Verify endpoint shape.** Confirm the provider kind and API transport (`openai-completions`, `openai-responses`, Anthropic, Google, etc.) match a request that Tau can actually send.
3. **Keep metadata internally consistent.** Every model in `models` should have matching entries in `context_windows` and, when practical, `model_metadata`. `default_model` must be in `models`.
4. **Be conservative with limits.** Set `context_window` and `max_tokens` from provider docs, live `/models` metadata, or a model card. If sources conflict, prefer the lower safe value and mention the source in the PR.
5. **Do not guess thinking support.** Only add `thinking_levels`, `thinking_parameter`, `reasoning = true`, or `thinking_level_map` when the provider/model accepts those request fields. Mark unsupported levels with `unsupported_thinking_levels` rather than exposing modes that will fail at runtime.
6. **Be explicit about pricing.** Use current provider pricing when billing is metered. Use zero-cost entries only for genuinely free developer endpoints or free-tier router models, and call that out in the PR.
7. **Check model capabilities.** Set `input = ["text"]` or `input = ["text", "image"]` based on the provider's supported payloads for that exact model.
8. **Avoid undocumented compatibility flags.** Add `compat`, `headers`, or per-model `api` overrides only when needed for Tau's transport layer and cover them with tests.
9. **Update user docs for new built-ins.** If a new provider becomes available through `/login`, update the providers guide under `website/content/guides/`.
10. **Add focused tests.** Extend provider-order tests and add or update a golden-entry test that covers the risky fields: `api`, model order, default model, context windows, thinking fields, and representative model metadata.

## Validation commands

Run targeted checks through `uv` from the repo root:

```bash
uv run pytest tests/test_provider_catalog.py tests/test_provider_config.py -q
uv run ruff check src/tau_coding/data/catalog.toml tests/test_provider_catalog.py tests/test_provider_config.py
```

For larger catalog changes, also parse the whole built-in catalog and inspect the changed entry:

```bash
uv run python - <<'PY'
from tau_coding.catalog_loader import builtin_catalog
entry = next(e for e in builtin_catalog() if e.name == "provider-name")
print(entry)
PY
```

If the provider offers a safe unauthenticated or credentialed smoke test and credentials are available, note the exact request or command in the PR. Do not commit credentials, generated local config, or live-test artifacts.
