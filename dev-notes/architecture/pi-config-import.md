# Pi configuration import

Tau can now import a best-effort subset of an existing Pi configuration into Tau's provider settings.

## What was added

- `tau config import-pi <path>` imports provider/model settings from a Pi JSON or TOML config file.
- `--dry-run` prints the resulting Tau `providers.json` payload without writing.
- `--yes` allows updating an existing `~/.tau/providers.json`; without it, existing provider settings are protected.
- `--default-pi-config` searches common Pi locations such as `~/.pi/config.json` and `~/.config/pi/config.toml`.

## Mapping decisions

The importer lives in `tau_coding` because it deals with CLI behavior and user-level config files. The portable `tau_agent` package remains independent of Pi/Tau filesystem conventions.

Supported import fields are intentionally conservative:

- provider name (`provider`, `default_provider`, provider table/list names)
- model (`model`, `default_model`, `defaultModel`)
- base URL (`base_url`, `baseUrl`, `baseURL`, `api_base`)
- API key env var (`api_key_env`, `apiKeyEnv`, `api_key_env_var`)

Raw API keys are not copied into Tau. When the Pi config contains a raw key, Tau warns and records only the expected environment variable reference. Unknown fields also generate warnings so users can decide whether to migrate them manually.

## How it maps to Pi

Pi and Tau share provider/model configuration concepts, but Tau splits durable provider metadata (`catalog.toml`) from runtime preferences (`providers.json`). The importer writes through Tau's existing provider settings helpers so custom provider definitions are persisted using the same path as `tau setup` and provider/model picker updates.

## How to test

```bash
uv run pytest tests/test_pi_config_import.py
uv run pytest
uv run ruff check .
uv run mypy
```

Manual dry run example:

```bash
tau --dry-run config import-pi ~/.pi/config.json
```
