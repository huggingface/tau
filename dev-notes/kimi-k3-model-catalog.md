# Kimi K3 model catalog support

Tau's built-in `kimi-code` provider now exposes Kimi K3 through the model ID
`k3`. It uses the existing Kimi Code subscription endpoint and credential:

- endpoint: `https://api.kimi.com/coding/v1`
- environment variable: `KIMI_CODE_API_KEY`
- saved credential name: `kimi-code`

Kimi documents a context window of up to 1,048,576 tokens for eligible plans.
The catalog records that maximum so Tau's context budgeting can use it; the API
may reject requests beyond the user's plan entitlement.

K3 currently accepts only `max` reasoning effort. Tau maps its `xhigh` thinking
level to the API value `max` and excludes all other thinking levels for this
model. `kimi-for-coding` remains the default to avoid silently changing existing
users' selected rolling model.

## Verify

```bash
uv run pytest tests/test_provider_catalog.py tests/test_provider_config.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

After `/login kimi-code`, choose `kimi-code:k3` from `/model` or start Tau with
`--provider kimi-code --model k3`. Kimi recommends beginning a new session when
switching models because the old model's context cache cannot be reused.
