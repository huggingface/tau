# Tau extensions

Extensions are Python modules that customize a Tau session: they add tools
and slash commands, observe the agent event stream, and intercept tool
calls, tool results, and user input. The design follows Pi's extension
system, adapted to Python.

## Quick start

Create `~/.tau/extensions/greet.py`:

```python
from tau_agent.tools import AgentTool, AgentToolResult


async def run_greet(arguments, signal=None):
    return AgentToolResult(
        tool_call_id="",
        name="greet",
        ok=True,
        content=f"Hello, {arguments.get('who', 'world')}!",
    )


def setup(tau):
    tau.register_tool(
        AgentTool(
            name="greet",
            description="Greet someone.",
            input_schema={
                "type": "object",
                "properties": {"who": {"type": "string"}},
            },
            executor=run_greet,
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

Extensions load project-first; on name conflicts (extension names, tool
names, command names) the first registration wins. `--no-extensions`
disables directory discovery entirely (explicit `-x` paths still load).
`/reload` re-imports all extensions and re-runs `setup`; it does not emit
`session_shutdown`, so background work an extension started before the
reload is orphaned — treat `/reload` as a restart of extension state.

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

    # actions — valid once the session is running, not during setup
    tau.send_user_message("text", deliver_as="follow_up")  # or "steer"
    tau.send_custom_message("text", custom_type="my-ext:status", details={...})
    await tau.append_entry("my-ext:records", {"key": "value"})
    tau.notify("message", "info")            # "info" | "warning" | "error"

    # read-only context
    tau.context.cwd, tau.context.model, tau.context.provider_name
    tau.context.session_id, tau.context.system_prompt
    tau.context.is_running, tau.context.has_ui

    # interactive UI dialogs (async; see "UI dialogs" below)
    await tau.context.ui.select("Title", ["a", "b"])   # -> str | None
    await tau.context.ui.confirm("Title", "message")   # -> bool
    await tau.context.ui.input("Title", "placeholder") # -> str | None
    tau.context.ui.notify("message", "info")           # same as tau.notify
```

`setup` must be a plain `def` (not `async def`). Event handlers may be sync
or async. Action methods raise `ExtensionError` if called before the session
is bound — register handlers in `setup` and act on events instead.

### Tools

`register_tool` takes a plain `tau_agent.tools.AgentTool`: a name, a
description, a hand-written JSON-schema `input_schema`, and an async
executor `(arguments, signal=None) -> AgentToolResult`. Give the tool a
`prompt_snippet` to list it in the system prompt's "Available tools"
section, and `prompt_guidelines` for usage guidance tied to the tool.
Registering a tool with a built-in's name (`read`, `write`, `edit`,
`bash`) replaces the built-in.

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
    return "opening menu..."

def setup(tau):
    tau.register_command("menu", _handler)
```

The task runs on the same event loop as the session, so awaiting the dialog
there is safe. (A tool executor, which is already `async`, can `await
tau.context.ui...` directly.)

### Events

Observation events mirror the agent event stream — subscribe by the event's
`type` literal: `agent_start`, `agent_end`, `turn_start`, `turn_end`,
`message_start`, `message_delta`, `thinking_delta`, `message_end`,
`tool_execution_start`, `tool_execution_update`, `tool_execution_end`,
`retry`, `queue_update`, `error` — or `agent_event` for everything (fires
per streamed token; prefer specific events). Handlers must be fast; they run
on the session's event loop.

Lifecycle and intercepting hooks:

| Event | Payload | Handler may return |
|---|---|---|
| `session_start` | `SessionStartEvent(reason)` | — |
| `session_shutdown` | `SessionShutdownEvent(reason)` | — |
| `input` | `InputEvent(text)` | `InputHookResult(action, text, message)` |
| `tool_call` | `ToolCallHookEvent(tool_name, arguments)` | `ToolCallHookResult(block, reason, arguments)` |
| `tool_result` | `ToolResultHookEvent(tool_name, arguments, result)` | `ToolResultHookResult(content, ok, details)` |

- `input` runs on the raw prompt text before skill/template expansion.
  `action="transform"` rewrites it (transforms chain), `action="handled"`
  consumes it without an agent run and shows `message` as a notification.
- `tool_call` runs before a tool executes. `block=True` prevents execution
  and reports `reason` to the model; returning `arguments` rewrites the
  call. A crashing `tool_call` handler blocks the tool (fail-safe).
- `tool_result` can rewrite a result's `content`, `ok`, or `details`.

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
  message).

## Example extensions

See [`examples/extensions/`](../examples/extensions):

- **`hello_tool.py`** — minimal custom tool.
- **`permission_gate.py`** — blocks dangerous bash commands with the
  `tool_call` hook.

A larger, real-world extension lives in its own repository:
[rian-dolphin/tau-subagents](https://github.com/rian-dolphin/tau-subagents)
ports [pi-subagents](https://github.com/tintinweb/pi-subagents) — an `agent`
tool that spawns autonomous subagents in-process with their own tools and
system prompts, foreground and background modes, agent types defined in
`.tau/agents/*.md`, a `get_subagent_result` tool, and an `/agents` command.

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
