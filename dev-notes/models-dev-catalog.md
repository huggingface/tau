# Generated models.dev catalog

## What changed

Tau now follows Pi's build-time model-generation design. The application-owned
`src/tau_coding/data/catalog.toml` remains the provider transport/auth/default
configuration and offline fallback. A generator fetches
`https://models.dev/api.json` and writes the complete checked-in model snapshot
to `src/tau_coding/data/models-dev-catalog.json`.

For each Tau provider with a models.dev counterpart, generation:

- includes every non-deprecated model that advertises tool calling and text I/O;
- retains catalog-only rows as explicit Tau/provider corrections;
- copies names, reasoning support, text/image input, cost, context limits, and
  output limits;
- converts verified reasoning effort options with Pi's semantics;
- keeps explicit provider-name aliases, such as `together` to `togetherai` and
  `kimi-code` to `kimi-for-coding`.

The generated inventory replaces the fallback inventory for covered providers.
Provider endpoints, API transports, authentication, defaults, compatibility
flags, and manual model corrections still come from `catalog.toml`. User
`~/.tau/catalog.toml` overlays are applied after both and therefore remain the
last word.

## Thinking levels

Tau now matches Pi's thinking-level vocabulary: `off`, `minimal`, `low`,
`medium`, `high`, `xhigh`, and `max`. `none` maps to `off`; `max` remains a
distinct selectable level rather than being translated to `xhigh`. Unsupported
levels are represented by null mappings.

Pi emits no generated map when `reasoning_options` is empty, toggle-only, or has
no usable effort values. In that case Tau retains its provider/manual behavior.
The current models.dev Hugging Face GLM-5.2 row has an empty list, so Tau keeps a
narrow provider-validated catalog correction for its accepted `none`, `high`,
and `max` values. This prevents the unsafe provider-wide `medium` default.

If generated constraints make a remembered `providers.json` thinking default
unavailable, Tau ignores that stale preference and resolves a safe current
default instead of failing startup.

## Runtime and failure behavior

Tau never contacts models.dev during startup. It validates and layers the
packaged snapshot in memory. Missing, malformed, or catalog-incompatible
generated data falls back silently to `catalog.toml`, preserving offline startup.
`providers.json` remains preference-only and is neither regenerated nor used as
model-definition storage.

A new models.dev model becomes available in Tau after the snapshot is regenerated
and shipped in a Tau release; no hand edit to the model inventory is required.

## Refreshing the snapshot

From the repository root:

```bash
uv run python scripts/generate_models.py
```

For deterministic tests or an audited download:

```bash
uv run python scripts/generate_models.py \
  --source /path/to/api.json \
  --output /tmp/models-dev-catalog.json
```

Review the generated diff before committing it. Source changes alter user-facing
model lists, limits, costs, modalities, and thinking controls.

## Validation

Focused coverage lives in `tests/test_models_dev.py` and the provider catalog,
configuration, runtime, and thinking suites. It covers Pi-compatible effort
conversion, distinct `max`, full model generation, new-model discovery,
provider aliases, malformed-resource fallback, user preference fallback, and
GLM-5.2 wire behavior.
