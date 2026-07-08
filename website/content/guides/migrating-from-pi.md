---
title: Migrating from Pi
description: Import an existing Pi provider/model configuration into Tau.
---

Tau can import a best-effort subset of an existing Pi configuration so you do not
have to recreate provider/model settings from scratch.

## Import a config file

```bash
tau config import-pi ~/.pi/config.json
```

Tau accepts JSON and TOML files. You can also pass a directory; Tau will look for
`config.json`, `config.toml`, `settings.json`, or `settings.toml` inside it.

If you want Tau to search common Pi locations, use:

```bash
tau --default-pi-config config import-pi
```

## Preview before writing

Use `--dry-run` to print the Tau `providers.json` payload without changing files:

```bash
tau --dry-run config import-pi ~/.pi/config.json
```

Tau protects an existing `~/.tau/providers.json`. To update it from a Pi import,
pass `--yes`:

```bash
tau --yes config import-pi ~/.pi/config.json
```

## What is imported

The importer maps provider/model settings when the fields are present:

- provider name (`provider`, `default_provider`, provider table/list names)
- model (`model`, `default_model`, `defaultModel`)
- base URL (`base_url`, `baseUrl`, `baseURL`, `api_base`)
- API key environment variable (`api_key_env`, `apiKeyEnv`, `api_key_env_var`)

Raw API keys are not copied. If Tau sees a raw key in the Pi config, it warns and
records only the expected environment variable name. Unmapped fields are reported
as warnings so you can decide whether to migrate them manually.
