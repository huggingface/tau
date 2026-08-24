---
title: Extensions
description: Extend Tau with plain Python — custom tools, slash commands, hooks, dialogs, and message rendering.
---

Extensions are Python modules that customize a Tau session: they add tools,
slash commands, and process-local provider definitions; observe the agent event
stream; and intercept tool calls, tool results, and user input. The design
follows Pi's extension system, adapted to Python.

## Quick start

Create `~/.tau/extensions/greet.py`:

```python
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult


async def run_greet(tool_call_id, arguments, signal=None, on_update=None):
    return AgentToolResult(
        content=[TextContent(text=f"Hello, {arguments.get('who', 'world')}!")],
    )


def setup(tau):
    tau.register_tool(
        AgentTool(
            name="greet",
            label="Greet",
            description="Greet someone.",
            parameters={
                "type": "object",
                "properties": {"who": {"type": "string"}},
            },
            execute_fn=run_greet,
            prompt_snippet="Greet someone by name.",
        )
    )
```

Start `tau` and the model can call `greet`. Every extension is a module
defining `setup(tau)`, which runs once at startup with the extension API.

## Install an extension

Install a trusted extension from Git with the same command shape as Pi:

```bash
tau install git:github.com/owner/repository
tau install git:github.com/owner/repository@v1.2.0
tau install https://github.com/owner/repository.git
```

Tau clones the repository into `~/.tau/extensions/<repository>` and loads it on
the next Tau startup. A pinned ref may be a tag, branch, or commit. Replacing an
existing install requires an explicit opt-in:

```bash
tau install git:github.com/owner/repository@v1.3.0 --force
```

Local files and package directories work too:

```bash
tau install ./my_extension.py
tau install ./my-extension
```

A local file is copied into `~/.tau/extensions/`. A directory is copied and must
contain `extension.py` or declare `[tool.tau].extensions` in `pyproject.toml`, so
it remains discoverable without `-e`. Local virtual environments, VCS metadata,
and Python cache directories are not copied.

The installer validates discovery metadata without importing the extension.
It does not install Python dependencies, maintain a package registry, or provide
remove/update commands yet. Install dependencies into Tau's Python environment
separately when an extension requires them. Extensions execute arbitrary Python
with your user permissions, so review the source before installation.

## Where extensions live

| Location | Loaded |
|---|---|
| `~/.tau/extensions/` | by default |
| `<project>/.tau/extensions/` | only after project approval **and** `--project-extensions` |
| any file or directory | with `tau -e PATH` (repeatable) |

### Trusted built-in extensions

Tau may bundle product capabilities as `BuiltInExtension` declarations. A
built-in is not a special provider or command branch: its synchronous
`setup(tau)` receives the normal extension API and registers tools, commands,
process-local providers, hooks, or later extension capabilities through the
same runtime.

Built-ins load once per staged runtime, before user, explicit, and trusted
project sources. They still load with `--no-extensions`, because that flag turns
off filesystem discovery rather than capabilities shipped in Tau itself. Their
code is trusted as installed package code and never counts as ambient project
input, so a built-in alone cannot trigger project trust.

Declarations are hidden by default. Hidden means omitted from ordinary
extension-name counts, not inactive: detailed runtime metadata retains the
`built-in` source, stable `built-in:<name>` source ID, and hidden flag. Setup
exceptions are isolated diagnostics and all partial registrations from that
source are removed. Reload, resume, new-session, and cwd replacement use fresh
generations; retiring an old generation invalidates captured APIs, removes its
registrations, and cancels generation-owned provider refresh work. Generic core
loading never checks a built-in capability's name.

Within a directory, `*.py` files are extensions, and a subdirectory
containing `extension.py` is a package-style extension — its sibling
modules are imported with relative imports (`from . import helper`).
Names starting with `_` are skipped.

Larger extensions that keep their code in a package (e.g. a `src/`
layout) can declare their entry files in `pyproject.toml` instead of
placing `extension.py` at the directory root:

```toml
[tool.tau]
extensions = ["src/my_ext/extension.py"]
```

The manifest takes precedence over an `extension.py` in the same
directory; each declared file loads as a package rooted at its parent
directory, so sibling modules stay importable with relative imports. The
extension is named after the entry's parent directory (or after the file
itself when it isn't named `extension.py`).

One caveat: `tau -e` on an entry **file** loads it standalone — no
package, so relative imports fail. Once an extension has sibling
modules, always pass a directory: the package directory itself, or the
repo root when a manifest declares the entry.

Before a project decision, built-in, user-global, and explicit `-e` extensions
may handle the `project_trust` event. The event contains canonical cwd, mode,
UI availability, and bounded category counts—never protected contents. Return
`ExtensionTrustResult("approve" | "decline" | "defer", remember=...)`; the
first decisive result wins, errors safely defer, and remembered results save
only the exact cwd before project loading. Project extensions cannot approve
themselves.

After built-ins, filesystem extensions keep their existing precedence; on name
conflicts (extension names, tool names, command names) the first registration
wins. `--no-extensions` disables directory discovery (explicit `-e` paths and
trusted built-ins still load).
`/reload` awaits `session_shutdown(reason="reload")` on the outgoing
extension generation, clears its UI, re-imports every extension and re-runs
`setup`, then awaits `session_start(reason="reload")` on the new generation.
Use those lifecycle hooks to stop and restart background work and to remount UI.

> **Security.** Extensions execute arbitrary Python inside your session.
> Project extensions are therefore off by default. Trust approval and
> `--project-extensions` are both required. Project trust is not a process,
> filesystem, network, credential, tool, model, or prompt-injection sandbox.

## The extension API

```python
def setup(tau):
    # registration
    tau.register_tool(agent_tool)            # tau_agent.tools.AgentTool
    tau.register_provider(dynamic_provider)  # process-local
    tau.register_command("name", handler, description="...")
    tau.add_prompt_guideline("Never commit directly to main")
    tau.on("event_name", handler)            # or @tau.on("event_name")

    # message rendering (register in setup; send once running)
    tau.register_message_renderer("my-ext:status", render_status)

    # actions — valid once the session is bound, not during setup
    tau.send_user_message("text", deliver_as="follow_up")  # or "steer"
    tau.send_custom_message("text", custom_type="my-ext:status", details={...})
    await tau.append_entry("my-ext:records", {"key": "value"})
    tau.notify("message", "info")            # "info" | "warning" | "error"
    tau.set_inference_provider("deepinfra")   # Hugging Face route; None resets

    # read-only context
    tau.context.cwd, tau.context.model, tau.context.provider_name
    tau.context.inference_provider             # current Hugging Face route, or None
    tau.context.inference_provider_mode        # "automatic" or "fixed"
    tau.context.session_id, tau.context.session_name
    tau.context.thinking_level, tau.context.system_prompt
    tau.context.is_running, tau.context.has_ui
    tau.context.transcript   # parent conversation, deep-copied AgentMessages

    # host-framed sidebar sections (see "Sidebar sections" below)
    sidebar = getattr(tau.context.ui, "sidebar", None)
    if sidebar is not None and sidebar.supported:
        sidebar.set_section("status", title="Status", content=["[green]ready[/green]"])

    # interactive UI dialogs (async; see "UI dialogs" below)
    await tau.context.ui.select("Title", ["a", "b"])   # -> str | None
    await tau.context.ui.confirm("Title", "message")   # -> bool
    await tau.context.ui.input("Title", "placeholder") # -> str | None
    tau.context.ui.notify("message", "info")           # same as tau.notify
```

`set_inference_provider(route)` lets provider-specific extensions select a
Hugging Face inference-provider route for the active session. A provider name
sets `context.inference_provider_mode` to `"fixed"`, so Tau honors the explicit
selection and does not automatically fail over. Passing `None` selects
`"automatic"` mode: the next successful response becomes a sticky route that
Tau may replace after an exhausted retryable pre-output failure. Other providers
reject the operation. The current resolved route is available as
`context.inference_provider`.

`setup` must be a plain `def` (not `async def`). Event handlers may be sync
or async and always receive `(event, context)`; the context is freshly created
for each dispatch. Action methods raise `ExtensionError` if called before the session
is bound — register handlers in `setup` and act on events instead.

### Local-backend registrations

An extension can pair a provider layer with a provider-neutral local backend:

```python
def setup(tau):
    tau.register_provider(provider)
    tau.register_local_backend(backend)
```

A backend declares structured text, secret, and choice fields plus asynchronous
configuration, refresh, status, and optional doctor/reset/model-management
operations. The host renders the values and owns confirmation, cancellation, and
idle checks; backend code never receives Textual widgets. Configuration is one
transaction, so validation or safe-state failure does not replace the prior
configuration. Secrets stay out of representations and host diagnostics.

The backend and provider must be registered by the same source and generation.
If another source shadows the provider, the backend can remain inspectable but
cannot use, reset, or manage models through the shadowed layer. Retired or
reloaded generations cancel their backend work and ignore late results. See the
[local backends guide]({{< relref "./local-inference.md" >}}).

### Dynamic providers

`register_provider` installs a complete `DynamicProvider` layer owned by the
calling extension source and current runtime generation. A provider may start
dormant with no models, and supplies exactly one runtime mechanism: an
`OpenAICompatibleTransport` descriptor or a custom runtime factory. Use
`ProviderModel` values for known metadata; leave unknown fields as `None`.

Authentication is explicit: `RequiredApiKey`, `OptionalApiKey`, or `NoAuth`.
Stored credentials win over the configured environment variable. Optional or
absent keys omit `Authorization`; Tau never synthesizes a local key. Static
transport/model headers cannot provide `Authorization`; custom schemes must be
resolved by an auth strategy at runtime. Resolved keys, headers, and arbitrary
auth provenance stay out of provider representations and diagnostics. Runtime
creation replaces custom auth exceptions with a categorical host error; Tau's exact
required-key strategy still reports its actionable missing-credential guidance.
Nested JSON compatibility metadata is deeply frozen while registered and copied to ordinary
JSON containers only when a runtime transport is created.

Discovery is snapshot-oriented. A `refresh_models` callback returns a complete
`ProviderModelSnapshot`; the registry validates and publishes it atomically.
Concurrent callers share work only when their layer and `allow_network` policy
match, and each caller keeps its own timeout. Opposite network policies never
alias. The last timeout and explicit cancellation leave the coalescing table before
returning, so an immediate retry invokes fresh discovery. Tau requests task
cancellation once, then waits up to 0.25 seconds from that request without
re-cancelling a callback's `finally` cleanup. Reload, session replacement, and final
close await this cooperative drain. Once reload/replacement publishes its new state,
caller cancellation is contained until outgoing cleanup finishes and the operation
returns the adopted result; it never reports cancellation as though publication
rolled back. Before publication, the replacement remains the explicit owner of its
candidate providers. Cancellation or failure during outgoing shutdown or incoming
start closes those candidates exactly once without closing the active provider;
success transfers ownership once. Final close uses one durable close task and
propagates cancellation only after the extension registry and every session-owned
runtime provider have each been closed once. A callback still running at the bound is reported as contained—not
drained—and a process-owned supervisor keeps its task and generation registry
reachable until it actually finishes; it cannot publish after source replacement or
retirement. Timeout, malformed output, and other failures retain the current
snapshot.

Dynamic definitions are runtime overlays—not durable configuration. Tau never
copies them into `catalog.toml`, `providers.json`, sessions, or generic extension
storage. Provider source ownership comes from the canonical entry path assigned by
the host, not the display name. The loader freezes all discovered source IDs before
importing any extension, so import/setup code cannot change ownership by retargeting
an entry or parent symlink. The stored ID is used for duplicate checks, every API
registration, and complete failed-setup cleanup. Separately loaded same-name
extensions therefore form independent provider layers; repeating the exact entry
source in one runtime is ignored with first-loaded precedence. Tools and commands
still use their first-registration-wins name registries. Removing a source reveals
the preceding complete layer, including the exact durable provider baseline. The contracts are
frontend-free and callbacks must not return Rich/Textual values.

Phase 6 validates these contracts with a permanent second fake backend and a
small test-only Ollama adapter. The trusted built-in `llama.cpp` provider uses
the same seams; no production Ollama backend is shipped. Provider discovery and
backend status may use different protocol endpoints, and `NoAuth` is a first-
class option. Its connection, cache, and troubleshooting behavior are covered
in the [local inference guide]({{< relref "./local-inference.md" >}}). Router
management and Hugging Face model mutations remain outside this phase.

### Tools

`register_tool` takes a plain `tau_agent.tools.AgentTool`: a name, label,
description, a hand-written JSON-schema `parameters` mapping, and an async
`execute_fn(tool_call_id, arguments, signal=None, on_update=None)`. Give the tool a
`prompt_snippet` to list it in the system prompt's "Available tools"
section, and `prompt_guidelines` for usage guidance tied to the tool.
Registering a tool with a built-in's name (`read`, `write`, `edit`,
`bash`) replaces the built-in.

A long-running tool can stream progress: an executor that additionally
uses `on_update` receives a callback accepting an `AgentToolResult`; each call becomes a
`tool_execution_update` event and drives the TUI's live progress line.
Executors without the parameter are unaffected.

By default the TUI shows an unrecognized tool call as `name {arguments}`
(truncated). Give the tool a `render_call` — `(arguments) -> str | None` —
to render a friendly one-line invocation instead (Pi's `renderCall`): for
example a subagent tool showing its `description` argument rather than the
raw JSON. Return `None` to fall back to the generic line. Renderer errors
are swallowed (diagnosed once per tool) and never crash the UI.

While a tool is executing, the TUI animates its row: a braille spinner
stands in for the line's leading marker (`→ ` / `▸ `) and, after the first
second, a live elapsed time is appended (`… (1m 23s)`). Keep `render_call`
output to a single line starting with a marker like `▸ ` so the spinner has
something to replace.

For behavioral guidance not tied to any tool, `add_prompt_guideline(text)`
adds a line to the system prompt's Guidelines section (de-duplicated at
build time; `/reload` rebuilds the prompt when guidelines change).

### Commands

`register_command(name, handler, *, description, usage, aliases)` adds a
slash command. Handlers are sync, receive `(args: str, context)`, and may
return a `str` shown to the user. Built-in commands cannot be overridden.
Extension commands appear in the TUI autocomplete automatically.

### UI dialogs

`tau.context.ui` gives extensions host-provided interactive dialogs (Pi's
`ctx.ui`). All three dialog methods are `async`:

```python
choice = await tau.context.ui.select("Deploy to", ["staging", "prod"])
ok     = await tau.context.ui.confirm("Deploy?", "This ships to production.")
name   = await tau.context.ui.input("Release name", "e.g. v1.2.0")
```

- `select(title, options, *, timeout=None) -> str | None` — a picker;
  returns the chosen option, or `None` if cancelled.
- `confirm(title, message, *, timeout=None) -> bool` — a yes/no dialog;
  returns `True` only if confirmed.
- `input(title, placeholder="", *, timeout=None) -> str | None` — a text
  prompt; returns the text (empty string on an empty submit), or `None` if
  cancelled.
- `timeout` is in **seconds**; when it elapses the dialog auto-dismisses and
  returns the cancel default (`None`/`False`/`None`).

Without an interactive frontend (print mode, `-p`, tests) every dialog
returns its cancel default immediately, so extensions can call them
unconditionally. Check `tau.context.ui.has_ui` (or `tau.context.has_ui`) if
you want to branch on whether a real UI is attached.

**Driving a dialog from a slash command.** Command handlers are synchronous,
so they cannot `await` a dialog directly. Instead, spawn a task on the
running event loop and return immediately:

```python
import asyncio

def _handler(args, context):
    async def _menu():
        choice = await context.api.context.ui.select("Action", ["deploy", "cancel"])
        if choice and choice != "cancel":
            context.api.send_user_message(f"run {choice}")
    asyncio.get_running_loop().create_task(_menu())
    return None  # any returned text opens a modal the user must dismiss first

def setup(tau):
    tau.register_command("menu", _handler)
```

The task runs on the same event loop as the session, so awaiting the dialog
there is safe. (A tool executor, which is already `async`, can `await
tau.context.ui...` directly.)

### Sidebar sections

`tau.context.ui.sidebar` lets an extension contribute a section to Tau's
interactive session sidebar without querying private widget IDs or importing
`TauTuiApp`. Register sections from `session_start`, after the frontend bridge
is attached:

```python
def setup(tau):
    turn_count = 0

    def show(context):
        sidebar = getattr(context.ui, "sidebar", None)
        if sidebar is not None and sidebar.supported:
            sidebar.set_section(
                "turns",
                title="extension status",
                content=[f"[green]{turn_count}[/green] completed turns"],
            )

    @tau.on("session_start")
    def started(event, context):
        show(context)

    @tau.on("turn_end")
    def finished(event, context):
        nonlocal turn_count
        turn_count += 1
        show(context)  # replacing the same key updates it in place

    @tau.on("session_shutdown")
    def stopped(event, context):
        sidebar = getattr(context.ui, "sidebar", None)
        if sidebar is not None:
            sidebar.remove_section("turns")
```

- Feature-detect the `sidebar` attribute with `getattr` when supporting older
  Tau versions. On current Tau, `sidebar.supported` is `False` in
  print/headless mode and when `sidebar_position` is `"off"`; calls are safe
  no-ops without a visible sidebar.
- `set_section(key, *, title, content)` adds or replaces this extension's key.
  Keys are isolated by extension, so two extensions may both use `"status"`.
  Updating a key preserves its position; removing and re-adding it places it
  after existing extension sections.
- `content` is either a sequence of Rich-markup display lines or a
  `factory(theme) -> textual.widget.Widget`. Prefer lines when possible: they
  need no Textual import and the host owns wrapping, width, scrolling, heading,
  separator, and left/right placement. Factories are rebuilt with the live
  theme and use the same crash isolation as other extension widgets.
- `remove_section(key)` removes the section. The host also clears every
  extension section on reload, session replacement, and shutdown. Responsive
  hiding preserves sections so they return when the terminal grows.

See `examples/extensions/sidebar_status.py` for a complete example.

### Component widgets

> This seam lets an extension mount its own **Textual widgets** into the TUI
> instead of publishing string data. It deliberately makes Textual part of the
> public extension contract (the "component" type *is*
> `textual.widget.Widget`): extensions build against the Textual version tau
> pins, and a Textual major bump is a coordinated break for core and
> extensions together. An extension that runs its own conversations (e.g.
> subagents) builds its own agents strip and in-place conversation view with
> this seam. Prefer strings/data (message renderers, tool renderers, string
> slot widgets) when they are enough — they work in every frontend, including
> print mode; reach for widgets when the extension needs live, interactive UI.

`tau.context.ui.components` (a `ComponentBridge`) hosts extension widgets.
Always gate on `supports_components` first — it is `False` in print mode and on
any host without this seam, where every call below is a safe no-op:

```python
def setup(tau):
    components = tau.context.ui.components
    if not components.supports_components:
        return  # print mode / older host: stay widget-less but functional

    # A persistent widget above or below the prompt. The factory runs on the UI
    # thread and receives the live theme.
    def build_strip(theme):
        return MyStripWidget(theme)          # a textual.widget.Widget

    components.set_slot_widget("my-widget", build_strip, placement="below_prompt")
    # set_slot_widget("my-widget", None) removes it again.

    # For plain text you can skip the factory (and the Textual import) entirely
    # by passing a list of display lines — the host renders them as Rich markup:
    #   components.set_slot_widget("status", ["[b]ready[/b]", "2 tasks queued"])

    # A pre-dispatch key hook (ports Pi's onTerminalInput): it is consulted
    # before the host's app-level priority bindings AND before the focused
    # widget, so returning True for "escape" preempts the turn-cancel and
    # returning True for "down" preempts completion nav. It fires for EVERY
    # main-screen key regardless of which widget has focus (never while a
    # modal dialog/picker is on top), so it MUST self-gate — e.g. on the
    # prompt text — and return True only for keys it actually consumes.
    def on_key(event, prompt_text):
        if prompt_text == "" and event.key == "down":
            ...            # activate your widget
            return True     # consume the key
        return False        # let it through
    unsubscribe = components.register_key_interceptor(on_key)
```

- `set_slot_widget(key, content, *, placement="above_prompt")` mounts an
  extension widget into a prompt-adjacent slot (`"above_prompt"` — the default —
  or `"below_prompt"`). `content` is either a `factory(theme)` callable or a
  plain list of display lines the host renders as Rich markup (so a text-only
  widget needs no Textual import); passing `content=None` removes that key.
  Multiple keys per placement mount in call order.
- `open_main_view(factory) -> handle` mounts `factory(handle, theme)` as a
  full main-area view *in place of* the transcript (a display-toggled sibling,
  **not** a modal screen), so your slot widgets stay visible and the prompt
  keeps focus — embed your own composer if you want one. `handle.close()`
  restores the transcript; `handle.is_open` reports its state.
- `register_key_interceptor(handler) -> unsubscribe` — `handler(event,
  prompt_text)`; return `True` to consume a key. Pre-dispatch: consulted ahead
  of the host's priority bindings and the focused widget, for every main-screen
  key (never while a modal is on top) — self-gate accordingly. A raising
  interceptor is treated as "not consumed".
- `theme` is the live `TuiTheme`; `get_prompt_text()` reads the prompt editor
  (interceptors already receive it as their second argument);
  `request_render()` re-renders your mounted widgets. Push live updates by
  calling your widget's own `refresh()` (Textual) — the seam does not poll.

The host is defensive: a factory that raises, a widget that crashes in
`render`/`on_mount`, or a throwing interceptor is isolated (quarantined and
diagnosed) so a broken component never takes the TUI down. All mounted widgets
are force-cleared on session rebind (`/resume`, `/new`) and teardown; also clear
your own on `session_shutdown`.

### Events

Observation events mirror the canonical agent/session stream. Handlers receive
`(event, context)`, run on the session event loop, and may subscribe to one
`type` or use `agent_event` for the complete stream.

| Event | Important payload |
|---|---|
| `agent_start` | — |
| `agent_end` | `messages`, `will_retry` on the session form |
| `agent_settled` | —; the started run has finished teardown and has no automatic retry, compaction, or continuation remaining; dispatched to extensions after interruption even if the cancelling frontend can no longer consume the streamed event |
| `turn_start` | `turn_index`, Unix-millisecond `timestamp` |
| `turn_end` | matching `turn_index`, `message`, `tool_results` |
| `message_start` / `message_end` | `message`; assistant usage is at `message.usage` |
| `message_update` | `message`, nested `assistant_message_event` |
| `tool_execution_start` | `tool_call_id`, `tool_name`, `args` |
| `tool_execution_update` | the call fields plus `partial_result` |
| `tool_execution_end` | the call fields plus `result`, `is_error` |
| `queue_update` | `steering`, `follow_up` |
| `compaction_start` | `reason` (`manual`, `threshold`, or `overflow`) |
| `compaction_end` | `reason`, `result`, `aborted`, `will_retry`, `error_message` |
| `entry_appended` | persisted session `entry` |
| `session_info_changed` | session `name`; emitted after automatic naming or `await session.set_session_name(...)` |
| `thinking_level_changed` | `level`; emitted after an explicit thinking-mode change |
| `auto_retry_start` | `attempt`, `max_attempts`, `delay_ms`, `error_message` |
| `auto_retry_end` | `success`, `attempt`, `final_error` |

`message_update.assistant_message_event` is the provider-neutral incremental
stream. Its nested `type` is one of `text_start`, `text_delta`, `text_end`,
`thinking_start`, `thinking_delta`, `thinking_end`, `toolcall_start`,
`toolcall_delta`, or `toolcall_end`. Terminal provider events become
`message_end`, rather than another `message_update`.

`context.session_name` and `context.thinking_level` provide the current values
when an extension attaches or a replacement session starts. Their matching
change events carry snapshots of later updates; no-op assignments do not emit.
Model changes, `/model` and `/local` selections, scoped-model toggles,
provider reloads, and branch/resume can coerce the active thinking level to
what the selected model supports without an explicit `thinking_level_changed`
event, so read the live context when handling other events instead of treating
change events as a complete cache feed.

Extension turn events are session-enriched like Pi's. `turn_start` and its
matching `turn_end` carry the same zero-based `turn_index`; `turn_start` also
carries a Unix-millisecond `timestamp`. The runtime increments the index after
`turn_end` and resets it on the next `agent_start`:

```python
from tau_coding.extensions import TurnEndEvent, TurnStartEvent


def setup(tau):
    @tau.on("turn_start")
    def on_turn_start(event: TurnStartEvent, context):
        context.api.notify(
            f"turn {event.turn_index} started at {event.timestamp}", "info"
        )

    @tau.on("turn_end")
    def on_turn_end(event: TurnEndEvent, context):
        assert event.turn_index >= 0
```

These enriched payloads are defined in `tau_coding.extensions`. The portable
`tau_agent.events.TurnStartEvent` and `TurnEndEvent` intentionally omit session
metadata, so reusable agent code stays independent of session policy.

Lifecycle and intercepting hooks:

| Event | Payload | Handler may return |
|---|---|---|
| `session_start` | `SessionStartEvent(reason)` | — |
| `session_shutdown` | `SessionShutdownEvent(reason)` | — |
| `input` | `InputEvent(text)` | `InputHookResult(action, text, message)` |
| `tool_call` | `ToolCallHookEvent(tool_name, arguments)` | `ToolCallHookResult(block, reason, arguments)` |
| `tool_result` | `ToolResultHookEvent(tool_name, arguments, result)` | `ToolResultHookResult(content, details)` |

- `session_start` fires once the host frontend is attached (Pi's ordering:
  the UI starts before extensions initialize), so handlers can call
  `tau.notify(...)` or open dialogs and they will actually be seen.
- `input` runs on the raw prompt text before skill/template expansion.
  `action="transform"` rewrites it (transforms chain), `action="handled"`
  consumes it without an agent run and shows `message` as a notification.
- `tool_call` runs before a tool executes. `block=True` prevents execution
  and reports `reason` to the model; returning `arguments` rewrites the
  call. A crashing `tool_call` handler blocks the tool (fail-safe).
- `tool_result` can rewrite a result's text `content` or `details`; execution
  error state belongs to the host's tool lifecycle rather than the result payload.

All other handler failures are contained: they are recorded as diagnostics
(visible in `/session`) and never crash the session.

### Messages and persistence

`send_user_message` delivers a user message into the conversation. During a
run it queues as steering or a follow-up; when the session is idle the TUI
starts a new turn with it — this is how background work reports back.
`append_entry(namespace, data)` persists extension-owned data as a durable
session entry replayed on resume.

### Custom message rendering

To format an injected message instead of showing it as raw text, register a
renderer in `setup` and send with `send_custom_message`:

```python
from tau_coding.extensions import CustomMessageView, MessageRenderOptions

def render_status(view: CustomMessageView, options: MessageRenderOptions) -> str:
    icon = "[green]✓[/green]" if view.details and view.details.get("ok") else "[red]✗[/red]"
    line = f"{icon} [bold]{view.content}[/bold]"
    if options.expanded and view.details:
        line += f"\n[dim]{view.details}[/dim]"
    return line  # a Rich-markup string, never a widget

def setup(tau):
    tau.register_message_renderer("my-ext:status", render_status)

# once the session is running:
tau.send_custom_message(
    "build finished",
    custom_type="my-ext:status",
    details={"ok": True, "duration_ms": 1200},
)
```

- The renderer receives a `CustomMessageView(custom_type, content, details)`
  and `MessageRenderOptions(expanded)`, and returns a **Rich-markup string**
  (e.g. `"[bold]text[/bold]"`). Returning a Textual widget is not supported —
  this keeps extensions free of any TUI toolkit.
- `send_custom_message(content, *, custom_type, details=None,
  deliver_as="follow_up", trigger_turn=True)` behaves like
  `send_user_message` (the `content` still enters the model's context), but the
  transcript renders it through the matching renderer. `trigger_turn=False`
  queues it **in-memory** for the next run instead of starting one when idle —
  the message is not shown or persisted until that run happens, and is lost if
  the session exits first. Use `append_entry` alongside if you need a durable
  record without triggering a turn.
- First registration per `custom_type` wins. If no renderer is registered, or a
  renderer raises or returns a non-string, the message falls back to its raw
  `content` — a broken renderer never crashes the UI.
- Custom rendering works in the interactive TUI and the `-p` print transcript,
  and survives `/resume` (the `custom_type`/`details` are persisted with the
  message). In the TUI, a custom message appears once its user event is
  confirmed by the run (a moment after delivery), rather than instantly like a
  typed prompt's optimistic echo.

## Growing and maintaining an extension

Extensions have three natural sizes; each step is optional and none
requires packaging:

1. **A single file** (`greet.py`) — the quick start above. No config.
2. **A folder with `extension.py`** — split helpers into sibling modules
   and import them relatively (`from . import helper`). No config.
3. **A repo with a `src/` layout** — declare the entry in
   `pyproject.toml` under `[tool.tau]` (see above). Tau reads only the
   `[tool.tau]` table; whether the repo is also an installable Python
   package is entirely your business (it helps IDEs resolve imports and
   lets tests import modules directly, but Tau never installs or
   `pip`-imports your extension).

Two rules keep all three shapes loadable:

- **Use relative imports between your own modules.** The loader imports
  your extension under a synthetic package name (and never touches
  `sys.path`), so `import helper` won't resolve — `from . import helper`
  will, in every load mode.
- **Feature-detect optional Tau APIs** (`getattr`/`try: import`) if you
  want the extension to load on older Tau versions rather than fail at
  import time.

**Testing an extension.** Load it through the real runtime rather than
importing your modules directly — that exercises discovery, the synthetic
package import, and `setup` registration exactly as a session does:

```python
from tau_coding import TauResourcePaths
from tau_coding.extensions import ExtensionRuntime

def test_loads(tmp_path):
    paths = TauResourcePaths(
        root=tmp_path / "tau", cwd=tmp_path / "project",
        agents_root=tmp_path / "agents",
    )
    runtime = ExtensionRuntime()
    runtime.load(paths, extra_paths=(EXTENSION_DIR,), include_resource_dirs=False)
    assert runtime.extension_names == ("my_ext",)
```

`extra_paths` takes your extension directory (or repo root with a
manifest); `include_resource_dirs=False` keeps the test hermetic —
nothing from `~/.tau/extensions` leaks in. To monkeypatch module globals
in tests, patch the loaded synthetic module (find it in `sys.modules` by
the `tau_extension_` prefix), not your package's import identity — the
runtime only sees the former.

## Example extensions

See [`examples/extensions/`](https://github.com/huggingface/tau/tree/main/examples/extensions):

- **`hello_tool.py`** — minimal custom tool.
- **`permission_gate.py`** — blocks dangerous bash commands with the
  `tool_call` hook.
- **`sidebar_status.py`** — adds and updates a host-framed sidebar section.

A larger, real-world extension lives in its own repository:
[rian-dolphin/tau-subagents](https://github.com/rian-dolphin/tau-subagents)
ports [pi-subagents](https://github.com/tintinweb/pi-subagents) — an `agent`
tool that spawns autonomous subagents in-process with their own tools and
system prompts, foreground and background modes, agent types defined in
`.tau/agents/*.md`, `get_subagent_result` and `steer_subagent` tools, an
`/agents` command, and a custom renderer for completion notifications. It is
also the reference for the `[tool.tau]` manifest shape above (a `src/` layout
package that feature-detects newer API seams).

```bash
git clone git@github.com:rian-dolphin/tau-subagents.git
tau -e ./tau-subagents
# then: "Use a subagent to summarize this repository's architecture."
```

## Not yet supported

Compared to Pi's extension system, Tau does not yet include a complete package
manager (the installer has no registry, dependency installation, remove, or
package-update commands), custom entry renderers (non-context cards),
declarative keyboard-shortcut registration, CLI flag registration,
system-prompt replacement, or context rewriting. The architecture document
(`dev-notes/architecture/phase-21-extensions.md`) tracks the extension design.
