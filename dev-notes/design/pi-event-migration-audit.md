# Pi-like Event Migration Audit

**Worktree:** `~/.agents/worktrees/tau/pi-event-extension-parity`
**Branch:** `feat/pi-event-extension-parity`
**Date:** 2026-07-16

## Executive summary

The `tau_ai` and `tau_agent` event models are substantially cut over to the Pi-like shape. However, the migration is incomplete and inconsistent in four main areas:

1. **`tau_ai`** still uses transitional `_provider_events.py` internally; public Pi events are produced only through `canonicalize_provider_stream()`.
2. **`tau_agent`** no longer emits several old events (`retry`, `queue_update`, `message_delta`, `thinking_delta`, `error`), but the replacement semantics are only partially wired.
3. **`tau_coding`** defines new session-level events (`agent_settled`, `auto_retry_start/end`, `compaction_start/end`, etc.) but most are never actually emitted.
4. The **extension API** still advertises the old Tau-v1 event names and handler signatures, even though the core no longer produces those events.

In addition, the circular dependency between `tau_agent` and `tau_ai` that PR #332 / issue #317 addressed has reappeared.

---

## 1. `tau_ai` layer

### What changed

- New canonical public events are defined in `src/tau_ai/events.py`:
  - `AssistantStartEvent`, `AssistantDoneEvent`, `AssistantErrorEvent`
  - `TextStartEvent`, `TextDeltaEvent`, `TextEndEvent`
  - `ThinkingStartEvent`, `ThinkingDeltaEvent`, `ThinkingEndEvent`
  - `ToolCallStartEvent`, `ToolCallDeltaEvent`, `ToolCallEndEvent`
- The provider protocol now returns `AsyncIterator[AssistantMessageEvent]` (`src/tau_ai/provider.py`).

### Inconsistencies

- **Transitional internal protocol still exists.** Every provider implementation (`anthropic.py`, `google.py`, `mistral.py`, `openai_compatible.py`, `openai_codex.py`) still emits the old `ProviderResponseStartEvent`, `ProviderTextDeltaEvent`, `ProviderToolCallEvent`, `ProviderResponseEndEvent`, etc. from `src/tau_ai/_provider_events.py`. These are then adapted by `canonicalize_provider_stream()` in `src/tau_ai/stream.py`.
- **No true streaming tool-call deltas.** The parsers emit a single completed `ProviderToolCallEvent`. `canonicalize_provider_stream()` maps this to `ToolCallStartEvent` immediately followed by `ToolCallEndEvent` with no `ToolCallDeltaEvent` in between. Pi expects tool-call arguments to stream via `toolcall_delta`.
- **Tool-call content is not inserted at the correct content index.** In `stream.py:112-120`, the start event is yielded before appending the block, so the `partial` snapshot in `ToolCallStartEvent` does not yet contain the tool call.
- **Retry events are swallowed.** `canonicalize_provider_stream()` drops `ProviderRetryEvent` (`stream.py:71-73`). Pi exposes retry at the coding-session layer, but Tau currently loses it entirely between `tau_ai` and `tau_coding`.
- **`AssistantErrorEvent` vs `AssistantDoneEvent` ambiguity.** `AssistantDoneEvent` carries `reason: StopReason`, and `AssistantErrorEvent` also carries `reason`. Consumers must check both event type and message `stop_reason` to detect failure.
- **Provider final message reconstruction is fragile.** `stream.py:145-158` rebuilds `final.content` by taking streamed thinking, final text, and final tools. This can reorder content relative to the provider's actual interleaving.

---

## 2. `tau_agent` layer

### What changed

- New event model in `src/tau_agent/events.py`:
  - `AgentStartEvent`, `AgentEndEvent`
  - `TurnStartEvent`, `TurnEndEvent`
  - `MessageStartEvent`, `MessageUpdateEvent`, `MessageEndEvent`
  - `ToolExecutionStartEvent`, `ToolExecutionUpdateEvent`, `ToolExecutionEndEvent`
- `MessageDeltaEvent` and `ThinkingDeltaEvent` are gone. Text/thinking deltas now arrive as `MessageUpdateEvent` wrapping a `tau_ai` `TextDeltaEvent`/`ThinkingDeltaEvent`.
- `ErrorEvent` is gone. Errors are terminal `AssistantMessage` objects with `stop_reason == "error"`.
- `RetryEvent` and `QueueUpdateEvent` are removed from `AgentEvent`.

### Inconsistencies

- **`AgentEndEvent` payload does not match Pi.** Pi's `agent_end` carries the generated `messages`. Tau's `AgentEndEvent` currently has no fields (`loop.py:19`, `events.py:19`). The messages are computed but not attached to the event (`loop.py:168` yields `AgentEndEvent()` with no arguments).
- **`TurnEndEvent` payload is incomplete.** It carries `message` and `tool_results`, but Pi's turn end also carries final state such as the authoritative assistant message and tool results. The current dataclass has no `tool_results` field in the definition shown at `events.py:28`, yet the loop constructs `TurnEndEvent(message=assistant, tool_results=tool_results)` (`loop.py:158`). This works only because the model is permissive, but the schema is not explicit.
- **Tool-execution update semantics changed without updating consumers.** `ToolExecutionUpdateEvent` now carries `partial_result: AgentToolResult` (`events.py:60`), but the old field was a `str` message. The TUI adapter uses `.text` (`adapter.py:82`), which happens to work, but this is an implicit contract.
- **Tool-result messages do not emit `message_start`/`message_end`.** In `loop.py:255-260`, a `ToolResultMessage` is appended after `ToolExecutionEndEvent`, but no `MessageStartEvent`/`MessageEndEvent` is yielded for it. Pi emits message lifecycle events for tool results.
- **Cancellation produces no explicit event.** The loop checks `signal.is_cancelled()` inside tool execution but does not emit a dedicated cancellation event; it relies on an error result.
- **Circular dependency reintroduced.** `tau_agent` imports `tau_ai`:
  - `src/tau_agent/loop.py:29-35` imports from `tau_ai.events` and `tau_ai.provider`.
  - `src/tau_agent/events.py:12` imports `AssistantMessageEvent` from `tau_ai.events`.
  - `src/tau_agent/harness.py:22` imports `ModelProvider` from `tau_ai.provider`.

  Meanwhile `tau_ai` imports `tau_agent.messages`, `tau_agent.tools`, `tau_agent.types` in virtually every module. This is the same cycle PR #332 inverted by moving the provider protocol into `tau_agent`.

---

## 3. `tau_coding` layer

### What changed

- New `src/tau_coding/events.py` defines session-level events:
  - `AgentSettledEvent`
  - `QueueUpdateEvent`
  - `CompactionStartEvent`, `CompactionEndEvent`
  - `EntryAppendedEvent`
  - `SessionInfoChangedEvent`
  - `ThinkingLevelChangedEvent`
  - `AutoRetryStartEvent`, `AutoRetryEndEvent`
- `CodingSessionEvent = AgentEvent | SessionOwnEvent`.

### Inconsistencies

- **`AgentSettledEvent` is defined but never emitted.** Searching `src/tau_coding/session.py` finds no `AgentSettledEvent(...)` yield. The TUI adapter handles it (`adapter.py:34-37`) but falls back to `AgentEndEvent` because it never arrives.
- **`AutoRetryStartEvent` / `AutoRetryEndEvent` are never emitted.** The overflow-retry path in `session.py:1492-1518` re-runs the harness but does not emit retry lifecycle events.
- **`CompactionStartEvent` / `CompactionEndEvent` are never emitted.** Compaction runs in `_append_compaction` (`session.py:1903-1925`) and `_try_overflow_compact` but yield no events.
- **`EntryAppendedEvent` is never emitted.** Session entries are appended without streaming a persistence event.
- **`SessionInfoChangedEvent` and `ThinkingLevelChangedEvent` are never emitted.** Model/thinking changes update persisted state silently.
- **`QueueUpdateEvent` is not yielded from `prompt()` / `continue_()`.** `session.py:778-783` exposes `queue_update_event()` as a method, and `harness.steer()` / `harness.follow_up()` return `QueuedMessages`, but no `QueueUpdateEvent` is yielded into the stream. The TUI adapter imports `QueueUpdateEvent` and handles it, but the session never produces it. (Steering during a run currently returns early with the raw `QueuedMessages` object, not an event — `session.py:1444-1448`.)
- **`SessionAgentEndEvent` duplicates `AgentEndEvent`.** `events.py:14` defines a session-level `agent_end` event with `will_retry`, but the agent layer already emits `AgentEndEvent`. The coding stream could carry two different `agent_end` shapes.
- **`CodingSession.prompt()` and `continue_()` are typed as `AsyncIterator[AgentEvent]`** (`session.py:1412`, `1529`) rather than `AsyncIterator[CodingSessionEvent]`, even though the goal is to expose session-level events.

---

## 4. Extension API

### Current state

`src/tau_coding/extensions/api.py:22-39` still advertises the old Tau-v1 observation set:

```python
AGENT_EVENT_TYPES = {
    "agent_start", "agent_end", "turn_start", "turn_end",
    "retry",          # no longer emitted by tau_agent
    "queue_update",   # no longer emitted by tau_agent
    "message_start", "message_delta", "thinking_delta", "message_end",
    "tool_execution_start", "tool_execution_update", "tool_execution_end",
    "error",          # no longer emitted by tau_agent
}
```

### Inconsistencies

- **`retry`, `message_delta`, `thinking_delta`, `error` are in the allow-list but are never produced.** Subscribing to them will silently never fire.
- **`message_update` is missing**, even though it is the only way the agent layer now streams text/thinking/tool deltas.
- **Session-level events are missing** from `AGENT_EVENT_TYPES`: `agent_settled`, `compaction_start`, `compaction_end`, `auto_retry_start`, `auto_retry_end`, `entry_appended`, `session_info_changed`, `thinking_level_changed`.
- **Handler signature is still one-argument.** `ExtensionHandler = Callable[[object], object | Awaitable[object]]` (`api.py:404`). The planned Pi-like API is `handler(event, context)`, but handlers are invoked as `handler(event)` (`runtime.py:812`, `819`).
- **`ToolCallHookEvent` / `ToolResultHookEvent` use the new `AgentToolResult` but expose old-shaped fields implicitly.** `ToolResultHookResult` still returns `content: str | None`, `ok: bool | None`, `details: dict | None` (`api.py:399-401`), which assumes the old result envelope. The runtime must convert this to the new `AgentToolResult` shape.
- **`InputEvent` still has the old `streaming_behavior` string** rather than a typed enum, and handlers are invoked without context.
- **`ExtensionCommandHandler` is sync-only** (`api.py:407`), whereas the Pi-like plan calls for async command handlers.

---

## 5. Message and tool model inconsistencies

### `UserMessage`

- Old Tau allowed `UserMessage` to carry `custom_type` and `details` for custom messages.
- New `UserMessage` (`messages.py:110-118`) has only `content` and `timestamp`. Custom messages are now a separate `CustomMessage` class.
- Several code paths still assume `UserMessage.custom_type` exists. For example, `TuiState.load_messages()` was recently patched to dispatch by class, but `session.py:1459-1465` constructs a `CustomMessage` when `custom_type` is provided. This split is correct in principle but must be propagated everywhere.

### `AgentToolResult`

- Old fields removed: `tool_call_id`, `name`, `ok`, `data`, `error`.
- New fields: `content` (block list), `details`, `added_tool_names`, `terminate`.
- The validator silently drops old fields (`tools.py:40-44`), which masks migration issues rather than failing fast.
- The `ToolResultRenderer` protocol does not accept the tool name (`tools.py:58-60`), but the TUI and extension runtime now need the name to look up the renderer. The runtime currently passes `tool_name` as a separate argument to `render_tool_result()` (`runtime.py:406-410`), while the `AgentTool.render_result` field still conforms to the two-argument protocol. This is a type/model mismatch.

### `AssistantMessage`

- `content` is now a list of blocks. A validator accepts `str` and `tool_calls` for convenience (`messages.py:165-189`), which helps but also hides places that still treat `content` as a string.
- `ToolResultMessage` now has `tool_name` and `tool_call_id` as top-level fields, but the old `name` alias is accepted only during validation. Some consumers still reference `.name`.

---

## 6. TUI and frontend inconsistencies

### `TuiEventAdapter`

- Handles `AgentStartEvent`, `AgentEndEvent`, `agent_settled`, `QueueUpdateEvent`, `MessageStartEvent`, `MessageUpdateEvent`, `MessageEndEvent`, tool events, and `AutoRetryStartEvent`.
- It now flushes and clears `state.running` on `AgentEndEvent` (`adapter.py:30-33`) because `AgentSettledEvent` is never emitted. This is a workaround, not the intended design.

### `TuiState`

- `load_messages()` was recently patched to dispatch by message class (`state.py`), which fixes the `UserMessage.custom_type` crash.
- `record_tool_result()` now takes `tool_name` separately, matching the new result model.

### Remaining issues

- The adapter does not handle `CompactionStartEvent`, `CompactionEndEvent`, `AutoRetryEndEvent`, `SessionInfoChangedEvent`, or `ThinkingLevelChangedEvent`.
- `MessageStartEvent` sets `state.assistant_buffer = event.message.text` (`adapter.py:42-43`). For an empty assistant message this is fine, but for tool-call-only assistant messages it leaves the buffer empty and the subsequent tool call is rendered without finishing any assistant text.
- `MessageUpdateEvent` handling only recognizes `TextDeltaEvent` and `ThinkingDeltaEvent`. It does not handle `ToolCallStartEvent`/`ToolCallDeltaEvent`/`ToolCallEndEvent` inside `message_update`, so the TUI relies on `MessageEndEvent` to discover tool calls.

---

## 7. Test suite status

- 11 test modules fail at collection time because they import removed symbols:
  - `ErrorEvent`, `RetryEvent`, `QueueUpdateEvent`, `MessageDeltaEvent`, `ThinkingDeltaEvent` from `tau_agent`
  - `ProviderEvent`, `ProviderErrorEvent`, `ProviderRetryEvent`, etc. in their old forms
- Affected files include `tests/test_agent_harness.py`, `tests/test_agent_loop.py`, `tests/test_agent_types.py`, `tests/test_cli.py`, `tests/test_coding_session.py`, `tests/test_extensions.py`, `tests/test_rendering.py`, `tests/test_tau_ai.py`, `tests/test_tui_adapter.py`, `tests/test_tui_app.py`, `tests/test_tui_components.py`.
- Only 340 tests collect successfully; the rest of the suite is blocked by these import errors.

---

## 8. Circular dependency regression

The intended architecture after PR #332 is:

```
tau_agent defines provider protocol and events
tau_ai implements them
tau_coding consumes them
```

Current worktree:

```
tau_agent/loop.py       -> tau_ai.events, tau_ai.provider
tau_agent/events.py     -> tau_ai.events
tau_agent/harness.py    -> tau_ai.provider
tau_ai/*                -> tau_agent.messages, tau_agent.tools, tau_agent.types
```

This creates a bidirectional import graph between `tau_agent` and `tau_ai`. Imports currently succeed because `tau_agent.types` and `tau_agent.messages` load before `tau_ai`, but the layering test from PR #332 would fail.

---

## Summary of the most critical inconsistencies

| Area | Inconsistency | Severity |
|---|---|---|
| `tau_ai` | Providers still emit old `_provider_events` internally | Medium (transitional) |
| `tau_ai` | No real `toolcall_delta` streaming | Medium |
| `tau_agent` | `AgentEndEvent` carries no `messages` | High |
| `tau_agent` | `TurnEndEvent(tool_results=...)` not reflected in schema | High |
| `tau_agent` | Tool-result messages lack `message_start`/`message_end` | Medium |
| `tau_coding` | `AgentSettledEvent` defined but never emitted | High |
| `tau_coding` | `AutoRetry*`, `Compaction*`, `EntryAppended*`, `SessionInfoChanged*`, `ThinkingLevelChanged*` never emitted | High |
| `tau_coding` | `QueueUpdateEvent` not yielded from session stream | High |
| Extensions | `AGENT_EVENT_TYPES` lists events that no longer exist | High |
| Extensions | Handler signature still one-argument | High |
| Architecture | Circular `tau_agent` ↔ `tau_ai` dependency reintroduced | High |
| Tests | 11 test modules fail to import | High |

The core text-only path works end-to-end, but any path involving session-level lifecycle, retries, compaction, tool-call streaming, or extensions is only partially migrated.


## Remediation update

The follow-up implementation addressed several audit findings:

- Moved the provider contract and canonical assistant events into `tau_agent`, with
  `tau_ai` retaining identity-preserving public re-exports. A layering test now
  prevents `tau_agent -> tau_ai` imports.
- Added focused canonical event-order and tool-result lifecycle tests.
- Preserved streamed block ordering and fixed tool-call start snapshots.
- Added coding-session `agent_end` (`willRetry`) and `agent_settled` events, queue
  updates, and overflow compaction/retry lifecycle events.
- Replaced the extension event allow-list with canonical event names and dispatches
  fresh `(event, context)` handler arguments, including session-owned events.
- Migrated built-in tools, provider payload conversion for session-only messages,
  print frontends, and the hello-tool example to canonical models.
- Verified real text and tool-call streams manually in JSON and text modes.

The cutover is now complete at every public boundary:

- The full Tau suite passes on canonical models and events; mypy and Ruff are clean.
- Extension handlers consistently receive fresh `(event, context)` arguments, and
  shipped examples plus the published guide use canonical tool definitions/results.
- Persisted Tau-v1 JSONL messages migrate at the storage boundary and rewrite as
  canonical assistant blocks, `toolResult` messages, and dedicated custom messages.
- Runtime models reject removed Tau-v1 fields instead of silently discarding them.
- Print, JSON, TUI, exporters, branch summaries, and provider tests consume one
  protocol.

Provider implementations still use `_provider_events.py` plus `stream.py` as a
**private parser-normalization implementation**. This is not an advertised profile:
`ModelProvider`, `tau_ai.events`, and every provider's `stream_response()` expose
only identity-preserving canonical `tau_agent` events. Removing that parser helper
is optional internal simplification, not a release or compatibility blocker.
