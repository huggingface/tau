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

Run `uv run pytest tests/test_tui_terminal_keys.py tests/test_tui_app.py`.
Tests decode actual terminal bytes and route the resulting events through a
Textual app into the prompt, checking movement and selection. They also cover
ordinary arrows, Escape, Ctrl arrows, and the existing Esc+b/Esc+f decoding.
For a manual check, restart Tau inside Herdr, type several words, and use
Option+Left/Right and Option+Shift+Left/Right.
