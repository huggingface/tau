# Tau providers and models

Tau separates provider/model streaming (`tau_ai`), the portable harness (`tau_agent`), and application configuration (`tau_coding`).

## User configuration

Use `/login` and `/model` for built-in providers. The custom-provider flow supports OpenAI-compatible endpoints. Durable provider settings live under Tau's home directory; consult the published `website/content/guides/providers-and-models.md` in a Tau checkout for the current schema and authentication behavior.

## Changing the built-in catalog

Use the bundled `tau-model-catalog` skill when adding or updating a first-party model/provider. The source of truth is:

```text
src/tau_coding/data/catalog.toml
```

Do not guess model IDs, context limits, modalities, reasoning values, output limits, or pricing. Verify them in official provider documentation. Confirm Tau supports the transport before adding a provider. Preserve existing defaults unless a change is intentional.

Relevant tests usually include:

```text
tests/test_provider_catalog.py
tests/test_provider_config.py
tests/test_provider_runtime.py
```

Update published provider docs and add a development note for substantial user-facing changes.
