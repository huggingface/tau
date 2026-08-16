# Compact bash invocations in the TUI

Long shell commands used to wrap across many transcript lines before their output
was even shown. This was especially noisy for heredocs and interpreter commands
that embed source code directly in `python -c`, `node -e`, or similar arguments.

## What changed

The bash tool now asks the model for an optional, brief present-participle
`description` in the same tool call. The TUI uses that semantic summary when it
is present, normalizing whitespace and limiting it to 80 characters. This adds
no second provider request. Calls from models that omit the optional field keep
a deterministic fallback:

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
`src/tau_coding/tools.py`. `description` remains optional so a model omission
cannot prevent command execution. The executor ignores it; the value is display
metadata carried inside the existing `ToolCall.arguments` mapping.

Formatting lives in `src/tau_coding/tui/state.py`. The state prefers a supplied
description, otherwise creates the deterministic compact row, then resolves the
exact invocation lazily when tool results are expanded. Existing custom tool
`render_call` output still takes precedence. No TUI concerns enter `tau_agent`,
and command execution is unchanged.

## Tests

- `tests/test_coding_tools.py` and `tests/test_system_prompt.py` cover the optional
  schema field and model instruction.
- `tests/test_tui_adapter.py` covers semantic descriptions, short commands,
  heredocs, generic multiline commands, long inline code, long ordinary commands,
  and exact expansion.
- `tests/test_tui_app.py` uses a Textual pilot to confirm `Ctrl+O` replaces a
  compact heredoc row with the exact command and full result.

Run:

```bash
uv run pytest tests/test_tui_adapter.py tests/test_tui_app.py
```

For manual validation, ask Tau to run a multiline heredoc. Confirm the collapsed
row occupies one line, press `Ctrl+O` to see the complete command, then press it
again to restore the preview.
