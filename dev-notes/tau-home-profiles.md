# Configurable Tau home profiles

## What changed

Tau now reads `TAU_HOME` whenever it constructs its default user-data paths. An
unset or empty value retains `~/.tau`; a non-empty value is expanded with
`Path.expanduser()` and must then be absolute. Explicit `TauPaths(home=...)`
values remain authoritative for tests and embedding applications.

Both user path abstractions use the same default:

- `TauPaths` owns sessions, credentials, provider configuration, settings, trust,
  extensions, diagnostics, local-backend state, and caches.
- `TauResourcePaths` owns user skills, prompts, themes, `SYSTEM.md`, and
  `APPEND_SYSTEM.md` discovery.

Reading the environment at construction time, rather than module import time,
lets independent Tau processes select different profiles. Embedding applications
that need concurrent profiles should inject explicit `TauPaths` instead of
mutating the process-global environment. Symlinks are not resolved, so a
dotfile-managed profile path keeps its identity.

## Why it exists

Tau stores one saved OAuth or API-key credential per provider name. Supporting
multiple named credentials inside one home would also require account-aware
provider selection, token refresh, session metadata, caches, and login/logout UI.
A configurable application home provides a smaller and clearer profile boundary:

```bash
TAU_HOME="$HOME/.tau-work" tau
TAU_HOME="$HOME/.tau-personal" tau
```

Each profile can authenticate `anthropic`, `openai-codex`, or another provider
independently while also keeping its sessions and preferences separate. This
matches the directory-selection pattern used by other coding agents without
introducing account concepts into `tau_agent` or `tau_ai`.

## Boundary

`TAU_HOME` isolates Tau-owned user configuration and state. It deliberately does
not relocate:

- shared `~/.agents` skills, prompts, and instructions;
- project-local `.tau` and `.agents` resources;
- explicit extension paths;
- credentials supplied directly through process environment variables; or
- external credential files read by third-party integrations.

It is therefore an application profile, not a filesystem or credential sandbox.
Credential files remain mode `0600`; users should create profile directories with
private permissions when they contain account credentials.

## Validation

The tests cover default and empty values, tilde expansion, rejection of relative
paths, construction-time environment reads, explicit-path precedence, resource
path selection, shared `.agents` behavior, OAuth credential isolation, and a CLI
setup write that leaves the default `~/.tau` untouched.

Run:

```bash
uv run pytest tests/test_paths.py tests/test_resources.py tests/test_credentials.py tests/test_cli.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
cd website && hugo --minify
```
