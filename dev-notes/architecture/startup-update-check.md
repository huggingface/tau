# Startup update check

Tau now performs a small, best-effort update check in CLI startup paths that launch the product experience: the Textual TUI and text print mode.

## What was added

- `tau_coding.update_check` fetches PyPI metadata for the published package (`tau-ai`).
- Versions are compared with `packaging.version.Version` so PEP 440 releases sort correctly.
- The result is cached under `~/.tau/cache/update-check.json` and refreshed at most once per day.
- Failures are quiet no-ops: network errors, malformed JSON, missing fields, and invalid versions do not stop startup.
- `TAU_NO_UPDATE_CHECK=1` disables the check, and the check is skipped automatically when `CI` is set.
- `tau update` upgrades `tau-ai`, trying uv, pipx, and then the current Python interpreter's pip.

## Where it belongs

This lives in `tau_coding`, not `tau_agent`, because update notification is CLI application behavior. The reusable agent harness remains independent of PyPI, Rich/Textual UI concerns, and Tau's home-directory layout.

## Output policy

- TUI startup renders the update notice as the first transcript item in fixed bright-yellow, bold styling, before release notes, provider errors, theme diagnostics, or session history.
- Print mode writes the notice to stderr for normal text output.
- Structured print output (`--output json`) suppresses the notice to avoid corrupting scripted output.
- Utility commands (`tau --version`, `tau update`, `tau sessions`, `tau export`, `tau providers`, `tau setup`) do not run the update check.

## Update command

`tau update` tries installers in this order:

1. `uv tool upgrade tau-ai`
2. `pipx upgrade tau-ai`
3. `<current-python> -m pip install --upgrade tau-ai`

Unavailable or failed installers fall through without cluttering successful output. If every installer fails, Tau exits nonzero and prints each diagnostic. Editable checkout installs should still be refreshed explicitly with `uv tool install --editable --force .`.

## Testing

Run:

```bash
uv run pytest tests/test_updater.py tests/test_update_check.py tests/test_cli.py tests/test_tui_app.py
```
