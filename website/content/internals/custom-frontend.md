---
title: Build your own frontend
description: Advanced — drive Tau's coding session from your own UI by consuming its event stream.
---

{{% caution title="Advanced" %}}
This page is for building a *new frontend* on top of Tau's core. If you just want
to use Tau, see [The interactive session]({{< relref "../guides/tui.md" >}}). The APIs here are
Python and assume you've read the [architecture overview]({{< relref "./architecture.md" >}}).
{{% /caution %}}

Tau's Textual app is one frontend, not the architecture. A custom UI plugs into
the same primitives the built-in TUI uses:

```text
CodingSession   — owns the coding-agent environment
AgentEvent      — describes assistant text, tool calls, results, errors
Frontend state  — belongs to your UI
```

The reusable `tau_agent` package stays independent of terminal frameworks,
widgets, keybindings, config paths, and slash-command UX. Build against
`tau_coding.session.CodingSession`, not Textual widgets.

`CodingSession` provides the environment (provider/model, tools, persistence,
skills, prompt templates, project context, slash-command handling, compaction).
Your frontend provides the interface (prompt input, transcript rendering, command
entry, cancellation, pickers).

## Minimal event loop

```python
async for event in session.prompt(user_text):
    render_event(event)
```

The stream yields provider-neutral `AgentEvent` values from `tau_agent.events`
(see [the agent loop]({{< relref "./agent-loop.md" >}}) for the list). Render from these, never
from provider-specific chunks. Treat `AgentStartEvent`/`AgentEndEvent` and
non-recoverable `ErrorEvent` as the source of truth for "is the agent working?".

## Steering and follow-ups

If the user submits while a run is active, queue instead of starting a second
run:

```python
async for event in session.prompt(user_text, streaming_behavior="steer"):
    adapter.apply(event); redraw(state)
```

Use `streaming_behavior="follow_up"` for a prompt that waits until the run would
otherwise stop. Overlapping `session.prompt(...)` calls without
`streaming_behavior` are rejected so two runs can't mutate one transcript.
`QueueUpdateEvent` carries pending queued text for badges/status.

## Slash commands

Slash commands belong to `tau_coding`. Before treating input as a prompt:

```python
result = session.handle_command(text)
```

If `result.handled`, apply the requested effect (`exit_requested`,
`clear_requested`, `new_session_requested`, `compact_summary`, `message`) and
show reference/status output *outside* the durable conversation. If
`result.compact_summary is not None`, call `await session.compact(result.compact_summary)`
(an empty string means "use the built-in prompt as-is").

`/skill:<name>` is intentionally **not** a command — pass it through to
`session.prompt(...)`, which expands it before the run.

## Restoring and switching sessions

Initialize the visible transcript from `session.messages` (the built-in
`TuiState.load_messages()` is a reference). `ToolResultMessage` preserves
structured metadata (e.g. edit patches), so you can render restored tool results
without reading JSONL directly.

For session switching, use `tau_coding.session_manager.SessionManager` —
`list_sessions(session.cwd)`, then `await session.resume(session_id)` (or load a
fresh `CodingSession` with `storage=jsonl_session_storage(record.path)`), then
rebuild the transcript from `session.messages`.

## Cancellation, pickers, keybindings

- Cancel with `session.cancel()` — keep consuming events until the stream ends.
- Read picker data directly from the session: `command_registry.list_commands()`,
  `skills`, `prompt_templates`, `available_model_choices`, `available_models`,
  `available_providers`, `thinking_level`, `available_thinking_levels`,
  `session_manager`. For model changes from another provider, call
  `set_provider(...)` then `set_model(...)`.
- Keybindings and themes are **frontend policy**. The built-in app reads
  `~/.tau/tui.json` via `tau_coding.tui.load_tui_settings()`, but your UI can
  ignore it.

## What not to depend on

Avoid coupling to private `CodingSession` attributes, provider-specific response
chunks, Textual internals, or the raw JSONL structure (use `SessionManager` /
`CodingSession`). Stick to the event, message, tool, harness, and session
primitives.

{{% note %}}
The full per-phase build journals for these systems live in the repo under
`dev-notes/` (see [Contributing]({{< relref "../contributing.md" >}})).
{{% /note %}}
