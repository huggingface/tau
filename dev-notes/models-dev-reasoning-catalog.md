# Generated models.dev reasoning catalog

## What changed

Tau now augments the hand-reviewed provider catalog with a checked-in,
build-time snapshot of per-model `reasoning_options` from
`https://models.dev/api.json`. The generated
`src/tau_coding/data/models-dev-reasoning.json` contains only reasoning-effort
maps for models already present in Tau's catalog; it cannot add providers,
models, endpoints, credentials, or unrelated capabilities.

For each OpenAI-compatible provider using `reasoning_effort` or
`reasoning.effort`, generation maps verified values to Tau's six UI levels:

- `none` becomes `off`;
- `minimal`, `low`, `medium`, `high`, and `xhigh` map directly;
- `max` becomes `xhigh` when a literal `xhigh` value is absent;
- every unverified level is stored as unavailable.

An empty `reasoning_options` list therefore disables provider-wide effort
controls for that model. Tau omits `reasoning_effort` and lets the upstream
router use its safe model default instead of sending an unverified global
`medium`. The current models.dev Hugging Face record for `zai-org/GLM-5.2` is
such a case. If a later snapshot advertises `none`, `high`, and `max`, generation
automatically exposes `off`, `high`, and `xhigh` and sends those exact values.

## Why build time

This follows Pi's generated-catalog approach without adding network I/O, cache
state, or a startup failure mode. Tau always starts offline. If the generated
resource is missing or invalid, catalog loading silently keeps the hand-reviewed
`catalog.toml` behavior. User catalog overlays are applied after generated
metadata, so explicit user configuration still wins.

Provider aliases are explicit (`together` to `togetherai`, `kimi-code` to
`kimi-for-coding`) rather than guessed. Models absent from either side are
ignored.

## Refreshing the snapshot

From the repository root:

```bash
uv run python scripts/generate_models_dev_reasoning.py
```

For deterministic tests or an audited download:

```bash
uv run python scripts/generate_models_dev_reasoning.py \
  --source /path/to/api.json \
  --output /tmp/models-dev-reasoning.json
```

Review the generated diff before committing it. The source changes over time and
can alter which thinking levels users see.

## Validation

Focused coverage lives in `tests/test_models_dev.py` and the provider catalog and
configuration suites. It covers effort conversion, `xhigh`/`max`, full-level
models, empty options, provider/model scoping, malformed-resource fallback, and
GLM-5.2 wire omission.
