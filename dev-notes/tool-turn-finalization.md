# Tool submissions and turn finalization

## Problem

A purpose-built agent can finish by submitting a structured result through a tool.
Previously `AgentToolResult.terminate` existed, but the loop never read it. Even a
successful submission therefore caused another model call, often only to say
"submitted." Calling `harness.cancel()` was not equivalent: cancellation marks an
interrupted run rather than successful completion.

## Pi reference

Reviewed `earendil-works/pi` at `b9ac5c1a0d89`:

- `packages/coding-agent/examples/extensions/structured-output.ts` demonstrates
  final data in `details` with `terminate: true`.
- `packages/agent/src/agent-loop.ts` computes an all-final-results batch decision
  and uses it when scheduling another model request.
- Its `finishTurn` callback runs after results but before `turn_end`, applying an
  explicit end decision before polling queues. Continuation selects one request,
  not an additional request on top of tools or queued input.

Tau ports these behaviors rather than introducing a special built-in tool name.

## Implementation

`tau_agent.loop` counts only final, post-hook tool result flags. A nonempty batch
with every flag exactly `True` skips automatic continuation. Sibling tools still
execute, mixed batches continue, progress flags have no effect, and queued input
still receives responses. Cancellation remains distinct and is not swallowed by
an otherwise terminating batch. The result flag is observable in tool-execution
events but deliberately absent from persisted `ToolResultMessage` values.

`tau_agent.turns` defines frontend-free frozen dataclasses:

- `AgentTurnContext(message, tool_results, messages, new_messages)`;
- `AgentTurnDecision(action="end" | "continue")`;
- `FinishTurn`, the sync-or-async callback type receiving context and signal.

`AgentHarnessConfig.finish_turn` forwards to the pure loop. `end` emits normal
turn/run end events without consuming more queues; `continue` is satisfied by the
next naturally scheduled request or, if none exists, one context-only request.
Error/aborted responses and synthetic cancellation/turn-limit exits invoke the
callback for observation but ignore its scheduling decision. Callback failures
propagate like the loop's existing host hooks; harness teardown still resets its
running state. Async cancellation is not caught or converted to success.

**Ruling:** Python contexts use named tuple snapshots of the full transcript and
new messages rather than Pi's mutable `AgentContext` object. They share message
objects and are documented read-only, without adding provider/system/tool mutation
APIs or deep-copy cost. Callbacks receive the active cancellation token explicitly.
This stays entirely in the reusable `tau_agent` layer. `CodingSessionConfig` and
extension registration do not gain a separate callback API in this change.

## Usage and validation

Run `tau -e examples/extensions/structured_output.py` and ask for a structured
repository summary. The example validates `headline`, `summary`, and `actionItems`
and returns them in result `details`, with readable text and `terminate=True`.
It writes no external file. The model should call it alone as the final action;
a mixed batch deliberately retains normal continuation.

For host code, see the typed callback example in
`website/content/internals/agent-loop.md`. The same example tool ships in the
installed package under `src/tau_coding/data/examples/extensions/`.

Deterministic checks:

```bash
uv run pytest tests/test_agent_turns.py tests/test_tool_turn_extensions.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
```

The focused tests cover all/mixed batches, hook overrides, progress flags,
queued work, one-request continuation, callback ordering and snapshots, hard
exits, exception/cancellation propagation, real extension loading, and durable
submission results across resume. No real provider is needed.
