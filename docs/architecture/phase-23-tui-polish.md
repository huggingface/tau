# Phase 23: Advanced TUI and Product Polish

Phase 23 improves the Textual frontend while keeping the reusable agent harness
independent of UI concerns.

The boundary remains:

```text
CodingSession emits AgentEvent values
        ↓
TuiEventAdapter updates TuiState
        ↓
Textual widgets render the transcript and controls
```

## Current polish slices

Live tool results now render successful output in the transcript, matching
restored session history. This keeps tool-call blocks useful during an active
run instead of hiding successful command output until the session is reloaded.

Transcript blocks now render fenced code and persisted edit patches with Rich
syntax renderables inside the same Pi-style stacked message blocks. The
transcript state still stores plain role/text items; this is renderer-only
polish in the Textual frontend.

Live `edit` tool results now include their unified patch in the tool block. This
provides an inline diff view for file edits while keeping the event adapter and
Textual widgets decoupled. Tool-result metadata is now preserved in
`ToolResultMessage`, so restored session history can render the same edit patch
blocks from persisted JSONL entries.

The TUI also has a command-palette entry point. Pressing `Ctrl+K` focuses the
prompt, inserts `/`, and shows all slash-command completions using the existing
completion engine. Selection still uses the same `Tab`, `Up`, and `Down`
bindings as ordinary slash-command autocomplete.

The same completion engine now suggests available values for `/model` and
`/provider` arguments. This gives the prompt a lightweight picker for model and
provider switching without adding a separate modal UI.

The prompt also suggests indexed session ids for `/resume <session-id>`, giving
the TUI a lightweight session picker path through the same completion UI.
Submitting the command reloads the selected session through `CodingSession` and
rebuilds the visible transcript in place.

The built-in Textual frontend now reads optional keybinding settings from
`~/.tau/tui.json`. This lets users remap the command palette, completion
navigation, cancellation, and quit keys while keeping the configuration in
`tau_coding.tui` instead of the reusable agent harness.

The frontend boundary is now documented in [Building a Custom TUI](../custom-tui.md).
That guide describes how another terminal UI can consume `CodingSession`,
`AgentEvent`, `TuiState`, and `TuiEventAdapter` without coupling to Textual
internals.

## Boundaries

These changes live in `tau_coding.tui`. The command registry still owns command
metadata, and `tau_agent` remains unaware of Textual, keybindings, slash
commands, and rendering.

## Still deferred

The larger Phase 23 roadmap still includes a richer modal session picker,
broader markdown transcript rendering, and deeper theme polish. Those should
remain separate atomic slices.

## Tests

Coverage lives in:

```text
tests/test_tui_adapter.py
tests/test_tui_app.py
tests/test_tui_config.py
```
