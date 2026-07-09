# TUI /session modal selection copy

## What changed

The `/session` command output modal now opts into automatic copy-on-select behavior. Users can select text in the session details modal and Tau copies the selected text to the clipboard, even when the global transcript `auto_copy_selection` setting is disabled.

## Why it exists

The session modal contains IDs, paths, provider/model details, resource diagnostics, and context accounting that are often useful when documenting production work or starting a follow-up PR. Copy-on-select makes that information easy to reuse without adding another command or exporting the whole session.

## Architecture notes

The change stays in the Textual TUI layer:

- `CommandOutputScreen` exposes a modal-local `auto_copy_selection` flag.
- `TauTuiApp._show_command_message()` enables that flag only for `/session` output.
- `TauTuiApp.on_text_selected()` now checks either the global TUI setting or the active screen's modal-local flag before copying.

No clipboard or Textual dependencies were added to `tau_agent`.

## How to test

Automated checks:

```bash
uv run pytest tests/test_tui_app.py -k "session_modal_auto_copies_selected_text or non_session_modal_uses_global_auto_copy_setting or command_modal"
uv run ruff check src/tau_coding/tui/app.py tests/test_tui_app.py
```

Manual check:

1. Run `uv run tau`.
2. Open `/session`.
3. Select text inside the modal.
4. Paste into another application or terminal prompt and confirm the selected text was copied.
