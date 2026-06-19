# Building a Custom TUI

Tau's Textual app is one frontend, not the agent architecture itself. A custom
TUI should plug into the same primitives that the built-in Textual frontend uses:

```text
CodingSession
  owns the coding-agent environment

AgentEvent stream
  describes assistant text, tool calls, tool results, and errors

Frontend state
  belongs to the UI implementation
```

The reusable `tau_agent` package must stay independent of terminal frameworks,
widgets, keybindings, local config paths, and slash-command UX.

## Recommended Boundary

Build custom frontends against `tau_coding.session.CodingSession`, not directly
against Textual widgets.

`CodingSession` provides the application environment:

- configured provider and model
- built-in coding tools
- session persistence
- skills and prompt templates
- project context files
- slash-command handling
- context compaction

The frontend provides the interface:

- prompt input
- transcript rendering
- command entry or command palette
- cancellation controls
- status indicators
- optional model/session pickers

## Minimal Event Loop

A custom TUI can run one user prompt by iterating the session event stream:

```python
async for event in session.prompt(user_text):
    render_event(event)
```

The event stream contains provider-neutral `AgentEvent` values from
`tau_agent.events`, including:

- `AgentStartEvent` and `AgentEndEvent`
- `MessageStartEvent`, `MessageDeltaEvent`, and `MessageEndEvent`
- `QueueUpdateEvent`
- `ThinkingDeltaEvent`
- `ToolExecutionStartEvent`, `ToolExecutionUpdateEvent`, and `ToolExecutionEndEvent`
- `ErrorEvent`

Do not render from provider-specific chunks. The provider layer translates model
output into Tau events so every frontend can share the same behavior.

`ThinkingDeltaEvent` carries optional streamed reasoning text from providers
that expose it. Keep it separate from assistant message text, hide it by
default, and let the user opt in from the frontend. The built-in Textual app
uses `Ctrl+T` for this toggle and renders thinking content with a distinct
transcript style.

`QueueUpdateEvent` carries the current pending steering and follow-up prompt
text. Use it for pending-message badges or status text. Queued prompts are not
durable conversation messages until the harness injects them and emits normal
`MessageStartEvent`/`MessageEndEvent` user-message events.

## Queued Steering And Follow-ups

If a user submits more text while `AgentStartEvent` has marked the session as
running, queue it instead of starting a second prompt worker:

```python
async for event in session.prompt(user_text, streaming_behavior="steer"):
    adapter.apply(event)
    redraw(state)
```

Use `streaming_behavior="follow_up"` for a follow-up shortcut that should wait
until the active run would otherwise stop. The built-in Textual frontend maps
normal `Enter` while running to steering and `Alt-Enter` to follow-up queueing.
Direct overlapping `session.prompt(...)` calls without `streaming_behavior` are
rejected so callers do not mutate the same transcript from concurrent runs.

## Restoring Visible State

When opening an existing session, initialize the visible transcript from:

```python
session.messages
```

The built-in Textual frontend uses `TuiState.load_messages()` as a reference
implementation. A custom frontend can use that class directly or implement its
own display state. Either way, restored messages should produce the same user,
assistant, tool-call, and tool-result blocks that live events produce.
`ToolResultMessage` preserves structured metadata such as edit patches, so a
custom frontend can render restored tool-result details without reading JSONL
session files directly.

## Handling Slash Commands

Slash commands are owned by `tau_coding`, not by `tau_agent`.

Before treating input as an agent prompt, call:

```python
result = session.handle_command(text)
```

If `result.handled` is true, the frontend should apply the requested UI effect
or show `result.message` outside the durable conversation when the message is
command reference or status text. The built-in Textual frontend uses
notifications for short command results and a dismissible modal for multi-line
output such as `/help`, `/skills`, `/resume`, `/status`, `/resources`, and
`/context`.

Existing result fields include:

- `exit_requested`
- `clear_requested`
- `new_session_requested`
- `compact_summary`
- `message`

If `result.compact_summary` is set, call:

```python
message = await session.compact(result.compact_summary)
```

Then show the returned status message as transient UI unless the custom
frontend intentionally wants command status entries in its transcript.

The `/skill:<name> [request]` form is intentionally a prompt-expansion path, so
it is not consumed by normal command handling. Pass it through to
`session.prompt(...)`; `CodingSession` expands it before the agent run.

## Cancellation

Expose cancellation through:

```python
session.cancel()
```

Cancellation is a request to stop the active agent turn. The frontend should
still continue consuming events until the stream ends or reports an error, then
update its running state. The built-in Textual app treats the recoverable
`Agent run cancelled` event as status text because it represents an intentional
user action, not a failed provider or tool call.

## Autocomplete and Pickers

The built-in Textual app uses:

```python
from tau_coding.tui import CompletionOption

build_completion_state(
    text,
    command_registry=session.command_registry,
    skills=session.skills,
    prompt_templates=session.prompt_templates,
    model_names=session.available_models,
    provider_names=session.available_providers,
    session_options=[
        CompletionOption(
            value=record.id,
            description=f"{record.title or 'Untitled session'} - {record.model} - {record.cwd}",
        )
        for record in session.session_manager.list_sessions()
    ]
    if session.session_manager
    else (),
)
```

Custom TUIs can reuse this helper for Pi-style slash-command completion,
`/skill:` completion, and lightweight model/login/session argument pickers.
Use `CompletionOption` when a picker row should apply one value but show richer
metadata such as a session title, model, or working directory.

The command layer also exposes picker requests for full-screen or modal flows.
When `session.handle_command("/model")` returns
`CommandResult(model_picker_requested=True)`, show an interactive list backed by
`session.available_model_choices`. Each choice contains `provider_name` and
`model`. When the user selects a model from another provider, call
`session.set_provider(provider_name)` first, then `session.set_model(model)`.
This keeps the picker as frontend policy while the agent session remains the
source of truth for the active provider and model.

When the user presses `Enter` with a highlighted completion that would change
the prompt text, apply the completion and keep focus in the input instead of
submitting immediately. If the highlighted completion would not change the text,
the frontend can treat `Enter` as normal submission.

A custom picker UI can also read the same data directly:

- `session.command_registry.list_commands()`
- `session.skills`
- `session.prompt_templates`
- `session.available_model_choices`
- `session.available_models`
- `session.available_providers`
- `session.thinking_level`
- `session.available_thinking_levels`
- `session.session_manager`

## Activity State

Frontends should treat `AgentStartEvent`/`AgentEndEvent` and non-recoverable
`ErrorEvent` values as the source of truth for whether the agent is working.
The built-in Textual app renders a subtle animated status indicator while the
session is running and resets it to `Ready` when the run completes, is cancelled,
or fails. Custom TUIs can use the same event-driven state instead of checking
provider-specific streaming details.

## Keybindings

Keybindings and themes are frontend policy. The built-in Textual app reads optional
settings from `~/.tau/tui.json` through `tau_coding.tui.load_tui_settings()`,
but a custom TUI can ignore that file or map the same action names to its own
input and styling system.

The built-in configurable action names are:

- `cancel`
- `command_palette`
- `session_picker`
- `queue_follow_up`
- `accept_completion`
- `completion_next`
- `completion_previous`
- `thinking_cycle`
- `toggle_thinking`
- `toggle_tool_results`
- `copy_message`
- `quit`

The built-in themes are `tau-dark` and `high-contrast`. They resolve to
`TuiTheme` objects used by the Textual app and widgets; custom frontends can
reuse those objects or define their own palette outside `tau_agent`.

## Session Switching

Session records are managed by `tau_coding.session_manager.SessionManager`.

For a custom session picker:

1. List records with `SessionManager.list_sessions(session.cwd)`.
2. Let the user choose a record id.
3. Create a new `CodingSessionConfig` with `storage=jsonl_session_storage(record.path)`.
4. Load the session with `await CodingSession.load(config)`.
5. Rebuild the visible transcript from `session.messages`.

If the frontend is already holding a `CodingSession`, it can also call:

```python
message = await session.resume(session_id)
```

Then clear and rebuild the visible transcript from `session.messages`.

Keep the picker in the frontend package. The reusable agent harness should not
know how sessions are displayed or selected.

## Reference Adapter

The built-in Textual frontend has a small adapter you can copy or reuse:

```python
state = TuiState()
state.load_messages(session.messages)
adapter = TuiEventAdapter(state)

async for event in session.prompt(user_text):
    adapter.apply(event)
    redraw(state)
```

`TuiEventAdapter` is deliberately small. It translates agent events into display
items but does not own widgets, layouts, colors, or terminal behavior.

The reference formatter previews long tool-result content before adding it to
visible state. Custom TUIs should avoid rendering full `read` or `bash` output
by default; show a compact preview in the transcript and provide an explicit
way to inspect the full result if the frontend needs one.

## What Not To Depend On

Avoid custom UI dependencies on:

- private attributes of `CodingSession`
- provider-specific response chunks
- Textual widget internals unless you are extending the built-in Textual app
- JSONL session file structure when `SessionManager` or `CodingSession` can be used
- `tau_agent` internals that are not part of the event, message, tool, harness, or session primitives

## Verification Checklist

A custom TUI should prove these behaviors before it is considered compatible:

- `tau_agent` has no dependency on the frontend framework.
- Opening a session restores prior user, assistant, and tool blocks.
- Restored tool results use persisted metadata for details such as edit patches.
- Prompt submission streams assistant deltas and tool events live.
- Slash commands run through `session.handle_command()`.
- Command reference/status output does not pollute the durable conversation.
- `/skill:<name>` prompts pass through to `session.prompt()`.
- Completion `Enter` applies the highlighted completion before submission.
- Cancellation calls `session.cancel()`.
- Large tool results render as compact previews by default.
- Keybindings and themes are owned by the frontend and do not leak into `tau_agent`.
- Provider/model choices come from the session, not hardcoded UI lists.
- Session persistence is handled through `CodingSession` and `SessionManager`.
