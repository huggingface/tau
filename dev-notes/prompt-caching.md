# Anthropic prompt caching

Tau's Anthropic provider parsed and priced cache usage from the very first
release — `Usage.cache_read`, `Usage.cache_write`, and `cache_write_1h` are all
read out of `message_start` — but it never placed a single `cache_control`
breakpoint in a request. The reporting half was wired up and the requesting half
was not, so those counters read zero on every real turn and each request re-billed
the system prompt, the whole tool schema block, and the entire conversation as
fresh input. In a coding session that is expensive: the tool schemas alone run to
tens of thousands of tokens and are resent on every tool call.

## The breakpoint budget

Anthropic rejects a request carrying more than four `cache_control` markers, and
the cache prefix is evaluated in `tools` → `system` → `messages` order. Tau spends
all four, in `_build_messages_payload`:

1. The **last tool** in the array, which caches the whole schema block.
2. The **final system block only**. When OAuth is active the system field holds a
   Claude Code identity block followed by Tau's prompt; a breakpoint on the
   identity block would cache a ~15-token prefix that the block after it already
   covers, so it would waste a slot.
3. and 4. **Two message positions** — this request's tail, and the previous
   request's tail.

Note that "request" is not "user turn". One prompt from the user drives as many
requests as the agent needs tool-call round trips, and all four markers are
recomputed on every one of them. Positions 3 and 4 therefore roll forward several
times within a single user turn, not once per thing the user types.

It also matters that Anthropic's wire format sends tool results with
`role: "user"`. The two message positions are the tails of *requests*, and a
mid-turn request ends with the batch of tool results the agent just collected — so
in practice these breakpoints land on `tool_result` blocks far more often than on
anything a human wrote. A prompt from the user is position 4 only on the first
request of a turn, and position 3 only on the second.

Two message breakpoints rather than one because Anthropic checks at most 20 block
positions back from a breakpoint before giving up. One Tau turn appends `2N+2`
blocks for `N` tool calls (one text block, `N` `tool_use` blocks, and `N`
single-block `tool_result` user messages), so a turn with nine or more parallel
tool calls pushes the previous cache entry outside that window and misses. Marking
where the previous request ended opens a second lookback window there. Breakpoints
are not themselves billed, so a marker that already hits costs nothing.

That second position is *reconstructed from the payload* rather than remembered
across requests, and it is not a fixed distance back. Tau's transcript is
append-only and every request stops immediately before the assistant message it
produces, so the last user-role message preceding the final assistant message is
exactly where the previous request's tail breakpoint sat — again, usually a
`tool_result`. `_previous_request_boundary` walks back to find it. Two cases
return an older position than the literal previous request — a turn whose
assistant message was empty and errored or aborted is filtered out of provider
context by `_provider_context`, and consecutive assistant messages leave no user
message at the true boundary — but both only shorten the reusable prefix rather
than corrupting anything.

`_mark_cache_breakpoint` will only mark `text`, `image`, or `tool_result` blocks,
promotes a string `content` to a one-element text block first, and skips empty
text and empty `tool_result` content, because a breakpoint on an empty block is
rejected outright.

## Retention

`AnthropicConfig.cache_retention` is a `Literal["none", "short", "long"]`:

- `long` emits `ttl: "1h"`. Applied to Anthropic **subscription OAuth** only.
  Subscription auth is not billed per token, and the default five-minute TTL is
  shorter than a test run, a build, or reading a diff — all of which would
  otherwise expire the prefix mid-session. Note that Pi deliberately defaults to
  five minutes, but for a reason that does not apply here: Pi is not a permitted
  subscription harness, so its users pay per-token API prices where the one-hour
  write premium does not pay off. Claude Code itself uses one hour for its own
  subscription users. See "Prompt Caching In Agents" in the sources below.
- `short` is the provider default TTL. This is what **API-key** Anthropic auth
  gets, so nobody silently pays the 2x cache-write premium they did not ask for.
- `none` emits no breakpoints at all and leaves the payload byte-identical to the
  pre-caching shape, plain-string `system` field included.

`none` exists because several catalog providers speak the Anthropic wire protocol
through a gateway rather than being Anthropic. `minimax`, `minimax-cn`,
`fireworks`, and `vercel-ai-gateway` are all `kind = "anthropic"` and so route
through `anthropic_config_from_provider`, and `vercel-ai-gateway` proxies to
non-Anthropic models entirely. Those backends may reject `cache_control` blocks or
the block-list `system` field.

Capability and intent are resolved separately, in `anthropic_cache_settings`.
Intent comes from the auth mode: OAuth wants `long`, an API key wants `short`.
Capability comes from three `compat` booleans, layered detected default → provider
compat → per-model compat, exactly like `forceAdaptiveThinking`:

| Key | Effect when `false` |
| --- | --- |
| `supportsCacheControl` | Resolves to `none`. Detected `false` for any host that is not `api.anthropic.com` |
| `supportsLongCacheRetention` | Clamps `long` to `short` |
| `supportsCacheControlOnTools` | Drops only the tools breakpoint |

Capability only ever narrows intent, so the two compose with no precedence rule.
That also makes the failure mode recoverable without a source edit: if Anthropic
ever stops honoring `ttl: "1h"` on subscriptions the request 400s and tau does not
retry 400s, but a three-line catalog overlay setting
`supportsLongCacheRetention = false` clamps it back to five minutes.

Two of these keys already existed in the catalog before anything read them —
`_detected_compat` emitted `supportsLongCacheRetention` and the Fireworks model
entries carry both it and `supportsCacheControlOnTools`, mirrored in from Pi. This
wiring makes that data live rather than inventing a parallel vocabulary.

## Observability

Without a visible hit rate there is no way to tell caching is working except by
watching a rate limit, so `SessionStats` now accumulates `cached_input_tokens` and
`cache_write_tokens` and exposes a `cache_hit_rate` property. The session sidebar
renders it between the token counts and the cost estimate. The rate is `None` —
and the sidebar omits it — when no provider in the branch reported any cache
activity at all, so backends without prompt caching are not shown a permanent
misleading `0%`.

## Validate

```bash
uv run pytest tests/test_prompt_caching.py tests/test_provider_runtime.py \
  tests/test_tau_ai.py tests/test_session_stats.py tests/test_tui_app.py
uv run ruff check .
uv run mypy
```

`tests/test_prompt_caching.py` covers the awkward transcripts rather than the
happy path: the four-breakpoint ceiling, a twelve-call parallel turn, adjacent
assistant messages, image and `tool_result` tails, empty content, and that the
caller's messages are never mutated.

To confirm end to end against the live API, drive `_build_messages_payload`
directly with `cache_retention="long"` over successive turns and read
`cache_read_input_tokens` and `cache_creation.ephemeral_1h_input_tokens` off the
response. A cold turn writes, the next turn reads back what it wrote, and a turn
adding more than 20 blocks still reads — that last case is the one the second
message breakpoint exists for.

## Sources

- <https://platform.claude.com/docs/en/build-with-claude/prompt-caching> — the
  four-breakpoint limit, the `tools` → `system` → `messages` prefix order, the
  20-block lookback window (with a worked example matching Tau's wide-turn case),
  and the rule that breakpoints are not themselves billed.
- "Prompt Caching In Agents", Earendil Engineering, 22 July 2026 —
  <https://earendil.com/posts/prompt-caching/>. Why Pi's five-minute default is a
  licensing artifact rather than a recommendation, why idle gaps rather than bad
  breakpoint placement dominate real-world misses, and the break-even argument for
  not pruning tool results to save tokens.
- `packages/ai/src/api/anthropic-messages.ts` in Pi — the reference
  implementation Tau's placement is adapted from. Tau differs in two ways: it does
  not mark the OAuth identity block, and it spends the freed slot on a second
  message breakpoint.
