# External editor for the prompt

## What changed

Pressing **Ctrl+G** (configurable via `keybindings.external_editor` in
`~/.tau/tui.json`) while focused on the prompt now opens its text in your
`$VISUAL`/`$EDITOR` (falling back to `vi`, or `notepad` on Windows, when
neither is set). Saving and exiting the editor replaces the prompt with the
edited text; a nonzero exit status, a failed launch, or an unsupported
environment leaves the prompt untouched and shows a notification instead.
The hotkey is a no-op (with a notification) while Tau is already working, so
it never suspends the terminal mid-stream.

## Why

Multi-line or careful prompt edits are awkward in a terminal `TextArea`.
Pi's TypeScript and Rust implementations all offer this as a core hotkey;
Tau lacked an equivalent.

## Architecture

Editor resolution and the temp-file round trip live in
`tau_coding.tui.external_editor`, a small module with no Textual dependency
(`resolve_editor_command` handles the `$VISUAL`/`$EDITOR`/fallback chain and
multi-word command splitting; `edit_text_in_external_editor` handles the
temp file and subprocess). `TauTuiApp.action_open_external_editor` suspends
the Textual app with `App.suspend()` — the terminal-mode toggling Textual
already provides, instead of reimplementing raw-mode/alternate-screen
handling — runs that module off the event loop thread via
`asyncio.to_thread`, and writes the result back into `PromptInput`. No
`tau_agent` code is involved, and the feature reuses the existing
`TuiKeybindings` validation/remapping machinery rather than adding a
separate config surface.

## Validation

```bash
uv run pytest tests/test_tui_app.py -k external_editor
uv run pytest tests/test_tui_config.py -k external_editor
uv run pytest tests/test_external_editor.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Manual check: focus the prompt, set `EDITOR=nano` (or your editor of
choice), press Ctrl+G, edit the text, save and quit; confirm the prompt
shows the edited text. Repeat exiting the editor without saving / with a
nonzero status and confirm the prompt is unchanged and a notification
appears.
