---
title: Extensions
description: Extend Tau with plain Python — custom tools, slash commands, hooks, dialogs, and message rendering.
---

Extensions are Python modules that customize a Tau session: they add tools
and slash commands, observe the agent event stream, and intercept tool
calls, tool results, and user input. The design follows Pi's extension
system, adapted to Python.

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

## Where extensions live

| Location | Loaded |
|---|---|
| `~/.tau/extensions/` | by default |
| `<project>/.tau/extensions/` | only with `--project-extensions` |
| any file or directory | with `tau -x PATH` (repeatable) |

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

One caveat: `tau -x` on an entry **file** loads it standalone — no
package, so relative imports fail. Once an extension has sibling
modules, always pass a directory: the package directory itself, or the
repo root when a manifest declares the entry.

Extensions load project-first; on name conflicts (extension names, tool
names, command names) the first registration wins. `--no-extensions`
disables directory discovery entirely (explicit `-x` paths still load).
`/reload` awaits `session_shutdown(reason="reload")` on the outgoing
extension generation, clears its UI, re-imports every extension and re-runs
`setup`, then awaits `session_start(reason="reload")` on the new generation.
Use those lifecycle hooks to stop and restart background work and to remount UI.

> **Security.** Extensions execute arbitrary Python inside your session.
> Project extensions are therefore off by default — enable them with
> `--project-extensions` only in repositories you trust.

## The extension API

```python
def setup(tau):
    # registration
    tau.register_tool(agent_tool)            # tau_agent.tools.AgentTool
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

    # read-only context
    tau.context.cwd, tau.context.model, tau.context.provider_name
    tau.context.session_id, tau.context.system_prompt
    tau.context.is_running, tau.context.has_ui
    tau.context.transcript   # parent conversation, deep-copied AgentMessages

    # interactive UI dialogs (async; see "UI dialogs" below)
    await tau.context.ui.select("Title", ["a", "b"])   # -> str | None
    await tau.context.ui.confirm("Title", "message")   # -> bool
    await tau.context.ui.input("Title", "placeholder") # -> str | None
    tau.context.ui.notify("message", "info")           # same as tau.notify
```

`setup` must be a plain `def` (not `async def`). Event handlers may be sync
or async and always receive `(event, context)`; the context is freshly created
for each dispatch. Action methods raise `ExtensionError` if called before the session
is bound — register handlers in `setup` and act on events instead.

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

Observation events mirror the canonical agent/session stream — subscribe by
`type`: `agent_start`, `agent_end`, `agent_settled`, `turn_start`, `turn_end`,
`message_start`, `message_update`, `message_end`, `tool_execution_start`,
`tool_execution_update`, `tool_execution_end`, `queue_update`,
`compaction_start`, `compaction_end`, `entry_appended`,
`session_info_changed`, `thinking_level_changed`, `auto_retry_start`, and
`auto_retry_end`. Use `agent_event` for the complete stream. A
`message_update` contains the provider event in `assistant_message_event`
(text/thinking/tool-call start, delta, and end). Handlers receive
`(event, context)` and run on the session event loop. `message_end` carries
provider token usage at `event.message.usage`. Extension `turn_start` and
`turn_end` events are session-enriched like Pi's: both carry the same zero-based
`turn_index`, `turn_start` also carries a Unix-millisecond `timestamp`, and the
index increments after `turn_end` (then resets on the next `agent_start`). These
extension payloads are defined in `tau_coding.extensions`; the portable
`tau_agent` turn events intentionally remain free of session metadata.

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
tau -x ./tau-subagents
# then: "Use a subagent to summarize this repository's architecture."
```

## Not yet supported

Compared to Pi's extension system, v1 does not yet include: package
management (`pi install`-style), custom providers, extension-authored TUI
widgets (custom *message* rendering via `register_message_renderer` *is*
supported; the host-provided `context.ui` dialogs *are* supported), custom
entry renderers (non-context cards), keyboard shortcuts, CLI flag
registration, system-prompt replacement, context rewriting, or a project trust
store. The
architecture document
(`dev-notes/architecture/phase-21-extensions.md`) tracks these.
