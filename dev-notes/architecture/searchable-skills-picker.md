# Searchable skills picker

Issue #451 adds `/skills` as a TUI-only discovery workflow. The normal command registry returns a UI intent, keeping Textual out of the reusable command/session layers. The Textual adapter presents loaded skills sorted by name and filters names and descriptions case-insensitively.

Selecting a row places `/skill:<name>` in the prompt without submitting. F1 previews the complete skill header description in a modal while leaving Space available for multi-word searches. Ctrl+Enter appends the full `SKILL.md` to display-only TUI state, so it appears in the transcript without entering persisted session history or model context. Cancelling restores the submitted `/skills` text. Empty collections and searches display explicit states; existing resource diagnostics remain on their existing surfaces.

Validate with:

```bash
uv run pytest tests/test_commands.py tests/test_tui_app.py
```
