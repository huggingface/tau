---
title: "Phase 21: Extensions"
---

Tau extensions are Python modules that customize a coding session: they add
tools and slash commands, observe the agent event stream, and intercept tool
calls, tool results, and user input. The design is a deliberate port of Pi's
extension system (`packages/coding-agent/src/core/extensions/` in
`earendil-works/pi`) onto Tau's Python architecture, scoped so the core is
small while still supporting real extensions such as a Claude Code-style
subagents extension.

This design was revised after an adversarial review; the notable v1 rulings
are called out inline as **Ruling:** notes.

## Goals

- Load extensions from user and project directories with the same discovery
  conventions as skills and prompt templates.
- Give extensions a single `ExtensionAPI` object with Pi-aligned naming:
  `register_tool`, `register_command`, `on(event)`, `send_user_message`,
  `append_entry`, and read access to session context.
- Support Pi's load-bearing hook semantics: `tool_call` (block/mutate),
  `tool_result` (transform), `input` (transform/handle), plus observation of
  every portable `AgentEvent`.
- Keep `tau_agent` untouched: the extension machinery lives entirely in
  `tau_coding`, using existing seams (`AgentHarness.subscribe`, executor
  wrapping, `CommandRegistry`, `CustomEntry`).
- Isolate failures: a broken extension is a `ResourceDiagnostic`, never a
  crashed session.

## Non-goals (this phase)

- npm-style package management (`pi install`), provider registration,
  custom TUI components/widgets/renderers, shortcut and flag registration,
  system-prompt replacement, `context`/`before_provider_request` rewriting,
  partial tool-result streaming (`onUpdate`), and a project trust store.
  These have reserved names and documented extension points but no
  implementation yet.

When any of these lands, design it from Pi's implementation first
(`packages/coding-agent/src/core/extensions/` and `docs/extensions.md` in
`earendil-works/pi`) and port that design — names, semantics, event shapes —
unless a strictly better way exists. Deviations get a **Ruling:** note with
the reason, like the ones below.

## Discovery and loading

Extension locations, in load order (first-registered wins on name conflicts,
matching Pi's project-first precedence — note this deliberately diverges from
skills/prompts, which use last-wins precedence):

1. `<cwd>/.tau/extensions/` — project extensions (**off by default**, see
   Security)
2. `~/.tau/extensions/` — user extensions
3. Paths passed explicitly (`tau --extension/-x PATH`, repeatable; a file or
   a directory)

Within a directory, one level deep, matching Pi:

- `*.py` files are extension modules
- a subdirectory containing `extension.py` is an extension (the analog of
  Pi's `index.ts` convention)

Names starting with `_` or `.` are skipped. Symlinked files are followed.

Each module is imported with `importlib` under a unique synthetic module name
(`tau_extension_<slug>_<n>`), so project and user extensions with the same
file name cannot collide in `sys.modules`. Directory extensions are imported
as real packages (`submodule_search_locations` set to the directory), so
sibling modules are reached with relative imports (`from . import helper`)
and land in `sys.modules` under the synthetic namespace. **Ruling:** the
loader does not touch `sys.path`; absolute intra-extension imports are
unsupported, which keeps helpers reload-safe and collision-free.

The module must define:

```python
def setup(tau: ExtensionAPI) -> None: ...
```

**Ruling:** `setup` is sync-only in v1 (a coroutine-function `setup` is a
load error). This keeps discovery callable from the sync `/reload` path.
Import errors, a missing `setup`, and exceptions raised by `setup` are
captured as `ResourceDiagnostic` (`kind="extension"`, `severity="error"`)
and the extension is skipped.

`tau --no-extensions` disables directory discovery entirely (explicit
`--extension` paths still load, matching Pi's CLI-survives semantics).

## Package layout

```text
src/tau_coding/extensions/
    __init__.py     public re-exports
    api.py          ExtensionAPI, ExtensionContext, hook payload/result types
    loader.py       discovery + importlib loading + diagnostics
    runtime.py      ExtensionRuntime: hook dispatch, tool wrapping,
                    command collection, harness/session binding
```

`tau_agent` remains free of extension imports. The runtime consumes only
public `tau_agent` types (`AgentTool`, `AgentToolResult`, `AgentEvent`,
`CustomEntry`). Hook payload/result types are frozen dataclasses (pydantic
stays reserved for `tau_agent` wire types).

## ExtensionAPI surface (v1)

```python
class ExtensionAPI:
    # registration (valid during setup and afterwards)
    def register_tool(self, tool: AgentTool) -> None: ...
    def register_command(
        self, name: str, handler: ExtensionCommandHandler, *,
        description: str = "", usage: str | None = None,
        aliases: tuple[str, ...] = (),
    ) -> None: ...
    def on(self, event: str, handler: ExtensionHandler | None = None): ...
        # usable as api.on("tool_call", fn) or @api.on("tool_call")

    # actions (valid once the session is bound; raise ExtensionError before)
    def send_user_message(
        self, content: str, *, deliver_as: Literal["steer", "follow_up"] = "follow_up",
    ) -> None: ...
    async def append_entry(self, namespace: str, data: dict[str, JSONValue]) -> None: ...
    def notify(self, message: str, level: Literal["info", "warning", "error"] = "info") -> None: ...

    # context (read-only)
    @property
    def context(self) -> ExtensionContext: ...
```

`ExtensionContext` exposes `cwd`, `model`, `provider_name`, `session_id`,
`system_prompt`, `is_running`, and `has_ui`. It is a live view over the
bound `CodingSession`; action methods raise `ExtensionError` if called
before binding (Pi's throwing-stubs-then-`bindCore` model).

Event handlers may be sync or async; async handlers are awaited. Handlers
run on the session's event loop, so they must be fast — slow work belongs in
a spawned task. Every handler invocation is wrapped in try/except — a
raising handler is recorded as a runtime diagnostic and dispatch continues.
The one deliberate exception, matching Pi: a raising `tool_call` hook blocks
the tool (fail-safe).

### Events

Observation events reuse the `AgentEvent` `type` literals directly:
`agent_start`, `agent_end`, `turn_start`, `turn_end`, `message_start`,
`message_delta`, `thinking_delta`, `message_end`, `tool_execution_start`,
`tool_execution_update`, `tool_execution_end`, `error`, `retry`,
`queue_update`. These are delivered from `AgentHarness.subscribe` and cannot
mutate anything. `api.on("agent_event", fn)` is the wildcard (note: it fires
per streamed token delta; prefer specific events).

Lifecycle events, dispatched by the runtime:

| Event | Payload | Result |
|---|---|---|
| `session_start` | `SessionStartEvent(reason: "startup" \| "reload" \| "new" \| "resume" \| "branch")` | — |
| `session_shutdown` | `SessionShutdownEvent(reason)` | — |
| `input` | `InputEvent(text)` | `InputHookResult(action="continue" \| "transform" \| "handled", text=None, message=None)` |
| `tool_call` | `ToolCallHookEvent(tool_name, arguments)` | `ToolCallHookResult(block=False, reason=None, arguments=None)` |
| `tool_result` | `ToolResultHookEvent(tool_name, arguments, result)` | `ToolResultHookResult(content=None, ok=None, details=None)` |

**Ruling:** the `tool_call`/`tool_result` hook payloads carry no
`tool_call_id`. The hooks are implemented by wrapping tool executors, and
the executor signature (`tau_agent/tools.py`) does not receive the call id —
the loop stamps it after execution. Extensions that need id correlation use
the observation events (`tool_execution_start/end`), which carry the full
`ToolCall`/`AgentToolResult`.

**Ruling:** provider token usage is surfaced on `AssistantMessage.usage`
(matching Pi's placement on `AssistantMessage.usage`, `packages/ai/src/types.ts`),
so extensions read real billed usage from the `message_end` observation event as
`event.message.usage` — e.g. `event.message.usage.input`, `.output`,
`.cache_read`, `.cache_write`, `.cache_write_1h`, `.reasoning`, `.total_tokens`.
`usage` is `Usage | None`: it is `None` when the provider reported no usage
(rather than Pi's always-present zeroed object), so a downstream extension can
distinguish "not reported" from "genuinely zero". Two field-level deviations from
Pi, both because Tau has no per-model pricing table (no equivalent of Pi's
`models.ts` `calculateCost`/`model.cost`): (1) `Usage.cost` is present in the type
for shape-parity but always left `None` — providers populate only token counts;
(2) the OpenAI-Responses/Codex path leaves `cache_write` at 0 because that API
does not report cache-creation tokens (same as Pi). Usage is **per-response**
only; lifetime/context totals are derivable by summing `message.usage` across the
transcript (Pi likewise aggregates in its UI/session layer, not on the message).

Chaining semantics mirror Pi: `input` transforms chain and `handled`
short-circuits; `tool_call` blocking short-circuits remaining handlers;
`tool_result` overrides chain, each handler seeing prior modifications.
Extensions run in load order; handlers within an extension run in
registration order.

### Tools

Extensions register plain `AgentTool` values (name, description, raw
JSON-schema `input_schema`, async executor) — the same hand-written-schema
convention as built-ins (ADR 0002). First registration wins per name; an
extension tool with a built-in's name replaces the built-in (Pi's override
rule). Registered tools appear in the system prompt tool list, the TUI
sidebar, and `/session` counts like built-ins. Tool-attached
`prompt_snippet`/`prompt_guidelines` flow into the system prompt as for
built-ins, and `add_prompt_guideline` contributes standalone guideline
lines through `BuildSystemPromptOptions.extra_guidelines` (rebuilt on
`/reload` when they change).

### Commands

`register_command` wraps the handler into a `SlashCommand` registered on a
per-session `CommandRegistry` built by calling
`create_default_command_registry()` and layering extension commands on top
(the registry has no clone; rebuilding is the mechanism). Built-in names
cannot be overridden — a duplicate registration is caught and recorded as a
diagnostic, keeping
`tests/test_commands.py::test_registered_commands_are_pi_aligned` intact.

**Ruling:** extension command handlers are sync-only in v1. The whole
command path (`CommandRegistry.execute` → `CodingSession.handle_command` →
the TUI's submit handler) is synchronous; making it async ripples through
the TUI. Handlers receive `(args: str, context: ExtensionCommandContext)`
and may return `None` or a `str` message, which flows through the normal
`CommandResult.message` path. Long-running command work should
`send_user_message` or spawn a task. Extension commands surface in TUI
autocomplete automatically because autocomplete reads
`CommandRegistry.list_commands()`.

## Hook wiring (how interception works without touching tau_agent)

- **Observation** — the runtime subscribes one listener via
  `AgentHarness.subscribe` and fans events out to extension handlers.
  Dispatch is skipped per event type when no handler is registered.
- **`tool_call` / `tool_result`** — every tool handed to
  `AgentHarnessConfig.tools` (built-in and extension alike) is wrapped: the
  wrapper executor runs `tool_call` hooks (which may block or replace
  `arguments`), calls the inner executor, then runs `tool_result` hooks over
  the `AgentToolResult`. Blocking returns an `ok=False` result carrying the
  block reason to the model. The loop's dispatch chokepoint
  (`loop.py:_execute_tool_calls`) stays untouched.
- **`input`** — `CodingSession.prompt` runs `input` hooks on the raw prompt
  text *before* skill/template expansion (matching Pi's
  command-check → input-event → expansion order; slash commands were already
  handled by `handle_command` before `prompt` is reached). `handled`
  consumes the input: the prompt generator returns without yielding run
  events, and the optional `message` is delivered through the UI bridge
  notification channel.
- **`send_user_message`** — maps to `harness.steer` / `harness.follow_up`
  when a run is active. When idle, the runtime invokes a
  `turn_requested(content)` callback carrying the message text (queuing a
  follow-up and calling `continue_()` would hit the provider with a stale
  transcript first, because the loop drains queues only after a turn). The
  TUI implements the callback by submitting the content through its
  existing exclusive prompt worker — the same serialization used for user
  submissions, so an extension turn can never race a user-initiated run; if
  a run starts while delivery is in flight, the text is queued as a
  follow-up instead. Print mode and tests may leave the callback unset;
  messages then queue as follow-ups for the next run.
- **`append_entry`** — async; persists a `CustomEntry(namespace=..., data=...)`
  through the session's append path with proper parent linkage so the entry
  sits on the active root-to-leaf path (off-path custom entries are
  invisible to `SessionState` replay after resume).
- **`notify`** — routed to a `UiBridge` protocol owned by `CodingSession`;
  the TUI installs a Textual implementation, print mode gets a stderr
  fallback, tests install a recorder.

## Session integration and runtime lifecycle

`CodingSessionConfig` gains:

- `extension_paths: tuple[Path, ...] = ()` — explicit extension files/dirs
- `extensions_enabled: bool = True` — directory discovery on/off
- `project_extensions_enabled: bool = False` — see Security
- `extension_runtime: ExtensionRuntime | None = None` — internal handoff for
  session replacement (resume/new/branch)

`CodingSession.load`:

1. creates the runtime and discovers/loads extensions (via `loader.py`) —
   unless the config carries an existing runtime, in which case discovery
   and `setup` are **not** re-run;
2. merges extension tools with `create_coding_tools()` (extension override
   by name), wraps all tools with the hook wrapper;
3. builds the per-session command registry (defaults + extension commands);
4. builds the harness, binds the runtime (context + actions become live),
   subscribes the fan-out listener.

The runtime is **long-lived**: `resume`, `new_session`, and branch flows
construct their replacement session with `extension_runtime=` the current
runtime, then re-bind it to the new session/harness (old harness
subscription dropped, new one added) and emit
`session_shutdown`/`session_start` with the appropriate reason. Extension
`setup` therefore runs once per process per extension, not once per session
swap. `aclose` emits `session_shutdown(reason="quit")`.

`CodingSession.reload` (sync, from `/reload`) re-runs discovery: it purges
`tau_extension_*` modules from `sys.modules`, re-imports, re-runs `setup`
on a fresh runtime registration set, rebuilds the wrapped tool list in
place (`harness.config.tools` is mutable by design), rebuilds the session
command registry, and re-subscribes the fan-out listener. The summary gains
an `extensions` category in `CodingReloadSummary`. Reload emits no
`session_shutdown`/`session_start` pair (it runs on the sync command path,
so async handlers could not be awaited); extension state is simply rebuilt,
and background work started before the reload is orphaned. The `"reload"`
lifecycle reason is reserved for when the command path becomes async.

Extension diagnostics (load-time and runtime handler failures) merge into
`resource_diagnostics`, so `/session` and `/reload` surface them with no
TUI changes.

## Security

Project extensions execute arbitrary Python at session startup — cloning a
hostile repo and running `tau` inside it must not be code execution. Pi
gates this behind a project-trust prompt; Tau does not have a trust store
yet. **Ruling:** project-directory extensions are **disabled by default**
in v1. `<cwd>/.tau/extensions/` loads only with the explicit
`--project-extensions` CLI flag. User extensions (`~/.tau/extensions/`) and
explicit `-x` paths load by default; `--no-extensions` turns directory
discovery off entirely. A per-project trust prompt is the immediate
follow-up that can flip the project default.

## Example extension: subagents

The subagents extension lives in its own repository
(`rian-dolphin/tau-subagents`, private) rather than in this repo. It ports
the core of `tintinweb/pi-subagents`:

- registers an `agent` tool (`prompt`, `description`, `subagent_type`,
  `run_in_background`) plus `get_subagent_result`;
- agent types come from `.tau/agents/*.md` / `~/.tau/agents/*.md` files
  with frontmatter (`description`, `tools`, `model`) — same shape as Pi's
  agent definitions (a new convention owned by the example, alongside the
  existing `.agents/` resource dirs);
- spawns subagents **in-process** by constructing a scoped `CodingSession`
  (in-memory storage, tool allow-list, own system prompt,
  `extensions_enabled=False` so subagents cannot recursively spawn) — the
  Python analog of Pi's `createAgentSession`, no CLI subprocess needed;
- foreground runs block and return the subagent's final assistant text
  (no inline progress in v1 — partial tool-result streaming is an explicit
  non-goal until `ToolExecutionUpdateEvent` emission lands in `tau_agent`);
  background runs return an id immediately and deliver completion through
  `send_user_message(deliver_as="follow_up")`, which also exercises the
  idle `turn_requested` path.

Smaller examples: `hello_tool.py` (minimal tool) and `permission_gate.py`
(`tool_call` blocking for dangerous bash commands).

## Verification

- `tests/test_extensions.py`: discovery order and precedence, synthetic
  module naming, package-style relative imports, broken-extension
  isolation, sync-only `setup` enforcement, tool registration/override,
  command registration/duplicate handling, event fan-out, `tool_call`
  block + argument mutation, `tool_result` transform, `input`
  transform/handled, send_user_message queueing and idle turn-request,
  append_entry persistence and on-path replay, reload including module
  purge and stale-listener replacement, runtime survival across
  resume/new.
- `tests/test_coding_session.py` additions for session wiring; TUI
  autocomplete pickup via existing autocomplete tests.
- Full gate: `uv run pytest && uv run ruff check . && uv run mypy`.
