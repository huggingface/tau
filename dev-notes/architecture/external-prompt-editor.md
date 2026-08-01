---
title: "External prompt editor"
---

Tau's Textual prompt composer supports a Pi-style external-editor action. The
feature remains in `tau_coding.tui`: the reusable `tau_agent` harness does not
know about terminal suspension, environment variables, temporary files, or key
bindings.

## Behavior

`Alt+E` is the default `external_editor` keybinding in `~/.tau/tui.json`. The
action expands any compact large-paste placeholders, writes the current prompt
to a temporary `*.tau.md` file, suspends Textual's terminal application mode,
and runs the first configured editor from:

1. `$VISUAL`
2. `$EDITOR`
3. Notepad on Windows or `nano` elsewhere

The temporary path is appended to editor arguments, so commands such as
`EDITOR="code --wait"` work. A zero exit status reloads the saved file into the
prompt and places the cursor at the end. Launch errors and non-zero exits leave
the original prompt untouched and surface a warning. Temporary files are always
removed.

This maps to Pi's built-in `app.editor.external` behavior while using Tau's
existing named-keybinding configuration rather than Pi's action-id map.

## Verification

```bash
uv run pytest -q tests/test_tui_external_editor.py tests/test_tui_config.py \
  tests/test_tui_app.py -k external_editor
uv run ruff check src/tau_coding/tui tests/test_tui_external_editor.py \
  tests/test_tui_config.py tests/test_tui_app.py
uv run mypy src
```
