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

Extensions load project-first; on name conflicts (extension names, tool
names, command names) the first registration wins. `--no-extensions`
disables directory discovery entirely (explicit `-x` paths still load).
`/reload` re-imports all extensions.

> **Security.** Extensions execute arbitrary Python inside your session.
> Project extensions are therefore off by default — enable them with
> `--project-extensions` only in repositories you trust.

## The extension API

```python
def setup(tau):
    # registration
    tau.register_tool(agent_tool)            # tau_agent.tools.AgentTool
    tau.register_command("name", handler, description="...")
    tau.on("event_name", handler)            # or @tau.on("event_name")

    # actions — valid once the session is running, not during setup
    tau.send_user_message("text", deliver_as="follow_up")  # or "steer"
    await tau.append_entry("my-ext:records", {"key": "value"})
    tau.notify("message", "info")            # "info" | "warning" | "error"

    # read-only context
    tau.context.cwd, tau.context.model, tau.context.provider_name
    tau.context.session_id, tau.context.system_prompt
    tau.context.is_running, tau.context.has_ui
```

`setup` must be a plain `def` (not `async def`). Event handlers may be sync
or async. Action methods raise `ExtensionError` if called before the session
is bound — register handlers in `setup` and act on events instead.

### Tools

`register_tool` takes a plain `tau_agent.tools.AgentTool`: a name, a
description, a hand-written JSON-schema `input_schema`, and an async
executor `(arguments, signal=None) -> AgentToolResult`. Give the tool a
`prompt_snippet` to list it in the system prompt's "Available tools"
section. Registering a tool with a built-in's name (`read`, `write`,
`edit`, `bash`) replaces the built-in.

### Commands

`register_command(name, handler, *, description, usage, aliases)` adds a
slash command. Handlers are sync, receive `(args: str, context)`, and may
return a `str` shown to the user. Built-in commands cannot be overridden.
Extension commands appear in the TUI autocomplete automatically.

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

## Example extensions

See [`examples/extensions/`](../examples/extensions):

- **`hello_tool.py`** — minimal custom tool.
- **`permission_gate.py`** — blocks dangerous bash commands with the
  `tool_call` hook.
- **`subagents/`** — a port of
  [pi-subagents](https://github.com/tintinweb/pi-subagents): an `agent`
  tool that spawns autonomous subagents in-process with their own tools and
  system prompts, foreground and background modes, agent types defined in
  `.tau/agents/*.md`, a `get_subagent_result` tool, and an `/agents`
  command. Try it:

  ```bash
  tau -x examples/extensions/subagents
  # then: "Use a subagent to summarize this repository's architecture."
  ```

  Custom agent types are markdown files with frontmatter:

  ```markdown
  ---
  description: Reviews code for security issues.
  tools: read, bash
  model: gpt-5.2
  ---
  You are a security reviewer. Investigate the code you are pointed at and
  report vulnerabilities with file references.
  ```

  saved as `.tau/agents/security-reviewer.md` (project) or
  `~/.tau/agents/security-reviewer.md` (user).

## Not yet supported

Compared to Pi's extension system, v1 does not yet include: package
management (`pi install`-style), custom providers, custom TUI
widgets/renderers/dialogs, keyboard shortcuts, CLI flag registration,
system-prompt replacement, context rewriting, partial tool-result
streaming, or a project trust store. The architecture document
(`dev-notes/architecture/phase-21-extensions.md`) tracks these.
