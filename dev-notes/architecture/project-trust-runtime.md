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
- Reload and destination replacement stage fresh cwd-bound extension runtimes,
  resources, tools, commands, prompts, and trust-cache entries before adoption.
  Resource plans are unconditionally rebound to the canonical destination;
  source-project registrations never cross cwd boundaries. Cancellation or any
  preparation failure keeps the current snapshot and prior coordinator cache.

`tau_agent` has no trust, path, Typer, or Textual dependency. Textual remains in
`tau_coding.tui`.

## Persistence and failure behavior

`~/.tau/trust.json` has `version` and sorted `decisions`. Reads reject unknown
fields/versions, duplicate or relative/non-normalized paths, and unknown
values. Updates lock the store and first durably install a mode-0600 undo journal,
then write/fsync a same-directory temporary file, replace `trust.json`, and fsync
the directory. Readers reject any store with a pending journal. The journal is
removed only after the destination commit point; failures attempt restoration,
and a failed restoration leaves the journal in place so even combined commit and
recovery failures cannot expose newly granting bytes. Journal-cleanup fsync
failure is safe in either durable outcome: the committed store remains visible,
or the journal reappears and reads fail closed. Errors diagnose and fail closed;
run-only explicit approval does not depend on storage.

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
