# Project-trust runtime

Issue #535 adds a `tau_coding` input-loading guard around ambient project
resources. The accepted policy and Pi compatibility research remain in
`dev-notes/design/project-trust.md`.

## Runtime shape

- `project_trust.py` owns typed requests/resolutions, strict cwd
  canonicalization, metadata-only detection, precedence, per-cwd caching, and
  the locked version-1 atomic store.
- `TauResourcePaths.project_resources_enabled` creates one coherent resource
  plan. Existing skill, prompt, context, system-prompt, theme, and extension
  loaders consume that plan rather than implementing policy.
- `CodingSession.load` imports only user/explicit extensions first, resolves
  trust, then loads protected Markdown/JSON and opted-in project extensions.
- CLI entry points supply the user-global default and invocation override.
  Structured/headless modes use no prompt. The TUI adapts the frontend-neutral
  request to an accessible Textual modal.
- Reload re-detects. Resume stages and resolves the destination cwd before
  adoption. A cancelled reload/replacement keeps the current snapshot.

`tau_agent` has no trust, path, Typer, or Textual dependency. Textual remains in
`tau_coding.tui`.

## Persistence and failure behavior

`~/.tau/trust.json` has `version` and sorted `decisions`. Reads reject unknown
fields/versions, duplicate or relative/non-normalized paths, and unknown
values. Updates lock the store, write/fsync a mode-0600 same-directory temporary
file, replace, and fsync the directory. Errors diagnose and fail closed; run-only
explicit approval does not depend on storage.

## Migration

There is no legacy store. Existing projects are not auto-trusted. Interactive
users decide when protected candidates exist; unresolved headless `ask` skips
project inputs. User/global and explicit CLI resources continue to load.
`--project-extensions` remains an additional executable-code opt-in.

## Verification

Use temporary homes/projects and fake extensions/providers. Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
cd website && hugo --minify
cd website && npx --yes pagefind@latest --site public
```

Project trust is not a filesystem/process/network/tool/model sandbox.
