---
title: "Multi-session TUI sidebars"
---

# Multi-session TUI sidebars

Tau's TUI now has two sidebars on wide terminals:

- a **left sessions sidebar** listing sessions for the current working directory
- a **right session-info sidebar** with the currently selected session's model,
  tools, skills, and context metadata

The left sidebar is an in-process workspace view. It combines durable session
records from `SessionManager.list_sessions(cwd)` with any sessions loaded in the
current TUI process. Rows show whether a session is currently visible, loaded in
memory, working, queued, idle, or in error.

## Why this exists

Before this change, `TauTuiApp` treated session switching as mutation of a
single `CodingSession`: `/resume` replaced the active session object and the TUI
blocked session switching while the agent was running. That made it impossible
to keep one session working while reading or prompting another session.

The new shape keeps Tau's existing package boundaries:

```text
tau_agent   unchanged portable agent brain
tau_coding  session loading plus Textual workspace orchestration
TUI         renders whichever loaded session is selected
```

## How it works

`TauTuiApp` owns a small `TuiSessionView` per loaded session. Each view contains:

- the `CodingSession`
- its own `TuiState`
- its own `TuiEventAdapter`
- prompt history and prompt draft
- the running Textual worker, if any

Submitting a prompt starts a worker for the selected view with `exclusive=False`,
so another loaded session can keep streaming in the background. Background
events update that session's own state and refresh the left sidebar. When the
user switches back, the transcript is redrawn from the preserved state.

`CodingSession.load_sibling(session_id)` loads another indexed session without
mutating the current session. The older `resume()` API is still available and now
uses the same loading helper internally.

## Current limits

- "Working" is an in-process status. A restarted TUI can list past sessions, but
  it cannot recover an already-running process from a previous TUI.
- Multiple sessions in the same working directory can still run tools
  concurrently. The sidebar makes this visible, but it does not serialize all
  cross-session file mutations.
- The left sidebar is keyboard-selectable through Textual's list view. More
  dedicated keybindings can be added later.

## Tests

Focused coverage lives in `tests/test_tui_app.py` and checks:

- both sidebars mount and hide responsively
- session records render in the left sidebar
- selecting another session switches transcript state
- a background fake session continues running while another session is visible
- prompt submission after switching targets the selected session
