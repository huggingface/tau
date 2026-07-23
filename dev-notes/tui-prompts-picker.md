# Searchable prompt-template picker

Issue #452 adds `/prompts` as a built-in TUI command. It opens a Textual modal over the coding session, keeping picker behavior in `tau_coding.tui` rather than the reusable agent harness.

The modal sorts loaded templates by name, displays names and descriptions, and filters both fields case-insensitively. Up/Down move the selection, Enter inserts `/<template-name>` into the prompt editor without submitting, and Escape cancels. Dedicated messages explain empty and no-match states.

The command registry communicates the UI request through `CommandResult.prompts_picker_requested`, matching existing session/model picker boundaries. The resource loader reserves the case-insensitive template name `prompts`, ignores colliding files with a diagnostic, and therefore guarantees that a template cannot shadow the picker command.

Validate with:

```bash
uv run pytest tests/test_commands.py tests/test_tui_app.py -k prompts
uv run ruff check .
uv run mypy
```
