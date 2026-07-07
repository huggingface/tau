# Provider catalog live validation

This note records the live validation pass for the Pi-derived API provider catalog.

## What was validated

A temporary local script under `~/.tau/provider-validation/` enumerated the effective
provider/model/thinking-level matrix, made one minimal request for each entry, and wrote
resumable JSONL results. The script was later switched to `--builtins-only` mode so the
packaged PR catalog could be verified independently from `~/.tau/catalog.toml` overlays.

The validation prompt was intentionally tiny:

```text
Reply with exactly: OK
```

A request counted as accepted once the provider returned `response_start`; this keeps the
validation cheap while still exercising model IDs and reasoning/thinking payloads.

## Fixes from validation

- Google Generative AI rejected Tau's first payload because `systemInstruction` was inside
  `generationConfig`. It now lives at the top level of the request payload.
- OpenRouter needed catalog-driven provider routing for one model. Tau now passes
  `compat.openrouterProvider` through as OpenRouter's `provider` request option.
- The built-in catalog was pruned for stale/deprecated/unroutable model IDs discovered
  during live validation.
- Per-model `unsupported_thinking_levels` were added where providers accepted a model but
  rejected one or more displayed reasoning levels.
- Anthropic adaptive-thinking metadata was tightened for current adaptive models.

## Final packaged-catalog result

After fixes, the packaged catalog had 1,193 expected validation attempts for credentialed
providers. All fixable model/reasoning mismatches were cleared.

Remaining failures were not global catalog/runtime bugs:

- DeepSeek returned `402 Insufficient Balance` for the available API key.
- OpenAI Codex returned account-entitlement errors for `gpt-5.3-codex` and `gpt-5.2` with
  the logged-in ChatGPT account, while other Codex models worked.
- Several OpenRouter free/shared upstream routes returned `429` rate limits.

Providers without credentials were not live-validated in this pass: Cerebras, Fireworks,
Mistral, Moonshot, Together, Vercel AI Gateway, xAI, Xiaomi, Z.ai, and regional variants.

## Verification

```bash
uv run pytest -q
uv run ruff check .
uv run mypy
```

Final local result:

```text
677 passed
All checks passed!
Success: no issues found in 64 source files
```
