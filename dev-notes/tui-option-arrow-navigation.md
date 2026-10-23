# Option-arrow word navigation

Tau's prompt inherits Textual's Ctrl-arrow word movement, but lacked Alt-arrow
bindings. Outside a multiplexer, macOS terminals may encode Option arrows as
Esc+b/Esc+f, which Textual normalizes to Ctrl arrows. Herdr can instead forward
CSI Alt-arrow sequences, which the current Textual parser decodes as Alt arrows.
The static ANSI lookup table alone is misleading: the parser handles modified
arrows before consulting that table.

`PromptInput` now binds Alt+Left/Right to its inherited word movement actions,
and Alt+Shift+Left/Right to word selection. No global parser patches, Herdr
configuration, macOS shortcut changes, or portable harness changes are needed.
This preserves Pi's frontend/agent separation: editing belongs to the adapter.

## Follow-up: plain Option arrows versus Shift+Option arrows

A user reported that word selection worked but plain Option arrows did not.
Herdr parses terminal Esc+b/Esc+f as Alt+b/Alt+f character keys. With Tau's
negotiated Kitty mode, these can become CSI `98;3u` / `102;3u`. Textual decodes
those as Alt+b/Alt+f, unlike the original Esc+b/Esc+f which it normalizes to
Ctrl arrows. The prompt now explicitly binds Alt+b/Alt+f to word movement too.
Regression tests cover both raw encodings through actual prompt event routing.
This reproduces a matching failure path; the user's live terminal still needs
a restart and manual verification.

Run `uv run pytest tests/test_tui_terminal_keys.py tests/test_tui_app.py`.
Tests decode actual terminal bytes and route the resulting events through a
Textual app into the prompt, checking movement and selection. They also cover
ordinary arrows, Escape, Ctrl arrows, and the existing Esc+b/Esc+f decoding.
For a manual check, restart Tau inside Herdr, type several words, and use
Option+Left/Right and Option+Shift+Left/Right.
