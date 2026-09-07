# Kimi K3 model catalog support

Tau exposes Kimi K3 through both the built-in `kimi-code` provider and Hugging
Face Inference Providers. Kimi Code uses the model ID `k3` and its existing
subscription endpoint and credential:

- endpoint: `https://api.kimi.com/coding/v1`
- environment variable: `KIMI_CODE_API_KEY`
- saved credential name: `kimi-code`

Kimi documents native visual understanding and a context window of up to
1,048,576 tokens for eligible plans. The catalog therefore marks K3 as accepting
text and image input and records that maximum so Tau's context budgeting can use
it; the API may reject requests beyond the user's plan entitlement.

K3 accepts `low`, `high`, and `max` reasoning effort. Tau maps these to `low`,
`high`, and `xhigh` respectively for both catalog entries. `kimi-for-coding`
remains the Kimi Code default, and `moonshotai/Kimi-K2.6` remains the Hugging
Face default, to avoid silently changing existing users' selections.

The Hugging Face catalog uses the official `moonshotai/Kimi-K3` repository ID.
Hugging Face currently advertises live routes through Together AI, Fireworks
AI, Featherless AI, Baseten, and DeepInfra. The catalog records the official
1,048,576-token context and the highest advertised route price of $3 input and
$15 output per million tokens; actual routing and price can vary.

## Verify

```bash
uv run pytest tests/test_provider_catalog.py tests/test_provider_config.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

After `/login kimi-code`, choose `kimi-code:k3` from `/model` or start Tau with
`--provider kimi-code --model k3`. For Hugging Face, use `/login huggingface`
and choose `huggingface:moonshotai/Kimi-K3`. Kimi recommends beginning a new
session when switching models because the old model's context cache cannot be
reused.
