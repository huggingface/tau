# Rich + prompt_toolkit minimalist TUI

## What was added

Tau now includes a second built-in interactive frontend:

```bash
tau --tui rich
```

It uses `prompt_toolkit` for multiline editing, history, key handling, and
completion, and Rich `Live`/`Layout` for a full-screen transcript. The existing
Textual frontend remains the default and the complete product surface.

Both frontends are created around the same `CodingSession` and consume events
through the Textual-free `TuiEventAdapter` and `TuiState`:

```text
CodingSession -> CodingSessionEvent -> TuiEventAdapter -> TuiState
                                                    -> Textual
                                                    -> Rich Live/Layout
```

No frontend dependency was added to `tau_agent`.

## Product decisions

The goal is a useful coding loop, not a second implementation of every Textual
widget. The Rich frontend keeps:

- restored and streaming transcript output
- Markdown and compact tool-result rendering
- multiline input, history, and command/skill/template completion
- registered slash commands, direct `!`/`!!` shell commands, resume by id,
  compaction, clear/new/quit, and thinking-level arguments
- session/model/thinking/context status in one line
- queued-message display when queue events are received
- terminal-tab activity and clean provider/session startup behavior

The following PRD features are deliberately rejected for this frontend because
they would recreate a widget framework on top of `prompt_toolkit`:

- permanent sidebar and shortcut footer
- model/session/tree/theme/login pickers and modal login flows
- extension-provided Textual widgets, main views, and key interceptors
- mouse-scoped message selection and Textual Markdown streaming widgets
- simultaneous steering/follow-up input during a run
- configurable Textual themes and Textual keybinding names

When a command needs a picker, the frontend explains that the user should supply
an argument or restart with `--tui textual`. Extensions still participate in the
session, tools, hooks, commands, and renderers, but extension UI components are
Textual-only. This keeps the alternate frontend honest and small instead of
providing incomplete imitations of complex controls.

## Interaction model

- `Enter` inserts a newline.
- `Alt+Enter` submits.
- `Tab` accepts completion.
- `Ctrl+C` cancels prompt editing; during provider work it requests terminal
  interruption through the normal process signal path.
- `Ctrl+D` exits from an empty prompt.

The bottom toolbar is contextual input help, not a second session-information
surface. Session facts stay on one status line and detailed information remains
available through `/session`.

## Testing

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Manual smoke test:

```bash
uv run tau --tui rich
```

Submit a multiline prompt with Alt+Enter, run `!pwd`, open `/session`, and resume
a known id with `/resume <id>`. Commands that require a picker should show the
Textual fallback guidance rather than failing.
