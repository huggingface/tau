# Hugging Face model catalog parity

## What changed

Tau's built-in Hugging Face Inference Providers catalog now contains 46 models. This adds 28 live-routable models from Pi's generated Hugging Face catalog across DeepSeek, Gemma, GLM, GPT OSS, Kimi, Llama, MiniMax, MiMo, Qwen, and Step families. Live testing excluded three Pi entries that no enabled Hugging Face inference provider could serve: `Qwen/Qwen3-Next-80B-A3B-Thinking`, `XiaomiMiMo/MiMo-V2-Flash`, and `moonshotai/Kimi-K2-Thinking`.

Each addition includes the provider model ID, display name, reasoning and input capabilities, context window, output limit, compatibility metadata, and token pricing. The existing `moonshotai/Kimi-K2.6` default remains unchanged.

## Why it exists

Tau and Pi use the same Hugging Face OpenAI-compatible router. Keeping their built-in model choices aligned means Tau users can select the broader set directly through `/model` instead of maintaining a personal catalog overlay.

This remains application configuration in `tau_coding`; no Hugging Face assumptions were added to the portable `tau_agent` harness.

## How to test

Run the catalog and provider configuration tests:

```bash
uv run pytest tests/test_provider_catalog.py tests/test_provider_config.py -q
```

With a saved Hugging Face credential, validate the packaged catalog against the live router:

```bash
uv run python ~/.tau/provider-validation/validate_provider_catalog.py run \
  --builtins-only \
  --provider huggingface \
  --concurrency 2 \
  --provider-concurrency 1 \
  --timeout-seconds 30 \
  --max-retries 0
```

The helper stops at `response_start` by default to verify model and payload acceptance while minimizing token usage. Keep detailed live-validation artifacts outside the repository.
