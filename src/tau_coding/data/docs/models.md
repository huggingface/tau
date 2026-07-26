# Tau providers and models

Tau separates provider/model streaming (`tau_ai`), the portable harness (`tau_agent`), and application configuration (`tau_coding`).

## User configuration

Use `/login` and `/model` for built-in providers. The custom-provider flow supports OpenAI-compatible endpoints. Durable provider settings live under Tau's home directory; consult the published `website/content/guides/providers-and-models.md` in a Tau checkout for the current schema and authentication behavior.

## Changing the built-in catalog

For changes to a first-party provider or model, use this workflow:

1. Decide whether this is a model on an existing provider or a new provider.
2. Verify the exact model ID, endpoint, transport, authentication, context window, modalities, output limit, reasoning values, pricing, and plan restrictions in official provider documentation. Never guess undocumented metadata.
3. Confirm Tau supports the API transport before adding a provider.
4. Update the catalog source of truth:

   ```text
   src/tau_coding/data/catalog.toml
   ```

5. Preserve the existing `default_model` unless changing it is intentional, and match nearby TOML compatibility metadata.
6. Test provider membership, context and model metadata, thinking-level filtering and wire mappings, runtime construction when the transport changes, and intentionally preserved defaults. Relevant tests usually include:

   ```text
   tests/test_provider_catalog.py
   tests/test_provider_config.py
   tests/test_provider_runtime.py
   ```

Tau thinking levels are `off`, `minimal`, `low`, `medium`, `high`, and `xhigh`. Provider-level `thinking_levels` must include every level needed by its models. Use model metadata when the wire value differs or a model supports only a subset:

```toml
thinking_level_map = { xhigh = "max" }
unsupported_thinking_levels = ["off", "minimal", "low", "medium", "high"]
```

Test both the exposed levels and the actual API value produced by provider configuration. Update `website/content/guides/providers-and-models.md` and add a beginner-friendly development note for substantial user-facing changes. Inspect `src/tau_coding/data/release-notes/releases.json`, but update it only when appropriate.

Run focused provider tests followed by the repository's full pytest, Ruff, formatting, and mypy checks. Build the website when published provider documentation changes.
