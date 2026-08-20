# Tau providers and models

Tau separates provider/model streaming (`tau_ai`), the portable harness (`tau_agent`), and application configuration (`tau_coding`).

## User configuration

Use `/login` and `/model` for built-in providers. The custom-provider flow supports OpenAI-compatible endpoints. Durable provider settings live under Tau's home directory; consult the published `website/content/guides/providers-and-models.md` in a Tau checkout for the current schema and authentication behavior.

## Durable versus dynamic providers

Catalog providers are durable user/application configuration. Extension providers
are source- and generation-owned overlays held only by an `ExtensionRuntime`.
The host identifies each source by its canonical entry path, independently of the
extension's display name. It freezes that identity before importing extension code,
so later symlink mutation cannot change ownership or failed-setup cleanup; same-name
files from different paths therefore cannot replace or remove each other's layers.
Their definitions and refresh snapshots must never be copied into `catalog.toml`,
`providers.json`, session metadata, or a generic
disk cache. Removing the final dynamic layer restores the original complete durable
provider object.

Dynamic providers support required, optional, or absent authentication without
fake keys. Secrets are resolved immediately before refresh/runtime creation;
resolved keys, headers, and extension-provided auth provenance are excluded from
representations and diagnostics. Custom runtime-auth exceptions become categorical
host errors, while Tau's required-key strategy retains missing-credential guidance.
Nested compatibility metadata is deeply frozen
inside registered definitions and copied back to ordinary JSON containers only at
the transport boundary. Phase 1 provides the provider contracts and registry
mechanics. The Phase 4 local-backend host adds `/local` for registered backends;
it does not turn a dynamic provider into a durable catalog entry or silently
select one at startup. Use an explicit `--provider`/`--model` or the TUI's
**Use** action after a backend has discovered a model.

## `/local` and dynamic local backends

In the TUI, `/local` first opens an explicit backend chooser. A single backend
is preselected and may be marked recommended, but it still requires confirmation.
The backend screen exposes configure, refresh, status, use, doctor, reset, and
model-management actions only when the backend declares them. Configuration
fields are host-rendered structured text, secret, or choice values; cancellation,
validation failure, and failed backend commits leave the prior state untouched.

Dynamic providers are process-local overlays. A backend may persist safe
integration state through its own versioned store, but provider definitions,
secrets, and arbitrary response data do not belong in `catalog.toml`,
`providers.json`, sessions, or generic extension state. Stored credentials are
referenced indirectly, and diagnostics identify only their source. The generic
host never probes endpoints on its own and never invents an API key.

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

When withdrawing a model from one built-in provider, add it to that provider's
`removed_models` list. Tombstones are applied after user overlays, preventing
stale saved catalog definitions from restoring an unroutable provider/model
combination while leaving the same model ID available on other providers.

Test both the exposed levels and the actual API value produced by provider configuration. Update `website/content/guides/providers-and-models.md` and add a beginner-friendly development note for substantial user-facing changes. Inspect `src/tau_coding/data/release-notes/releases.json`, but update it only when appropriate.

Run focused provider tests followed by the repository's full pytest, Ruff, formatting, and mypy checks. Build the website when published provider documentation changes.
