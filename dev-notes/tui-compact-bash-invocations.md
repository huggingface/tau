# Compact bash invocations in the TUI

Long shell commands used to wrap across many transcript lines before their output
was even shown. This was especially noisy for heredocs and interpreter commands
that embed source code directly in `python -c`, `node -e`, or similar arguments.

## What changed

The bash tool requires the model to provide a brief present-participle
`description` in the same tool call. The TUI normalizes that summary and limits
it to 56 characters. Short commands pair it with the complete command; long or
multiline commands pair it with a 32-character prefix derived from the real
command. Every summary therefore retains deterministic command text. This adds
no second provider request. Calls that still omit the field because of malformed
provider output, custom integrations, or older session history keep a
deterministic fallback:

- multiline heredocs show the opening line and inline-script line count;
- other multiline commands show the first line and total line count;
- long inline-code commands show the prefix through `-c` or `-e` and a character
  count;
- other commands over 120 characters show their first 120 characters and total
  character count.

`Ctrl+O` now expands both sides of a tool interaction: the exact bash command and
the full result. Collapsing restores the compact command preview.

## Architecture

The provider-visible bash schema and tool prompt guideline live in
`src/tau_coding/tools.py`. The schema requires `description` to make compliant
models return the display metadata consistently. The executor remains tolerant
of omissions and ignores the value, so malformed provider output, custom
integrations, and older history cannot prevent command execution. The value is
display metadata carried inside the existing `ToolCall.arguments` mapping.

Formatting lives in `src/tau_coding/tui/state.py`. The state combines supplied
descriptions with complete short commands or deterministic command hints for long
calls, and creates an argument-only compact row when no description exists.
It resolves the exact invocation lazily when tool results are expanded. Existing
custom tool `render_call` output still takes precedence. The print-mode transcript renderer
explicitly requests the unabridged invocation because it has no interactive
expansion control. Session JSONL serialization remains independent of these
display formatters and retains the complete `command`. No TUI concerns enter
`tau_agent`, and command execution is unchanged.

## Tests

- `tests/test_coding_tools.py` and `tests/test_system_prompt.py` cover the required
  schema field, omission-tolerant execution, and model instruction.
- `tests/test_tui_adapter.py` covers semantic descriptions, complete short
  commands, command hints, short-command safety, heredocs, generic multiline commands,
  whitespace-only input, interpreter flags, long inline code, long ordinary
  commands, and exact expansion.
- `tests/test_tui_app.py` uses a Textual pilot to confirm `Ctrl+O` replaces a
  compact heredoc row with the exact command and full result.
- `tests/test_rendering.py` confirms print-mode transcripts always show the exact
  command instead of the display description.

Run:

```bash
uv run pytest tests/test_tui_adapter.py tests/test_tui_app.py
```

For manual validation, ask Tau to run a multiline heredoc. Confirm the collapsed
row occupies one line, press `Ctrl+O` to see the complete command, then press it
again to restore the preview.
