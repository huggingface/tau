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
  custom TUI components/widgets (extension-authored Textual widgets),
  custom **entry** renderers (`registerEntryRenderer`/`appendEntry`-rendered,
  non-LLM-context cards), shortcut and flag registration, system-prompt
  replacement, `context`/`before_provider_request` rewriting, and a project
  trust store.
  These have reserved names and documented extension points but no
  implementation yet.

  **Implemented since:** custom **message** renderers
  (`register_message_renderer` + `send_custom_message`) — the subset of Pi's
  renderer surface that formats messages which *do* participate in LLM context
  (see "Custom message rendering" below) — and extension-authored **component
  widgets** (`context.ui.components`: slot widgets, a main-area view, and
  pre-dispatch key interceptors), adopted as the committed design via the
  component-seam experiment (see the superseding Ruling under "Custom message
  rendering" and `dev-notes/design/component-seam-experiment.md`). Custom
  entry renderers remain out of scope.

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
3. Paths passed explicitly (`tau --extension/-e PATH`, repeatable; a file or
   a directory)

Within a directory, one level deep, matching Pi:

- `*.py` files are extension modules
- a subdirectory containing `extension.py` is an extension (the analog of
  Pi's `index.ts` convention)
- a directory (subdirectory or explicit `-e` path) whose `pyproject.toml`
  declares `[tool.tau] extensions = ["src/pkg/extension.py", ...]` loads the
  declared entries instead — the analog of Pi's `package.json`
  `pi.extensions` manifest ("complex packages must use package.json
  manifest"), so src-layout repos need no root shim

Names starting with `_` or `.` are skipped. Symlinked files are followed.

**Ruling:** the manifest lives under `[tool.tau]` in `pyproject.toml` — the
Python-ecosystem home for tool config — rather than a bespoke manifest file.
It takes precedence over a sibling `extension.py` (Pi's order); each declared
entry loads as a package rooted at the entry's parent directory (siblings
stay relatively importable — the manifest's whole purpose is structured
layouts), named after that parent (or the file stem when the entry is not
`extension.py`). Deviation from Pi: a declared-but-missing entry emits an
`error` diagnostic instead of being silently skipped — a manifest is an
explicit claim, and Tau already surfaces discovery diagnostics. A manifest
that yields no usable entries falls back to the `extension.py` convention;
an unparseable `pyproject.toml` is a `warning` (scanned directories may
contain unrelated projects). Declared paths are not confined to the manifest
directory (Pi parity: `path.resolve(dir, extPath)`) — extensions execute
arbitrary code anyway, so path containment would be security theater.

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

**Ruling:** `session_start` is emitted by the **host**, not by
`CodingSession.load`. Pi starts the UI before initializing extensions
precisely so `session_start` handlers can use dialogs and notifications
(`interactive-mode.ts`: "Start the UI before initializing extensions…");
Tau's load originally emitted before any UI bridge existed, silently
dropping `notify` and cancelling dialogs from `session_start` handlers.
`load` now marks the event pending and hosts release it with
`session.emit_pending_session_start()` (idempotent) after
`set_ui_bridge(...)` — the TUI in `on_mount`, print mode right after
installing `StderrUiBridge`. The adopt-replacement paths (`new`/`resume`/
`branch` and `/reload`) still emit directly: they reuse the long-lived
runtime whose bridge is already attached. A host that never calls it gets
no `session_start` — host responsibility, same as Pi.

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
`system_prompt`, `is_running`, `has_ui`, and `transcript`. It is a live view
over the bound `CodingSession`; action methods raise `ExtensionError` if
called before binding (Pi's throwing-stubs-then-`bindCore` model).

`transcript -> tuple[AgentMessage, ...]` gives read access to the active-path
parent conversation (`CodingSession.messages`). It is the Tau analogue of the
only conversation surface Pi hands extensions — `ctx.sessionManager.getBranch()`
— and exists so a subagent extension can port pi-subagents'
`buildParentContext` (an `inherit_context` text prepend built from the parent
branch) without `src/tau_agent` importing `src/tau_coding`.

**Ruling:** `context.transcript` returns **deep copies** of the messages, not
the live objects. Pi leans on TypeScript `Readonly<...>` types on `getBranch()`
for compile-time read-only-ness; Python has no such guarantee and Tau's message
models are mutable pydantic instances, so an extension holding a live object
could silently corrupt the session transcript. Copying is the enforcement.
This is a deliberate deviation from Pi (which returns live references). Semantic
parity otherwise: Pi's branch keeps user/assistant/tool entries and renders
compaction summaries from an explicit `compaction` entry `summary`. Tau has no
separate summary entry in `messages` — compaction and branch summaries are
already folded into the transcript as `UserMessage`s (`Previous conversation
summary:\n...` / `<summary>...</summary>`), so an extension building a digest
sees them as user turns rather than a distinct `[Summary]` type. If exact
`[Summary]` parity is ever needed, expose branch *entries* instead; not done in
v1.

Event handlers may be sync or async; async handlers are awaited. Handlers
run on the session's event loop, so they must be fast — slow work belongs in
a spawned task. Every handler invocation is wrapped in try/except — a
raising handler is recorded as a runtime diagnostic and dispatch continues.
The one deliberate exception, matching Pi: a raising `tool_call` hook blocks
the tool (fail-safe).

### Events

> **Superseded protocol note (Pi 0.80.6 cutover):** the original event/tool/message
> descriptions below document the first Tau extension implementation. The current
> contract is the canonical protocol in `tau_agent.events`, `tau_agent.tools`, and
> `tau_coding.events`: handlers receive `(event, context)`, streamed provider events
> are nested under `message_update.assistant_message_event`, tool executors receive
> `(tool_call_id, arguments, signal, on_update)`, and custom messages use the
> dedicated `CustomMessage` role. The session-to-extension adapter additionally
> enriches `turn_start` with Pi's zero-based `turn_index` and millisecond
> `timestamp`, and `turn_end` with the matching `turn_index`; portable
> `tau_agent` events remain session-agnostic. See the published extension guide
> and `dev-notes/design/pi-event-migration-audit.md` for the final shape.

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
| `input` | `InputEvent(text, source="interactive" \| "extension", streaming_behavior="steer" \| "follow_up" \| None)` | `InputHookResult(action="continue" \| "transform" \| "handled", text=None, message=None)` |
| `tool_call` | `ToolCallHookEvent(tool_name, arguments)` | `ToolCallHookResult(block=False, reason=None, arguments=None)` |
| `tool_result` | `ToolResultHookEvent(tool_name, arguments, result)` | `ToolResultHookResult(content=None, ok=None, details=None)` |

**Ruling:** the `input` hook payload ports Pi's `InputEvent` metadata with two
omissions. Pi's `images` field is dropped (Tau has no image input yet) and
Pi's `"rpc"` source is dropped (Tau has no RPC mode), so `source` is just
`"interactive" | "extension"`. Both new fields carry backward-compatible
defaults (`source="interactive"`, `streaming_behavior=None`), so handlers that
read only `.text` keep working. `source="extension"` is set when an extension
starts an idle turn via `send_user_message`/`send_custom_message` — that path
reaches `run_input_hooks` through `session.prompt`; when the session is already
running, extension delivery routes through `queue_steering_message`/
`queue_follow_up_message`, which bypass the hook stage entirely (no `input`
fires). `streaming_behavior` **is** populated: user-typed mid-run submissions
in the TUI go through `session.prompt(streaming_behavior=…)`, and `prompt`
runs `input` hooks before the mid-run steer/follow-up branch, so the hook sees
`"steer"`/`"follow_up"` on the streaming path and `None` on the idle path —
matching Pi's "undefined when idle". (The `queue_*_message` seams remain
hook-free, matching Pi, since those are host/extension-injected messages, not
user input.)

**Ruling:** the `tool_call`/`tool_result` hook payloads carry no
`tool_call_id`. The hooks are implemented by wrapping tool executors, and
the executor signature (`tau_agent/tools.py`) does not receive the call id —
the loop stamps it after execution. Extensions that need id correlation use
the observation events (`tool_execution_start/end`), which carry the full
`ToolCall`/`AgentToolResult`.

**Ruling:** live tool-execution progress (Pi's `onUpdate`) is implemented, but
with a lighter payload than Pi. A tool reports progress through an opt-in
`on_update` callback (`ToolUpdateCallback`, `tau_agent/tools.py`) whose
signature is `(message: str, data: dict[str, JSONValue] | None = None) -> None`
— deliberately *not* Pi's `onUpdate(partialResult: AgentToolResult)`. The loop
turns each call into the already-defined `ToolExecutionUpdateEvent(tool_call_id,
message, data)`, which carries no `content`/`details`/`ok` echo of a partial
result. Rationale: Tau's update event exists to drive a progress line, not to
re-render a partial tool result; extensions that need the full result read the
terminal `tool_execution_end`. `on_update` is sync and fire-and-forget (matching
Pi); the loop bridges calls onto the async event stream via an unbounded queue
and a task/queue race in `_execute_tool`, preserving order and never dropping the
final result (even on tool error or a closed/cancelled stream).

**Ruling:** the `on_update` seam is *opt-in via signature inspection*, not a
changed executor signature. `AgentTool` detects at construction (once, in
`__post_init__` via `inspect.signature`) whether its executor declares an
`on_update` parameter, and `AgentTool.execute` forwards the callback only to
executors that do. This keeps every existing `(arguments, signal)` executor —
all built-in tools — untouched, rather than mechanically adding `on_update=None`
to each. The alternative (widen the `ToolExecutor` protocol) was rejected because
it would force the parameter on every executor and break structural typing for
the built-ins. The extension runtime's tool wrapper (`_wrap_tool`) always
declares `on_update` and forwards it; the *inner* tool's inspect-gate still drops
it for wrapped executors that do not accept it.

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

### UI dialogs

`context.ui` (Pi's `ctx.ui`) exposes host-provided interactive dialogs plus
`notify`:

```python
async def select(title: str, options: Sequence[str], *, timeout: float | None = None) -> str | None
async def confirm(title: str, message: str, *, timeout: float | None = None) -> bool
async def input(title: str, placeholder: str = "", *, timeout: float | None = None) -> str | None
def notify(message: str, level: Literal["info", "warning", "error"] = "info") -> None
```

The methods delegate to a host `UiBridge` (`api.py`): `NullUiBridge`
(headless/tests) and `StderrUiBridge` (print mode) return Pi's no-op defaults
(`None`/`False`/`None`); `_TuiExtensionUiBridge` (`tui/app.py`) drives three
`ModalScreen`s (`ExtensionSelectScreen`/`ExtensionConfirmScreen`/
`ExtensionInputScreen`). `api.notify` stays as a back-compat alias for
`context.ui.notify`.

**Ruling:** extension UI dialogs are host-provided (`select`/`confirm`/
`input`), not extension-authored widgets, so they are in-spirit despite the
"custom TUI components" non-goal. Deviations from Pi, all deliberate for v1:
(a) **no `AbortSignal`** — only `timeout` (in **seconds**, not Pi's
milliseconds); on timeout the dialog auto-dismisses and returns the no-op
default (no live countdown display — Pi shows one). (b) `input` returns the
entered text verbatim (empty string on empty submit), `None` only on
cancel/escape, matching Pi's `string | undefined`. (c) The TUI bridge awaits
each modal via `push_screen(screen, callback)` + an `asyncio.Future`, **not**
`push_screen_wait` — the latter requires a Textual *worker* context, which a
task spawned by a sync command handler is not; the future/callback pattern
(already used by the OAuth `_manual_code_input` flow) works from any coroutine
on the app's event loop.

**Ruling:** the sync-only command-handler Ruling (above) is **kept** for v1
even though dialogs are async. An extension `/command` that needs a dialog
does **not** await it directly (the handler is sync); instead it spawns a loop
task and returns immediately:

```python
def _handler(args, context):
    async def _menu():
        choice = await context.api.context.ui.select("Action", ["deploy", "cancel"])
        if choice is not None:
            context.api.send_user_message(f"run {choice}")
    asyncio.get_running_loop().create_task(_menu())
    return "opening menu..."
```

This is safe because `CodingSession.handle_command` is invoked from the TUI's
async submit path (`tui/app.py`), i.e. on the Textual event-loop thread, so
`asyncio.get_running_loop()` is available and the spawned task shares that
loop. Converting the command path to async (the faithful Pi port) is the clean
long-term option but is deferred — it ripples through `CommandRegistry.execute`
→ `handle_command` → the TUI submit handler.

## Custom message rendering

Extensions can format their injected messages instead of leaving them as raw
text. The tau-subagents extension uses this so a background agent's
`<task-notification>` renders as a compact status block rather than raw XML.

API (ports Pi's `registerMessageRenderer` + `sendMessage`):

```python
def setup(tau):
    tau.register_message_renderer("subagent-notification", render_notification)

# later, from a tool executor / event handler:
tau.send_custom_message(
    "<task-notification>...</task-notification>",
    custom_type="subagent-notification",
    details={"id": run_id, "status": "completed", ...},
)
```

A renderer is `Callable[[CustomMessageView, MessageRenderOptions], str]`:
`CustomMessageView(custom_type, content, details)` plus
`MessageRenderOptions(expanded)`. It returns a **Rich-markup string**
(e.g. `"[bold]✓ done[/bold]"`).

Data flow: `send_custom_message` → the message rides the normal user-message
pipeline as a `UserMessage` carrying `custom_type`/`details` metadata (it still
enters LLM context via `content`) → `MessageEndEvent(message=...)` → the TUI
adapter projects it to a `ChatItem(role="custom")` → the render path calls
`runtime.render_custom_message(...)`, which looks up the registered renderer,
builds the view/options, and returns markup (or `None` to fall back to raw
`content`). The resolver is installed into every render path: the live TUI
(`state.custom_renderer`, consumed by `TranscriptView._redraw` /
`TranscriptMessageWidget` / `render_chat_item`), session **resume**
(`TuiState.load_messages` projects `custom_type` off replayed `UserMessage`s),
and the **print-mode** transcript (`TranscriptRenderer`, wired in `cli.py`).

**Ruling:** custom-message renderers return **markup strings, not Textual
widgets** (deviation from Pi's `Component` return). This keeps extensions
free of any TUI-toolkit import — an extension only ever produces a string,
and the host decides how to display it (Rich markup in the TUI, Rich `Text`
in print mode). Malformed markup never crashes the frontend: `Text.from_markup`
is called under a guard that falls back to literal text.

**Ruling (reaffirmed 2026-07, subagents boundary audit):** the
strings-not-widgets choice was revisited when the subagent UX (agents strip,
in-place agent views) had to be built host-side against generic data seams
rather than shipped by the extension, as pi-subagents does. Considered and
rejected: a pi-style component seam (`ctx.ui.custom` / `setHeader` /
`setFooter` / `setEditorComponent`). The decisive asymmetry is that **Pi
ships its own TUI framework** — `@earendil-works/pi-tui` is a sibling
package in the pi monorepo — so when Pi hands extensions a `Component` it
exposes an interface it owns and can evolve in lockstep with its host. Tau
renders with **Textual, a third-party toolkit**: a widget seam would promote
Textual's API to Tau's public extension contract, making every Textual
upgrade a potential ecosystem break and forever precluding a frontend swap.
Strings additionally work in every frontend (TUI, print mode, future hosts)
and cannot crash or wedge the render loop; extension needs so far (dialogs,
message renderers, tool-call lines, transcript sources, themes) have all
been expressible as data seams. Note the practical consequence: the "removes
code from core" appeal is largely illusory — features would move out, but
core would grow a widget-hosting layer (mounting, lifecycle, layout slots,
focus/input routing, error isolation) with a much larger public contract.
**Reopen triggers:** (a) Tau takes ownership of its UI layer (its own
toolkit, or a hard abstraction over Textual); (b) a real third-party
extension need that cannot be expressed as a data seam; (c) upstream tau
explicitly wants component extensions. Preferred middle ground before raw
widgets: a **declarative UI layer** (extensions emit structured component
descriptions — block-kit style — that the host renders), which widens
expressiveness while preserving toolkit independence; design it from Pi's
`ctx.ui` surface first, per the porting rule above.

**Ruling (superseded 2026-07-09 — component seam adopted):** reopen trigger
(b) fired in practice. Keeping the subagent UX host-side against data seams
forced agent vocabulary and agent UI *into* core (the agents strip and
in-place viewer lived in `tui/app.py`), which defeats the point of an
extension system. The `component-seam-experiment` branch then implemented the
raw-widget path end-to-end — the tau-subagents extension owns its entire UI
through `ComponentBridge` — and measured the cost the Ruling above predicted
(`component-seam-experiment.md` §8: core src grew ~+250 lines and the public
contract got heavier; the prediction held and is accepted). Decision: the
component seam is the **committed design**, not an experiment. The component
type is Textual's `Widget`; the coupling is owned deliberately — extensions
build against the Textual version tau pins, and a Textual major bump is a
coordinated break for core and extensions together. Strings remain the
preferred form wherever they suffice (message renderers, tool-call/result
renderers, string-list slot widgets — all frontend-portable and print-safe);
widgets are for extensions that need live, interactive UI. The declarative
middle ground stays available as a future *addition*, not a replacement.

**Ruling:** `custom_type`/`details` ride on **`UserMessage` metadata** rather
than a separate `custom` message role (deviation from Pi's `CustomMessageEntry`
/ `role:"custom"`). Pi needs a distinct role because its messages are
content-block arrays converted to a user message for the LLM anyway
(`convertToLlm`); Tau's transcript is plain-string, so metadata on `UserMessage`
achieves identical LLM-context semantics with a far smaller wire/replay
footprint — no new entry in the `AgentMessage` union, no discriminator change.

Wire behavior (the actual compatibility contract):

- **Reading old files:** both fields default to `None`, so sessions persisted
  before this change load under the models' `extra="forbid"` config.
- **Writing new files:** the fields are **omitted from serialization when
  unset** (a targeted `model_serializer` on `UserMessage`, not a global
  `exclude_none`, so no other field's wire semantics change). A session that
  never uses custom messages is therefore **byte-identical** to the
  pre-metadata format and remains readable by older binaries.
- **Downgrade with custom messages present is unsupported:** once a session
  contains a message sent via `send_custom_message`, its JSONL lines carry
  `custom_type`/`details` keys, and an older binary's `extra="forbid"`
  `UserMessage` will reject that file. This is the one real
  persistence-format extension this feature makes.
- Sessions written with the fields round-trip through `MessageEntry` and
  render correctly after resume.

**Ruling:** the resolver **never raises** into a render path. A missing
renderer, a renderer that throws, or one that returns a non-string all yield
`None` (recorded as a runtime diagnostic for the last two — deduplicated to
one diagnostic per `custom_type`, since render paths re-run on every redraw
and a persistently-broken renderer would otherwise grow diagnostics without
bound), and the frontend renders the raw `content`.
First-registration-per-`custom_type` wins, matching Tau's other extension
registries; the registry (and the failure-dedupe set) is cleared on `/reload`.

**Ruling:** Pi's `sendMessage` `display` (hide from TUI while keeping in
context) and `triggerTurn` are **partly** ported, with weaker durability on
the no-turn path: with `trigger_turn=False` (or when no turn callback is
installed), Tau queues the message **in-memory** on the harness follow-up
queue — it is not yet visible in the transcript and is **silently lost if the
session exits before the next run**. Pi, by contrast, persists the message to
the session file and emits `message_start`/`message_end` immediately even
without triggering a turn (`agent-session.ts:1357-1370`). Extensions that need
a durable no-turn record should use `append_entry` alongside, or accept the
default turn-triggering delivery. `display=false` is **not** implemented —
custom messages are always shown (the tau-subagents extension only ever sends
`display:true`). Pi's parallel `registerEntryRenderer`/`appendEntry`
(non-LLM-context cards) stays out of scope.

**Ruling:** delivery **defaults deviate from Pi**, deliberately matching Tau's
existing `send_user_message` semantics instead: Pi's `sendMessage` defaults to
`triggerTurn: false` and `deliverAs: "steer"` while streaming; Tau's
`send_custom_message` defaults to `trigger_turn=True` and
`deliver_as="follow_up"`. The tau-subagents notification path wants exactly
follow-up + turn-trigger (it passes `deliverAs: "followUp", triggerTurn: true`
explicitly in Pi too), and keeping one default across both send methods is
less surprising for extension authors. Callers can pass
`deliver_as="steer"`/`trigger_turn=False` for Pi-shaped behavior.

Note: two surfaces intentionally show a custom message's raw `content` rather
than its rendered markup: the session **HTML export**
(`session_export.py` renders messages from the persisted transcript without
the extension runtime) and the **queued-message preview**
(`harness.py` `queue_update_event` reports queued content strings). Both are
raw-text views by design; only live transcripts (TUI + print mode) render.

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
- **`send_user_message` / `send_custom_message`** — both funnel through one
  `_deliver_message` path. When a run is active, they map to
  `queue_steering_message` / `queue_follow_up_message` (which build a
  `UserMessage` carrying any `custom_type`/`details`). When idle, the runtime
  invokes a `turn_requested(content, custom_type, details)` callback (queuing a
  follow-up and calling `continue_()` would hit the provider with a stale
  transcript first, because the loop drains queues only after a turn). The
  TUI implements the callback by submitting through its existing exclusive
  prompt worker — the same serialization used for user submissions, so an
  extension turn can never race a user-initiated run; if a run starts while
  delivery is in flight, the message is queued as a follow-up instead. The
  custom metadata threads all the way to `CodingSession.prompt` →
  `harness.prompt`, so a custom message that starts an idle turn still renders
  and persists with its `custom_type`. Print mode and tests may leave the
  callback unset; messages then queue as follow-ups for the next run.
  `send_custom_message`'s `trigger_turn=False` forces the follow-up-queue path
  even when idle.
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

`CodingSession.reload` is async. The synchronous command registry returns a
`reload_requested` action, and each frontend awaits the session operation.
Reload first awaits `session_shutdown(reason="reload")` while the outgoing
extension generation is still active, clears its host UI, invalidates the old
generation, purges `tau_extension_*` modules from `sys.modules`, re-imports,
and re-runs `setup` on a fresh registration set. It then rebuilds the wrapped
tool list in place (`harness.config.tools` is mutable by design), rebuilds the
session command registry, re-subscribes the fan-out listener, and awaits
`session_start(reason="reload")` on the new generation. This lets shutdown
handlers clean up through their live API and start handlers remount UI before
reload reports completion. The summary includes an `extensions` category in
`CodingReloadSummary`.

**Ruling:** reload invalidates prior extension instances (Pi's
`assertActive`/`invalidate` parity). Each load generation shares an
`ExtensionGeneration` token; `reset_for_reload` clears outgoing UI first, then marks the generation stale
and mints a fresh one, and every `ExtensionAPI` method/property and every
`ExtensionContext`/`ExtensionUi` read — trivial reads like `has_ui`
included, matching Pi's assert-on-everything — checks the token first and
raises `ExtensionError`. An orphaned background task holding a pre-reload
`tau` therefore fails loudly (and, when it fails inside a handler, is
recorded as a normal runtime diagnostic) instead of silently acting against
the new registration set. Rebinding does **not** invalidate — a deliberate
deviation from Pi, which replaces the runtime per session swap and hands
fresh contexts via `withSession`: Tau's runtime is long-lived across
resume/new/branch, the same extension instances continue by design, and
their context views simply reflect the newly bound session.

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
explicit `-e` paths load by default; `--no-extensions` turns directory
discovery off entirely. A per-project trust prompt is the immediate
follow-up that can flip the project default.

## Example extension: subagents

The subagents extension lives in its own repository
(`rian-dolphin/tau-subagents`) rather than in this repo. It ports
the core of `tintinweb/pi-subagents` and doubles as the reference consumer
of the newer API seams (manifest, dialogs, renderers, `on_update`,
`context.transcript`), feature-detecting each so it loads on older builds:

- a `src/`-layout package whose `pyproject.toml` declares
  `[tool.tau] extensions = ["src/tau_subagents/extension.py"]` — the
  manifest's reference user;
- registers an `agent` tool (`prompt`, `description`, `subagent_type`,
  `run_in_background`, plus `model`/`thinking`/`max_turns`/`resume`/
  `isolation`/`inherit_context`/`schedule`), `get_subagent_result`,
  `steer_subagent`, and an `/agents` command that opens a `context.ui`
  dialog menu when a UI is attached;
- agent types come from `.tau/agents/*.md` / `~/.tau/agents/*.md` files
  with frontmatter (`description`, `tools`, `model`, `thinking`,
  `max_turns`, `prompt_mode`, `memory`, `isolation`, …) — same shape as
  Pi's agent definitions (a new convention owned by the example, alongside
  the existing `.agents/` resource dirs);
- spawns subagents **in-process** by constructing a scoped `CodingSession`
  (in-memory storage, tool allow-list, own system prompt,
  `extensions_enabled=False` so subagents cannot recursively spawn) — the
  Python analog of Pi's `createAgentSession`, no CLI subprocess needed;
- foreground runs block, stream child activity through the `on_update`
  seam, and return the subagent's final assistant text; background runs
  return an id immediately and deliver completion through
  `send_custom_message(custom_type="subagent-notification",
  deliver_as="follow_up")` — rendered by its registered message renderer,
  falling back to `send_user_message` on older builds — which also
  exercises the idle `turn_requested` path.

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
