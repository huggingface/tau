---
title: "Phase 11: Print and Event Rendering Modes"
---

Phase 11 adds a small rendering boundary for Tau's non-interactive CLI modes.

The implementation lives in:

```text
src/tau_coding/rendering/
```

## What was added

Tau now has event renderers that consume `tau_agent` events outside the portable harness layer:

- `FinalTextRenderer` prints only the final assistant answer.
- `JsonEventRenderer` writes every agent event as JSON Lines.
- `TranscriptRenderer` preserves Tau's previous live transcript behavior.
- `PrintOutputMode` selects `text`, `json`, or `transcript` output.

The CLI exposes this with:

```bash
tau --output text "summarize this project"
tau --output json "summarize this project"
tau --output transcript "summarize this project"
```

`text` is the default mode.

## Why this exists

Pi keeps terminal output modes outside the reusable agent core:

```text
agent/session emits events
print mode consumes events for final text or JSON output
interactive mode uses a separate TUI layer
```

Tau now follows the same architectural boundary:

```text
tau_agent     portable event-producing harness
tau_coding    CLI mode selection and event rendering
future TUI    another consumer of the same event stream
```

This keeps `tau_agent` free of Typer, Rich, Textual, terminal behavior, and UI policy.

## Output modes

### Text mode

Text mode is Pi-style print mode. It consumes the full event stream and prints only the final assistant message after the run finishes.

It ignores tool events for display purposes and returns failure when a non-recoverable `ErrorEvent` appears.

### JSON mode

JSON mode writes one serialized event per line:

```json
{"type":"message_start","message_role":"assistant"}
{"type":"message_delta","delta":"Hello"}
```

This gives scripts and future integrations a stable event stream without depending on human-oriented terminal formatting.

### Transcript mode

Transcript mode preserves Tau's earlier print-mode behavior:

- assistant deltas stream to stdout
- tool starts, progress updates, and tool results render to stderr
- successful and failed tool result content renders to stderr
- errors render to stderr

The transcript renderer uses Rich for human-oriented stderr output while keeping
the default `text` mode script-friendly.

This is useful while Tau does not yet have a full interactive TUI.

## CLI integration

`run_print_mode()` now accepts:

```python
output: PrintOutputMode = PrintOutputMode.text
```

It creates a renderer with:

```python
create_event_renderer(output)
```

and sends every event from `CodingSession.prompt()` to the renderer. The session
wrapper persists the one-shot transcript and keeps print mode aligned with the
same prompt, resource, and tool environment used by the TUI.

## Tests

The phase is covered by:

```text
tests/test_rendering.py
tests/test_cli.py
```

The tests verify:

- transcript streaming behavior
- final text behavior
- JSON Lines output
- non-recoverable error handling
- CLI output-mode integration

## Non-goals

This phase does not add:

- Textual UI
- keyboard input
- session picker
- diff viewer
- markdown renderer
- theme system

Those belong to later frontend phases.

## Next phase

The next phase can build the Textual TUI behind the same event boundary, or first add optional Rich styling inside `tau_coding` without changing `tau_agent`.
