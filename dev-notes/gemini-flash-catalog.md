# Gemini Flash catalog refresh

Tau's `google` catalog entry stopped at `gemini-3.5-flash` (May 2026). Google has
since shipped two more Flash generations and a 3.5 Lite, and repointed the
`-latest` aliases at them:

| Model | Released | Input | Output | Reasoning effort |
| --- | --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | 2026-07-21 | $0.30 | $2.50 | minimal, low, medium, high |
| `gemini-3.6-flash` | 2026-07-21 | $0.75 | $3.75 | minimal, low, medium, high |
| `gemini-3.7-flash` | 2026-08-13 | $0.75 | $3.75 | low, medium, high |

All three keep the 1,048,576-token context and 65,536-token output ceiling of the
rest of the Gemini 3 line. Like every Gemini 3 model already in the catalog, they
cannot disable thinking, so each declares `unsupported_thinking_levels = ["off"]`.
Gemini 3.7 Flash additionally drops `minimal`, which the 3.5 and 3.6 generations
accept.

## Aliases

`gemini-flash-latest` is the provider default and now resolves to Gemini 3.7
Flash; `gemini-flash-lite-latest` resolves to Gemini 3.5 Flash Lite. Both alias
entries still carried Gemini 2.5-era pricing ($0.30/$2.50 and $0.10/$0.40) and
allowed `off`, so cost reporting and the thinking picker were both wrong for
anyone on the default model. Their metadata now matches the models they point at.

Aliases move when Google moves them, so this metadata is a snapshot and needs
rechecking whenever a new Flash generation ships.

## Verify

```bash
uv run pytest tests/test_provider_catalog.py tests/test_provider_config.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

After `/login google`, choose `google:gemini-3.7-flash` from `/model` or start
Tau with `--provider google --model gemini-3.7-flash`.
