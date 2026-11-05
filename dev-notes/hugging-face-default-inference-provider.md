# Hugging Face default inference provider

## What changed

Tau's bundled Hugging Face catalog now sets `default_inference_provider` to
`openrouter`. New sessions send `logical-model-id:openrouter` through the
Hugging Face router, without needing OpenRouter to appear in
`inferenceProviderMapping`. Authentication and billing remain with Hugging Face;
this is independent of Tau's direct `openrouter` provider.

The matching `provider_preferences.huggingface.default_inference_provider` field
in `providers.json` accepts an explicit provider name or `null`. A string replaces
the bundled default. Explicit `null` opts out and restores unsuffixed automatic
routing. Null is serialized rather than omitted, so unrelated preference saves
cannot silently restore OpenRouter. Removing the preference inherits the catalog
default again.

## Resolution and session behavior

1. Preserve the active/restored route and mode for the same provider/model.
2. For a new selection, use its per-model `inference_providers` entry.
3. Otherwise use the provider-wide `default_inference_provider`.
4. If that is null, use Hugging Face's unsuffixed automatic routing.

Defaults are fixed routes, not availability probes. An unsupported route fails
normally; Tau does not silently switch services. Model discovery describes
advertised routes but is not an allowlist for request suffixes. Logical IDs,
catalog lookups, and displayed model names stay unsuffixed.

`set_inference_provider(None)` and the external extension's `/hf route automatic`
explicitly choose recoverable automatic routing. Reload and resume preserve that
choice; switching models resolves the destination's preferences again. Automatic
mode still pins the successful response's `x-inference-provider` and retains its
existing bounded pre-output failover. Tau does not translate automatic mode to
`:cheapest` or `:fastest`.

## Architecture and Pi mapping

Like Pi's provider/model configuration, route defaults are application policy in
`tau_coding`, not behavior added to the reusable `tau_agent` brain. The catalog
owns the shipped default; `providers.json` owns personal preferences. A shared
resolver is used by CLI print/RPC, TUI startup, staged session loading, and model
switches. The transport still only appends the explicitly resolved suffix, so an
explicit `None` reset or failover cannot accidentally reapply the default.

## Validation

Deterministic tests cover catalog parsing, fixed defaults, preference validation,
string/null round trips, unrelated saves, provider updates, print/RPC/TUI startup,
explicit automatic and sticky session routes, model switches, reload, unsupported
routes, and the exact OpenRouter wire-model alias. No inference requests are
needed for these tests.

```bash
uv run pytest tests/test_huggingface_default_routing.py \
  tests/test_huggingface_frontend_routing.py \
  tests/test_huggingface_provider_preferences.py tests/test_provider_runtime.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For manual validation, start a fresh Hugging Face session with `HF_TOKEN` set,
then inspect `/session`: the route should be `openrouter (fixed)`. With the
Hugging Face extension loaded, `/hf route automatic` should survive `/reload`
and resume. Set the JSON default to `null`, start a new session, and verify
it starts automatic; a per-model override should still win. Unsupported models
should report Hugging Face's route error without silently falling back.
