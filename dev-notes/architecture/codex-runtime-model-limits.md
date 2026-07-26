# Codex runtime model limits

Issue: [#389](https://github.com/huggingface/tau/issues/389)

## Production incident

A long-running Tau session used `gpt-5.6-sol` through the `openai-codex` OAuth
provider. Its last successful request reported roughly 371,440 cached plus
uncached input tokens. The next request failed with
`context_length_exceeded`.

Tau had copied the direct OpenAI API's 1.05M context metadata into the Codex
subscription catalog, so its default compaction threshold was 1,033,616 tokens.
The request actually went to `https://chatgpt.com/backend-api/codex/responses`,
where Codex subscription limits are independently configured. Reports from the
server-delivered Codex catalog showed profiles around 372K and, in later
rollouts, 272K. Tau therefore never compacted before the real backend ceiling.

This is a serving-surface problem, not a contradiction in the public API model
page: direct API access and ChatGPT/Codex OAuth can use different limits for the
same model ID.

## Directions considered

### Hard-code a 500K Codex window

This is deterministic and avoids another startup request, but 500K is a total
budget often described as 372K input plus 128K output. Tau's old compaction
formula treated `context_window` as the usable input budget and subtracted only
16,384 tokens. A 500K entry would therefore compact around 483K, after the
reported 372K input ceiling, and would not fix the incident.

A lower hard-coded value is useful as a safe fallback. This change uses 272K for
GPT-5.6 Codex variants, matching the most conservative recently observed Codex
catalog profile. Direct OpenAI API entries remain 1.05M. Static values alone are
not the long-term answer because Codex has changed them during rollouts and can
vary them by account.

### Discover the authenticated Codex catalog

The official Codex client requests:

```text
GET <codex-base>/models?client_version=<version>
```

For Tau's default base URL, that resolves to:

```text
https://chatgpt.com/backend-api/codex/models
```

The request uses the same refreshed OAuth token, ChatGPT account ID, and
originator headers as response requests. Model entries can include:

```text
slug
context_window
max_context_window
effective_context_window_percent
auto_compact_token_limit
```

Dynamic discovery reflects the actual serving surface and account. Its drawback
is that this is a Codex product endpoint rather than the public OpenAI API model
contract, so its schema or availability can change.

## Decision

Use **dynamic discovery with a conservative static fallback**.

`tau_ai` owns the authenticated request and defensive parsing. It implements the
optional provider-neutral `ModelLimitsProvider` capability and returns a typed
`RuntimeModelLimits` value. It ignores malformed entries and caches one catalog
per provider instance.

`tau_coding` asks for limits when a session loads and again after a provider or
model switch, before sending the next prompt. It applies the live raw context
window and either the provider's explicit compaction limit or Codex's 90% raw
window default. Discovery failures are non-fatal: the configured provider
catalog remains active and `/session` reports the source and error.

`tau_agent` is unchanged. It receives a ready provider and remains independent
of OAuth, model catalogs, Tau home paths, and compaction policy.

This first implementation deliberately avoids a persistent live-catalog cache.
An in-memory cache prevents duplicate requests within one runtime provider;
every new session gets a fresh account-specific value. A disk cache would need
an expiry policy, ETag handling, credential/account partitioning, atomic writes,
and a clear rule for whether stale values are safer than conservative built-in
fallbacks. Those concerns can be added later without changing the discovery
protocol.

## Limit semantics

`RuntimeModelLimits` keeps four concepts separate:

- raw context window
- maximum output tokens, when advertised
- effective context percentage
- explicit auto-compaction threshold, when advertised

When no threshold is returned, Tau uses 90% of the raw context window and clamps
it to the effective window. This mirrors the official Codex client's default and
leaves headroom for provider framing, tools, instructions, and output.

Tau's character-based usage estimator remains approximate. Earlier compaction
is intentional near a hard remote limit.

## Failure behavior

Catalog discovery cannot prevent every overflow: the backend can change between
discovery and a response, or token estimation can undercount provider framing.
Tau's existing overflow path remains the second line of defense: recognize a
context overflow, compact older messages, and retry once. Dynamic discovery
makes the proactive path accurate enough that emergency recovery should be
rare.

## Testing

Deterministic tests use `httpx.MockTransport` and a fake model-limit provider.
They cover:

- authenticated Codex model-catalog URL and headers
- defensive parsing and in-memory caching
- live context/compaction values in a coding session
- non-fatal fallback when discovery fails
- separation between direct API and Codex built-in metadata

Run:

```bash
uv run pytest tests/test_tau_ai.py tests/test_coding_session.py \
  tests/test_provider_catalog.py tests/test_provider_runtime.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
cd website && hugo --minify && npx --yes pagefind@latest --site public
```

## Manual validation

1. Log in with `/login openai-codex`.
2. Select a GPT-5.6 model.
3. Start or resume a session.
4. Run `/session`.
5. Confirm that `Context window source` is `provider live catalog` and that the
   displayed limit/compaction threshold match `codex debug models` for the same
   account.
6. Block the models request or use invalid discovery data and confirm Tau starts
   with `configured catalog`, reports the non-fatal discovery failure, and can
   still send a normal model request.
