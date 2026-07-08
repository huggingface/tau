# Provider catalog live-validation runbook

This runbook explains how we validated the Pi-derived API provider catalog and how to
repeat the process when adding or changing providers/models.

The goal is not to benchmark answer quality. The goal is to prove that every model and
every thinking/reasoning level shown by Tau can be accepted by the provider API with the
payload Tau sends.

## When to use this runbook

Run live validation when:

- importing or regenerating provider catalog metadata
- adding a provider runtime adapter
- changing reasoning/thinking payloads
- changing model lists or default models
- seeing provider errors such as deprecated model IDs or unsupported reasoning levels

Do **not** run it casually against large catalogs. Even tiny prompts can consume credits
when repeated across hundreds of models and reasoning levels.

## What counts as validated

For this pass we used a tiny prompt:

```text
Reply with exactly: OK
```

A request counted as valid once Tau received a provider `response_start` event. That means:

1. Tau built the runtime provider successfully.
2. Credentials were accepted well enough to make the request.
3. The provider accepted the model ID.
4. The provider accepted the reasoning/thinking payload for that level.
5. The endpoint started a streaming response.

This intentionally stops early. Waiting for full text output validates more of the stream,
but costs more tokens. Use full-response validation only for a small sample or when testing
stream parsing behavior.

## Validation matrix

The matrix is:

```text
credentialed providers
  × provider.models
  × provider_thinking_levels(provider, model)
```

For non-reasoning models, validate one request with no thinking level.

Tau has two useful catalog scopes:

- **Effective catalog**: built-in catalog plus `~/.tau/catalog.toml` user overlays. This
  matches what the UI shows on a specific machine.
- **Packaged catalog**: built-in catalog only. Use this for PR verification so local user
  overlays do not mask or reintroduce stale models.

In this pass, we first validated the effective catalog to catch what the UI showed locally,
then reran summaries and retries with `--builtins-only` to verify the PR catalog itself.

## Local validation helper

We used a temporary helper script at:

```text
~/.tau/provider-validation/validate_provider_catalog.py
```

The script is intentionally resumable. Each attempt appends one JSON object to:

```text
~/.tau/provider-validation/pi-api-provider-catalog/results.jsonl
```

The key fields are:

| Field | Meaning |
|---|---|
| `provider` | Tau provider name |
| `model` | Model ID sent to the provider |
| `thinking_level` | Tau thinking level, or `null` for non-reasoning validation |
| `status` | `ok` or `error` |
| `message` | Provider/runtime error message for failed attempts |
| `data.status_code` | HTTP status when available |
| `data.body` | Truncated provider error body when available |

The script supports three operations:

```bash
# Show credentialed providers, missing credentials, and total attempt counts.
uv run python ~/.tau/provider-validation/validate_provider_catalog.py plan

# Run live validation.
uv run python ~/.tau/provider-validation/validate_provider_catalog.py run \
  --builtins-only \
  --concurrency 4 \
  --provider-concurrency 2 \
  --timeout-seconds 30 \
  --max-retries 0

# Summarize recorded results.
uv run python ~/.tau/provider-validation/validate_provider_catalog.py summarize \
  --builtins-only \
  --top-errors 12
```

Important flags:

| Flag | Use |
|---|---|
| `--builtins-only` | Ignore `~/.tau/catalog.toml`; validate the packaged PR catalog only. |
| `--provider NAME` | Limit the run to one provider; repeatable. |
| `--limit N` | Run only the next N pending attempts. Useful for sampling. |
| `--retry-errors` | Retry attempts that already have error rows. Use after fixes. |
| `--concurrency N` | Global maximum concurrent calls. |
| `--provider-concurrency N` | Maximum concurrent calls per provider. |
| `--wait-for response_start` | Cheapest success condition; validates request acceptance. |
| `--wait-for text` | More expensive; validates that text actually streams. |

The script skips attempts that already have a result unless `--retry-errors` is set.

## Safe concurrency defaults

Start conservative:

```bash
--concurrency 4 --provider-concurrency 2 --max-retries 0
```

For expensive providers or accounts with low limits, use:

```bash
--concurrency 1 --provider-concurrency 1
```

For OpenRouter, we used low per-provider concurrency because the catalog is large and many
routes are free/shared upstreams:

```bash
uv run python ~/.tau/provider-validation/validate_provider_catalog.py run \
  --builtins-only \
  --provider openrouter \
  --concurrency 2 \
  --provider-concurrency 1 \
  --timeout-seconds 35 \
  --max-retries 0
```

Avoid automatic retries during the first pass. Retries can hide deterministic payload/model
failures and consume extra credits. Retry only after classifying errors.

## Failure classification

Classify errors before fixing anything.

### Fix immediately if recurring provider-wide

If every model for a provider fails with the same payload/schema error, pause validation,
fix the adapter, then rerun that provider with `--retry-errors`.

Example from this pass:

- Google rejected every model because `systemInstruction` was nested under
  `generationConfig`.
- Fix: move `systemInstruction` to the top-level Google request payload.
- Rerun: Google models then passed except for real model/level issues.

### Fix in catalog/runtime

These are real Tau catalog/runtime issues:

| Error pattern | Typical fix |
|---|---|
| `model ... does not exist`, `No endpoints found`, `deprecated` | Remove the model from the packaged catalog. |
| `not a chat model`, requires audio/tools/search/file/MCP | Remove from coding-chat API catalog for now. |
| `Unsupported value: 'minimal'`, expected `low/medium/high` | Add per-model `unsupported_thinking_levels`. |
| Provider expects a different reasoning value | Add `thinking_level_map`. |
| Provider needs special routing/header/compat option | Add provider/model `compat` metadata and runtime support. |
| Provider-wide invalid JSON/payload | Fix the adapter and add a regression test. |

### Document, do not globally fix

These are account/runtime conditions, not necessarily catalog truth:

| Error pattern | Treatment |
|---|---|
| `401`, `403`, missing credential | Report as not validated or account-specific. |
| `402 Insufficient Balance` | Report as credentialed but not validated due balance. |
| Account entitlement errors | Report separately; do not remove globally unless provider docs confirm. |
| `429` rate limit | Retry later or report as rate-limited. Do not remove by default. |
| `500`, `502`, `503` transient/high demand | Retry once; if persistent, report as transient/upstream. |

Example from this pass:

- DeepSeek returned `402 Insufficient Balance`; we did not remove DeepSeek models.
- OpenAI Codex rejected some models for the logged-in ChatGPT account; we did not remove
  them globally because entitlement can vary.
- OpenRouter free/shared routes returned `429`; we left them in the catalog and reported
  them as upstream rate limits.

## Fix/rerun loop

Use this loop until all fixable errors are gone:

1. Run a plan or dry run.
2. Run a small batch for new providers.
3. If failures are provider-wide, fix the adapter first.
4. Run the full provider/catalog pass.
5. Summarize errors.
6. Apply catalog/runtime fixes.
7. Rerun only failures:

   ```bash
   uv run python ~/.tau/provider-validation/validate_provider_catalog.py run \
     --builtins-only \
     --provider PROVIDER_NAME \
     --retry-errors \
     --concurrency 2 \
     --provider-concurrency 1
   ```

8. Summarize again.
9. Repeat until remaining errors are only documented account/balance/rate-limit cases.

## Updating Tau after validation

Common code/catalog changes:

- Remove stale model IDs from `src/tau_coding/data/catalog.toml`.
- Set validated defaults for providers whose previous default was removed.
- Add `unsupported_thinking_levels` under `providers.model_metadata.<model>`.
- Add `thinking_level_map` for provider-specific value mapping.
- Add model `compat` fields for adapter-specific behavior.
- Add adapter regression tests for any provider-wide payload fixes.
- Update stale tests that hardcoded old defaults or provider lists.

For this validation pass, notable fixes were:

- Google Generative AI: `systemInstruction` now lives at request top level.
- OpenRouter: Tau passes `compat.openrouterProvider` as OpenRouter's `provider` routing
  option.
- Catalog: stale/deprecated/unroutable model IDs were pruned.
- Catalog: unsupported reasoning levels were hidden per model.
- Anthropic: adaptive-thinking metadata was tightened for current adaptive models.

## Final report format

End every validation pass with a concise report containing:

1. Catalog scope: effective or packaged/built-in.
2. Prompt and success condition.
3. Total expected attempts, recorded attempts, pending attempts.
4. Per-provider OK/error counts.
5. Remaining failures grouped by reason.
6. Providers not validated because credentials were missing.
7. Code/catalog fixes made.
8. Final project validation commands and results.

Store detailed local artifacts outside the repo:

```text
~/.tau/provider-validation/<validation-name>/results.jsonl
~/.tau/provider-validation/<validation-name>/validation-summary.md
```

Do not commit raw JSONL logs. They should not contain secrets, but they can include account
IDs, provider-specific metadata, rate-limit details, and paid-account availability clues.

## Result from the Pi API provider pass

After fixes, the packaged catalog had 1,193 expected validation attempts for credentialed
providers. All fixable model/reasoning mismatches were cleared.

Remaining failures were not global catalog/runtime bugs:

- DeepSeek returned `402 Insufficient Balance` for the available API key.
- OpenAI Codex returned account-entitlement errors for `gpt-5.3-codex` and `gpt-5.2` with
  the logged-in ChatGPT account, while other Codex models worked.
- Several OpenRouter free/shared upstream routes returned `429` rate limits.

Providers without credentials were not live-validated in this pass: Cerebras, Fireworks,
Mistral, Moonshot, Together, Vercel AI Gateway, xAI, Xiaomi, Z.ai, and regional variants.

Final local project validation:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy
```

```text
677 passed
All checks passed!
Success: no issues found in 64 source files
```
