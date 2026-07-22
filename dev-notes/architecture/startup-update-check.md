# Startup update check

Tau now performs a small, best-effort update check in CLI startup paths that launch the product experience: the Textual TUI and text print mode.

## What was added

- `tau_coding.update_check` fetches PyPI metadata for the published package (`tau-ai`).
- Versions are compared with `packaging.version.Version` so PEP 440 releases sort correctly.
- The result is cached under `~/.tau/cache/update-check.json` and refreshed at most once per day.
- Failures are quiet no-ops: network errors, malformed JSON, missing fields, and invalid versions do not stop startup.
- `TAU_NO_UPDATE_CHECK=1` disables the check, and the check is skipped automatically when `CI` is set.
- `tau update` upgrades `tau-ai` with the package manager that owns the active Tau environment.

## Where it belongs

This lives in `tau_coding`, not `tau_agent`, because update notification is CLI application behavior. The reusable agent harness remains independent of PyPI, Rich/Textual UI concerns, and Tau's home-directory layout.

## Output policy

- TUI startup renders the update notice as the first transcript item in fixed bright-yellow, bold styling, before release notes, provider errors, theme diagnostics, or session history.
- Print mode writes the notice to stderr for normal text output.
- Structured print output (`--mode json`) suppresses the notice to avoid corrupting scripted output.
- Utility commands (`tau --version`, `tau update`, `tau sessions`, `tau export`, `tau providers`, `tau setup`) do not run the update check.

## Update command

`tau update` inspects the active environment before running anything:

- `uv-receipt.toml` means uv owns the tool. Tau fetches the latest stable PyPI version and runs `uv tool install tau-ai@<latest-version>`, explicitly replacing any version pin recorded when the tool was installed.
- `pipx_metadata.json` means pipx owns it, so Tau runs `pipx upgrade tau-ai`.
- The distribution's standard `INSTALLER` metadata identifies ordinary uv and pip installs. Tau runs either `uv pip install --python <current-python> --upgrade tau-ai` or `<current-python> -m pip install --upgrade tau-ai`, targeting the environment that is running Tau.

Tau does not fall through to another installer when the selected command fails. Direct-URL and editable installs are sent back to their original source; Conda/Pixi-managed and unrecognized environments get manual instructions rather than being modified with pip. Editable checkout installs can be refreshed with `uv tool install --editable --force .`.

## Testing

Run:

```bash
uv run pytest tests/test_updater.py tests/test_update_check.py tests/test_cli.py tests/test_tui_app.py
```
