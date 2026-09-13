# Sidebar file editor

## What changed

Prompt-template, project-context, and skill rows in the TUI sidebar are now
interactive file entries. Clicking one replaces the transcript with a main-area
text editor. `Ctrl+S` saves without closing the editor, while `Escape` restores
the transcript. Save success and filesystem errors remain visible in both the
editor status line and a TUI notification.

Skills deliberately expose only their main `SKILL.md`. Supporting files beside
it remain outside this first editor surface.

## Why

The sidebar already identifies the files that shape a session, but editing them
required switching to another terminal or opening the prompt-template picker.
A direct editor makes those visible resources actionable while preserving the
existing session and agent loop.

## Architecture

The feature stays in `tau_coding.tui`. `SidebarFileItem` owns mouse/keyboard
activation and emits a typed Textual message. `TauTuiApp` reads the selected
path and mounts `SidebarFileEditor` through the existing main-view seam, leaving
`tau_agent` and session semantics unchanged. Saving writes only the selected
file; `/reload` remains the explicit operation that reapplies changed resources
to the active session.

## Validation

```bash
uv run pytest tests/test_tui_app.py -k sidebar
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For a manual check, expand the sidebar skills or prompts section, hover a file
row, click it, edit the text, save with `Ctrl+S`, then close with `Escape`.
Repeat with a context file such as `AGENTS.md` and run `/reload` to apply it.
