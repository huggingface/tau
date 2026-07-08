# Startup release notes

Tau now stores structured release notes in `release-notes/releases.json`.

The same file is used by:

- the TUI startup notice shown after an upgrade
- the website release-notes page at `/releases/`
- package metadata tests that ensure the current package version has release notes

Tau records the last installed version seen at startup in `~/.tau/cache/release-notes-state.json`.

On the first run it only writes the current version. On later runs, if the installed version is newer than the recorded version, the TUI prepends a status-style transcript item with release highlights for versions between the old and new versions.

This keeps the reusable agent harness unchanged: release-note detection lives in `tau_coding.update_check`, and the TUI consumes the resulting startup notice as display state.

Test with:

```bash
uv run pytest tests/test_update_check.py tests/test_cli.py tests/test_tui_app.py
```
