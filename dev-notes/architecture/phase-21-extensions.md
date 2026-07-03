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
  and a project trust store. These have reserved names and documented
  extension points but no implementation yet.

## Discovery and loading

Extension locations, in load order (first-registered wins on name conflicts,
matching Pi's project-first precedence):

1. `<cwd>/.tau/extensions/` — project extensions
2. `~/.tau/extensions/` — user extensions
3. Paths passed explicitly (`tau --extension/-x PATH`, repeatable; a file or
   a directory)

Within a directory, one level deep, matching Pi:

- `*.py` files are extension modules
- a subdirectory containing `extension.py` is an extension (the analog of
  Pi's `index.ts` convention); its directory is added to `sys.path` for the
  duration of the import so it can ship helper modules

Names starting with `_` are skipped. Symlinked files are followed.

Each module is imported with `importlib` under a unique synthetic module name
(`tau_extension_<slug>_<n>`), so project and user extensions with the same
file name cannot collide in `sys.modules`. The module must define a
callable:

```python
def setup(tau: ExtensionAPI) -> None: ...   # async def also supported
```

`setup` is invoked once at load. Import errors, a missing `setup`, and
exceptions raised by `setup` are captured as `ResourceDiagnostic`
(`kind="extension"`, `severity="error"`) and the extension is skipped.

`tau --no-extensions` disables discovery entirely (explicit `--extension`
paths still load, matching Pi's CLI-survives semantics).

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
`CustomEntry`).

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

    # actions (valid once the session is bound; raise before that)
    def send_user_message(
        self, content: str, *, deliver_as: Literal["steer", "follow_up"] = "follow_up",
    ) -> None: ...
    def append_entry(self, namespace: str, data: dict[str, JSONValue]) -> None: ...
    def notify(self, message: str, level: Literal["info", "warning", "error"] = "info") -> None: ...

    # context (read-only)
    @property
    def context(self) -> ExtensionContext: ...
```

`ExtensionContext` exposes `cwd`, `model`, `provider_name`, `session_id`,
`system_prompt`, `is_running`, and `has_ui`. It is a thin view over the
bound `CodingSession`; action methods raise `ExtensionRuntimeError` if
called before binding (Pi's throwing-stubs-then-`bindCore` model).

Handlers may be sync or async; async handlers are awaited. Every handler
invocation is wrapped in try/except — a raising handler is recorded as a
runtime diagnostic and dispatch continues. The one deliberate exception,
matching Pi: a raising `tool_call` hook blocks the tool (fail-safe).

### Events

Observation events reuse the `AgentEvent` `type` literals directly:
`agent_start`, `agent_end`, `turn_start`, `turn_end`, `message_start`,
`message_delta`, `thinking_delta`, `message_end`, `tool_execution_start`,
`tool_execution_update`, `tool_execution_end`, `error`, `retry`,
`queue_update`. These are delivered from `AgentHarness.subscribe` and cannot
mutate anything. `api.on("agent_event", fn)` is the wildcard.

Lifecycle events, dispatched by the runtime:

| Event | Payload | Result |
|---|---|---|
| `session_start` | `SessionStartEvent(reason: "startup" \| "reload" \| "new" \| "resume" \| "branch")` | — |
| `session_shutdown` | `SessionShutdownEvent(reason)` | — |
| `input` | `InputEvent(text)` | `InputHookResult(action="continue" \| "transform" \| "handled", text=None, message=None)` |
| `tool_call` | `ToolCallHookEvent(tool_name, tool_call_id, arguments)` | `ToolCallHookResult(block=False, reason=None, arguments=None)` |
| `tool_result` | `ToolResultHookEvent(tool_name, tool_call_id, arguments, result)` | `ToolResultHookResult(content=None, ok=None, details=None)` |

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
sidebar, and `/session` counts like built-ins.

### Commands

`register_command` wraps the handler into a `SlashCommand` registered on a
per-session `CommandRegistry` that is created by cloning the default
registry and layering extension commands on top. Built-in names cannot be
overridden (duplicate registration is a diagnostic, not an override) — the
default registry stays byte-identical to Pi's built-in command set, which
`tests/test_commands.py::test_registered_commands_are_pi_aligned` locks
down. Extension command handlers receive `(args: str, context:
ExtensionCommandContext)` and may return `None` or a `str` message; the
returned message flows through the normal `CommandResult.message` path.
Extension commands surface in TUI autocomplete automatically because the
autocomplete reads `CommandRegistry.list_commands()`.

## Hook wiring (how interception works without touching tau_agent)

- **Observation** — the runtime subscribes one listener via
  `AgentHarness.subscribe` and fans events out to extension handlers.
- **`tool_call` / `tool_result`** — every tool handed to
  `AgentHarnessConfig.tools` (built-in and extension alike) is wrapped: the
  wrapper executor runs `tool_call` hooks (which may block or replace
  `arguments`), calls the inner executor, then runs `tool_result` hooks over
  the `AgentToolResult`. Blocking returns an `ok=False` result carrying the
  block reason to the model. The loop's dispatch chokepoint
  (`loop.py:_execute_tool_calls`) stays untouched.
- **`input`** — `CodingSession.prompt` runs `input` hooks on the expanded
  prompt text before handing it to the harness. `handled` yields a
  message-only outcome without an agent run.
- **`send_user_message`** — maps to `harness.steer` / `harness.follow_up`
  when a run is active. When idle, the message is queued as a follow-up and
  the runtime invokes a `turn_requested` callback that the TUI (or print
  mode) registers to start `continue_()`; without a registered callback the
  message waits for the next run.
- **`append_entry`** — persists a `CustomEntry(namespace=..., data=...)`
  through the session's storage seam, the hook reserved for extensions in
  `tau_agent/session/entries.py`.
- **`notify`** — routed to a `UiBridge` protocol owned by `CodingSession`;
  the TUI installs a Textual implementation, print mode installs a stderr
  fallback, tests install a recorder.

## Session integration

`CodingSessionConfig` gains `extension_paths: tuple[Path, ...] = ()` and
`extensions_enabled: bool = True`. `CodingSession.load`:

1. discovers/loads extensions (via `loader.py`) after resources, before
   tool/registry/harness assembly;
2. merges extension tools with `create_coding_tools()` (extension override
   by name), wraps all tools with the hook wrapper;
3. builds the per-session command registry (defaults + extension commands);
4. builds the harness, binds the runtime (context + actions become live),
   subscribes the fan-out listener, emits `session_start`.

`aclose` emits `session_shutdown`. `reload` re-runs discovery with fresh
module names, rebuilds wrapped tools and the session registry in place, and
reports an `extensions` category in `CodingReloadSummary`. `resume`,
`new_session`, and branch flows emit `session_shutdown`/`session_start`
with the appropriate reason.

Extension diagnostics merge into `resource_diagnostics`, so `/session`
and `/reload` surface them with no TUI changes.

## Security note

Project extensions execute arbitrary Python at session startup. Pi gates
this behind a project-trust prompt; Tau does not have a trust store yet.
This phase documents the risk (README + docs/extensions.md), keeps
`--no-extensions` as the off switch, and leaves a trust prompt as the
immediate follow-up before any release that enables project extensions by
default.

## Example extension: subagents

`examples/extensions/subagents.py` ports the core of
`tintinweb/pi-subagents`:

- registers an `agent` tool (`prompt`, `description`, `subagent_type`,
  `run_in_background`) plus `get_subagent_result`;
- agent types come from `.tau/agents/*.md` / `~/.tau/agents/*.md` files
  with frontmatter (`description`, `tools`, `model`) — same shape as Pi's
  agent definitions;
- spawns subagents **in-process** by constructing a scoped `CodingSession`
  (in-memory storage, tool allow-list, own system prompt) — the Python
  analog of Pi's `createAgentSession`, no CLI subprocess needed;
- foreground runs block and return the subagent's final assistant text;
  background runs return an id immediately and deliver completion through
  `send_user_message(deliver_as="follow_up")`.

Smaller examples: `hello_tool.py` (minimal tool) and `permission_gate.py`
(`tool_call` blocking for dangerous bash commands).

## Verification

- `tests/test_extensions.py`: discovery order and precedence, synthetic
  module naming, broken-extension isolation, setup diagnostics, tool
  registration/override, command registration/duplicate handling, event
  fan-out, `tool_call` block + argument mutation, `tool_result` transform,
  `input` transform/handled, send_user_message queueing, append_entry
  persistence, reload.
- `tests/test_coding_session.py` additions for session wiring; TUI
  autocomplete pickup via existing autocomplete tests.
- Full gate: `uv run pytest && uv run ruff check . && uv run mypy`.
