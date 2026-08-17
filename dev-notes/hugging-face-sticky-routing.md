# Hugging Face session routing and cache adaptation

Issues: <https://github.com/huggingface/tau/issues/559>,
<https://github.com/huggingface/tau/issues/572>

## What changed

Tau pins a logical model from its built-in `huggingface` provider to one
explicit Hugging Face Inference Provider. Users can configure a per-model
`inference_providers` map in `~/.tau/providers.json`. Without a preference, the
first request uses automatic routing; after it succeeds, Tau reads the
normalized `response_provider` value and rebuilds the runtime with that explicit
suffix. The session index records the route and whether it came from an explicit
choice or automatic routing.

The OpenAI-compatible runtime now supports an internal logical-to-wire model alias.
For example, the harness and persisted assistant messages continue to use
`zai-org/GLM-5.2`, while the request payload uses
`zai-org/GLM-5.2:deepinfra`. This avoids duplicating every suffixed route in the
catalog and keeps context windows, capabilities, pricing, thinking controls, and
model selection keyed by the logical model.

Automatic routes now receive a bounded cache check. Requests below 4,096 prompt
tokens are ignored. The first eligible request on a route is a cold warm-up; two
later append-only requests with absent cache telemetry or an explicit zero cache
read trigger candidate discovery. A positive cache read retains the route. Tau
tries at most three routes across at most nine eligible requests, and keeps the
current route if discovery or all remaining candidates fail.
Candidate discovery filters the model API mapping to validated `status: live`,
`task: conversational` suffixes, sorts them lexicographically, and skips routes
already attempted in the session.

`/session` reports the pin and evaluation phase. Every automatic route change is
a typed coding-session event consumed by print and TUI frontends. Manual route
selection remains available through the external Hugging Face extension. An
explicit route locks evaluation; `/route automatic` resets it. Historical
records with a pin but no source field are treated as explicit, so an upgrade
cannot silently take over a user's existing route.

## Why it exists

Hugging Face Router automatically chooses a backing provider for unsuffixed model
IDs. Real Tau sessions showed working automatic prefix caching, but occasional
full misses only seconds after large cache reads, followed immediately by another
large hit. That pattern is consistent with requests moving between provider or
worker cache domains rather than normal TTL expiry.

In the motivating GLM-5.2 comparison, `deepinfra` reported roughly 99% reuse
after its cold request. Five append-only requests through `scaleway` returned
null cache details for an approximately 12.5k-token prefix and incurred about
$0.13 in Hugging Face billing, consistent with processing the prompt as fresh.
That is evidence about reported reuse and billing, not proof that a backend has
no internal cache.

An explicit `:<provider>` suffix narrows one source of routing changes. It does
not guarantee a cache hit: the selected provider can still evict entries,
load-balance across workers, or expire them.

## Architecture

Provider preferences, adaptive policy, session metadata, and model-route
selection remain in `tau_coding`. State snapshots use the existing session
`CustomEntry` mechanism, so evidence, unavailable candidates, and transitions
resume with the active branch. The reusable `tau_agent` harness receives the
logical model and has no Hugging Face-specific behavior. `tau_ai` and the
provider-neutral `Usage` model expose only whether a cache-read counter was
reported, which distinguishes absent telemetry from a reported zero.

[Hugging Face's own Chat UI](https://github.com/huggingface/chat-ui/blob/main/src/lib/server/endpoints/openai/endpointOai.ts)
consumes `x-inference-provider` from OpenAI-compatible responses, so Tau uses
that header rather than guessing which mapping is fastest.
The pin is committed only after a successful stream. Existing OpenAI-compatible
retries keep the same wire model and stop retrying after streamed model output.

Tau deliberately does not fail over an explicit pin or send provider-specific
cache-affinity fields. Errors, aborts, model changes, compaction boundaries,
route mismatches, and non-append-only contexts do not add cache-failure evidence.
Backing providers differ in accepted affinity fields, so no unknown field is
enabled for the entire gateway.

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

Use `/session` to confirm the selected route. For opt-in live
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
