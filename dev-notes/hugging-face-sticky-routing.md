# Hugging Face session-scoped provider routing

Issue: <https://github.com/huggingface/tau/issues/559>

## What changed

Tau pins a logical model from its built-in `huggingface` provider to one
explicit Hugging Face Inference Provider. Users can configure a per-model
`inference_providers` map in `~/.tau/providers.json`. Without a preference, the
first request uses automatic routing; after it succeeds, Tau reads the
`x-inference-provider` response header and rebuilds the runtime with that explicit
suffix. The session index records the route for resume.

The OpenAI-compatible runtime now supports an internal logical-to-wire model alias.
For example, the harness and persisted assistant messages continue to use
`zai-org/GLM-5.2`, while the request payload uses
`zai-org/GLM-5.2:deepinfra`. This avoids duplicating every suffixed route in the
catalog and keeps context windows, capabilities, pricing, thinking controls, and
model selection keyed by the logical model.

`/session` and `/route` report the pin. `/route <provider>` changes it and
`/route automatic` resets resolution. Switching logical models selects the new
model's configured pin or returns to automatic Hugging Face routing when none
exists. Existing records without the optional field remain compatible and pin
after their next successful automatic response.

## Why it exists

Hugging Face Router automatically chooses a backing provider for unsuffixed model
IDs. Real Tau sessions showed working automatic prefix caching, but occasional
full misses only seconds after large cache reads, followed immediately by another
large hit. That pattern is consistent with requests moving between provider or
worker cache domains rather than normal TTL expiry.

An explicit `:<provider>` suffix narrows one source of routing changes. It does
not guarantee a cache hit: the selected provider can still evict entries,
load-balance across workers, or expire them.

## Architecture

Provider preferences, session metadata, and model-route selection remain in
`tau_coding`. The reusable `tau_agent` harness receives the logical model and has
no Hugging Face-specific behavior. `tau_ai` only gains a provider-neutral
`model_aliases` transport option, used to put a different model ID in the wire
payload while preserving the logical model in normalized events.

[Hugging Face's own Chat UI](https://github.com/huggingface/chat-ui/blob/main/src/lib/server/endpoints/openai/endpointOai.ts)
consumes `x-inference-provider` from OpenAI-compatible responses, so Tau uses
that header rather than guessing which mapping is fastest.
The pin is committed only after a successful stream. Existing OpenAI-compatible
retries keep the same wire model and stop retrying after streamed model output.

The first version deliberately does not automatically fail over a stale explicit
pin or send provider-specific cache-affinity fields. Safe failover also needs a
user-visible reroute event plus durable retry/reroute telemetry; silently falling
back would hide temporary cache-locality loss. Users can explicitly reset with
`/route automatic`. Backing providers differ in accepted affinity fields, so no
unknown field is enabled for the entire gateway.

## Configure and validate

```json
{
  "schema_version": 2,
  "default_provider": "huggingface",
  "provider_preferences": {
    "huggingface": {
      "default_model": "zai-org/GLM-5.2",
      "inference_providers": { "zai-org/GLM-5.2": "deepinfra" }
    }
  },
  "scoped_models": []
}
```

Use `/session` or `/route` to confirm the selected route. For opt-in live
validation, start a new session, issue repeated requests that append to the same
long prefix, and compare cache-read counts before and after automatic resolution.
Do not commit credentials or generated session artifacts.

Project checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
