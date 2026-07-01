# Startup update check

Tau now performs a small, best-effort update check in CLI startup paths that launch the product experience: the Textual TUI and text print mode.

## What was added

- `tau_coding.update_check` fetches PyPI metadata for the published package (`tau-ai`).
- Versions are compared with `packaging.version.Version` so PEP 440 releases sort correctly.
- The result is cached under `~/.tau/cache/update-check.json` and refreshed at most once per day.
- Failures are quiet no-ops: network errors, malformed JSON, missing fields, and invalid versions do not stop startup.
- `TAU_NO_UPDATE_CHECK=1` disables the check, and the check is skipped automatically when `CI` is set.

## Where it belongs

This lives in `tau_coding`, not `tau_agent`, because update notification is CLI application behavior. The reusable agent harness remains independent of PyPI, Rich/Textual UI concerns, and Tau's home-directory layout.

## Output policy

- TUI startup passes the notice through the existing startup notification path.
- Print mode writes the notice to stderr for normal text output.
- Structured print output (`--output json`) suppresses the notice to avoid corrupting scripted output.
- Utility commands (`tau --version`, `tau sessions`, `tau export`, `tau providers`, `tau setup`) do not run the update check.

## Testing

Run:

```bash
uv run pytest tests/test_update_check.py tests/test_cli.py tests/test_tui_app.py
```
