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

- `uv-receipt.toml` means uv owns the tool. Tau fetches the latest stable PyPI
  version and runs `uv tool install tau-ai@<latest-version>`, explicitly replacing
  any version pin recorded when the tool was installed. On Windows, Tau hands
  this command to a detached PowerShell process. The helper waits for the
  original Tau PID to exit before invoking uv, preventing Windows from partially
  replacing the still-running tool environment. Tau reports that the update was
  scheduled—not completed—and gives the path to a log containing the eventual
  command output and exit code. The helper treats process inspection or waiting
  errors as fatal: uv never starts unless the original Tau process is confirmed
  absent or its observed process object exits. The update executable and a
  Microsoft-runtime-quoted argument line are staged as base64-encoded JSON. The
  helper launches them with non-shell `ProcessStartInfo`, preserving spaces,
  metacharacters, embedded quotes, empty arguments, and trailing backslashes on
  Windows PowerShell 5.1 and PowerShell 7 without interpolated shell execution.
- `pipx_metadata.json` means pipx owns it, so Tau runs `pipx upgrade tau-ai`.
- The distribution's standard `INSTALLER` metadata identifies ordinary uv and pip installs. Tau runs either `uv pip install --python <current-python> --upgrade tau-ai` or `<current-python> -m pip install --upgrade tau-ai`, targeting the environment that is running Tau.

Tau does not fall through to another installer when the selected command fails. Direct-URL and editable installs are sent back to their original source; Conda/Pixi-managed and unrecognized environments get manual instructions rather than being modified with pip. Editable checkout installs can be refreshed with `uv tool install --editable --force .`.

## Testing

Run:

```bash
uv run pytest tests/test_updater.py tests/test_update_check.py tests/test_cli.py tests/test_tui_app.py
```

`tests/test_updater.py` also contains Windows-and-PowerShell-only integration
coverage. On Windows it launches the generated helper against a live fake parent
and fake updater, checking blocking, exact executable and argument delivery,
exit-code logging, and fail-closed wait errors without installing uv or replacing
Tau. Every installed supported engine (`powershell.exe` and `pwsh.exe`) is tested;
individual unavailable engines are omitted, and the runtime tests skip only when
Windows has neither. Cross-platform tests cover the Windows quoting algorithm,
encoded payload, script generation, detached launch options, staging failures,
and cleanup. Current Ubuntu CI cannot execute the Windows runtime cases.
