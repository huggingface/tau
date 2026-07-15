# Kimi K2.7 model catalog support

Tau's built-in catalog now exposes Kimi's current coding models through their two official API surfaces:

- `moonshotai:kimi-k2.7-code` uses the pay-as-you-go Kimi API at `https://api.moonshot.ai/v1`.
- `kimi-code:kimi-for-coding` uses the Kimi Code subscription endpoint at `https://api.kimi.com/coding/v1`. Kimi documents `kimi-for-coding` as the rolling model ID for the latest coding model.

They are separate providers because their API keys, base URLs, and billing plans are separate. Both authenticate HTTP requests with Bearer API keys rather than OAuth. Moonshot AI reads a pay-as-you-go key from `MOONSHOT_API_KEY`; Kimi Code reads a subscription key from `KIMI_CODE_API_KEY`. The distinct names let users configure both services at once and mirror Tau's existing separation between OpenAI API access and an OpenAI Codex subscription.

Kimi's documentation says K2.7 Code has a 262,144-token context window, accepts multimodal input, and always uses thinking mode. The catalog therefore marks it as reasoning-capable and does not offer an `off` thinking mode.

## Verify

```bash
uv run pytest tests/test_provider_catalog.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

To use either model, save the appropriate credential with `/login`, then select it with `/model` or launch Tau with `--provider` and `--model`.
