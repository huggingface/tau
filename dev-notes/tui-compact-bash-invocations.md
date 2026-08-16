# Compact bash invocations in the TUI

Long shell commands used to wrap across many transcript lines before their output
was even shown. This was especially noisy for heredocs and interpreter commands
that embed source code directly in `python -c`, `node -e`, or similar arguments.

## What changed

The TUI now keeps short bash commands intact and compacts only commands that are
likely to clutter the transcript:

- multiline heredocs show the opening line and inline-script line count;
- other multiline commands show the first line and total line count;
- long inline-code commands show the prefix through `-c` or `-e` and a character
  count;
- other commands over 120 characters show their first 120 characters and total
  character count.

`Ctrl+O` now expands both sides of a tool interaction: the exact bash command and
the full result. Collapsing restores the compact command preview.

## Architecture

This is presentation-only behavior in `tau_coding.tui`. The canonical `ToolCall`
and persisted session message are unchanged. `ChatItem` already retains raw tool
arguments, so expanded rendering can recover the original command without adding
TUI concerns to `tau_agent` or changing command execution.

Formatting lives in `src/tau_coding/tui/state.py`. The state creates the compact
row once, then resolves the exact invocation lazily when tool results are
expanded. Existing custom tool `render_call` output still takes precedence.

## Tests

- `tests/test_tui_adapter.py` covers short commands, heredocs, generic multiline
  commands, long inline code, long ordinary commands, and exact expansion.
- `tests/test_tui_app.py` uses a Textual pilot to confirm `Ctrl+O` replaces a
  compact heredoc row with the exact command and full result.

Run:

```bash
uv run pytest tests/test_tui_adapter.py tests/test_tui_app.py
```

For manual validation, ask Tau to run a multiline heredoc. Confirm the collapsed
row occupies one line, press `Ctrl+O` to see the complete command, then press it
again to restore the preview.
