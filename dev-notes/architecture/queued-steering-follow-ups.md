---
title: "Queued Steering And Follow-ups"
---

This slice adds Pi-style message queueing while an agent run is active.

## What Was Added

`tau_agent.AgentHarness` now owns two prompt queues:

- steering messages, injected after the current assistant turn and any tool batch
- follow-up messages, injected only when the run would otherwise stop

The harness exposes `steer()`, `follow_up()`, `clear_queues()`,
`queued_messages`, `pending_message_count`, and `is_running`. Direct overlapping
`prompt()` and `continue_()` calls are rejected so callers cannot mutate one
transcript from two active runs.

`run_agent_loop()` accepts provider-neutral queue-drain callbacks. When a queue
drains, the loop appends the queued user message to the same message list used
for normal prompts and emits ordinary user `MessageStartEvent` and
`MessageEndEvent` values before the next provider call.

`QueueUpdateEvent` reports pending steering and follow-up text for frontends.
The Textual TUI uses it for status-line queue counts.

## Coding Session Boundary

`CodingSession.prompt()` keeps ordinary non-running prompts unchanged. While the
harness is running, callers must pass one of:

```python
session.prompt(text, streaming_behavior="steer")
session.prompt(text, streaming_behavior="follow_up")
```

The session expands `/skill:<name>` prompt text before queueing. It does not
persist a queued message at queue time. Persistence still happens after the
active run finishes, when the harness has injected queued user messages into the
transcript.

## TUI Behavior

The built-in Textual frontend maps:

- `Enter` while running to steering queueing
- `Alt-Enter` while running to follow-up queueing
- `Up` on an empty prompt while running to edit the latest queued follow-up

Pending queues are visible above the prompt while the run continues. If several
follow-ups are queued, edit pulls the most recently queued follow-up back into
the prompt and removes it from the queue. Once a queued message is injected, it
appears in the transcript as a normal user message and the pending queue display
updates.

## Boundary

Queue ownership lives in `tau_agent`, prompt expansion and persistence stay in
`tau_coding`, and Textual only decides keybindings and presentation. Provider
adapters do not know about queued messages; they receive the updated transcript
on the next provider call.

## Tests

The behavior is covered by:

```text
tests/test_agent_loop.py
tests/test_agent_harness.py
tests/test_coding_session.py
tests/test_tui_adapter.py
tests/test_tui_app.py
```
