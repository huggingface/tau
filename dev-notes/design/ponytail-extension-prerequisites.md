# Ponytail extension prerequisites

## Status

Approved on 2026-09-17 by the project owner in the Tau Ponytail planning
session. Revision 1 covers the two public seams defined here; implementation
and upstream publication remain separately gated.

## Goal

Let a Tau extension reproduce Ponytail's Pi behavior:

- choose prompt guidance from mutable extension state immediately before an
  agent run;
- persist mode changes from slash commands before the command reports success;
- restore that state on a later session; and
- remain a normal extension that uses only Tau's public `ExtensionAPI`.

Tau needs two generic capabilities: a `before_agent_start` system-prompt hook
and awaitable slash-command handlers.

## Why existing APIs are insufficient

`add_prompt_section()` contributes static setup-time text. Ponytail's active
mode changes during a session, so the extension must choose guidance at run
time.

`input` hooks can transform or consume user input but cannot change the system
prompt. Injecting a durable user message would change transcript and replay
semantics. The unpublished local `context` hook experiment on
`feat/pi-extension-ports` appends ephemeral messages before provider requests,
but it does not replace the system prompt and is not present on Tau `main`.

`ExtensionAPI.append_entry()` is async, while extension command handlers and
the command path are currently synchronous. Scheduling persistence in a
background task would let a command report success before the mode entry is
durable and would introduce shutdown and session-replacement races.

## Design

### 1. `before_agent_start`

Add a lifecycle event and result type to the public extension API:

```python
@dataclass(frozen=True, slots=True)
class BeforeAgentStartEvent:
    prompt: str
    system_prompt: str


@dataclass(frozen=True, slots=True)
class BeforeAgentStartHookResult:
    system_prompt: str | None = None
```

An extension may register a synchronous or asynchronous handler with
`tau.on("before_agent_start", handler)`.

Tau invokes the handlers after input hooks and skill/template expansion, after
static system-prompt construction, and immediately before starting an idle
agent run. It does not invoke them when an input is merely queued into an
already-running agent.

Handlers run in extension order. Each handler receives the system prompt
produced by the previous handler. Returning `None`, or returning a result whose
`system_prompt` is `None`, leaves the current value unchanged. A string is a
full replacement, matching Pi's current `before_agent_start` contract.

The final value applies to every provider request, tool continuation, and
automatic retry in that agent run. Tau restores the session's base system
prompt when the run settles, fails, or is cancelled; the transformed value is
never persisted.

Handler exceptions and invalid results are recorded through the existing
extension runtime diagnostic path and ignored. Later handlers still run. This
is fail-open behavior: a faulty prompt extension cannot prevent the user from
starting an agent run.

The first version intentionally omits Pi fields and results that Ponytail does
not use, including images, structured system-prompt options, and injected
messages. They can be added independently when a concrete extension needs
them.

### 2. Awaitable slash-command handlers

Broaden both command handler contracts so handlers may return their existing
result directly or through an awaitable:

```python
CommandHandler = Callable[
    [CommandContext],
    CommandResult | Awaitable[CommandResult],
]

ExtensionCommandHandler = Callable[
    [str, ExtensionCommandContext],
    str | None | Awaitable[str | None],
]
```

Make `CommandRegistry.execute()` and `CodingSession.handle_command()` async.
They await an awaitable result and otherwise accept the existing synchronous
result unchanged. Tau's CLI and TUI already call commands from async paths, so
they simply await `handle_command()`.

This is a focused public API change, not a large architectural refactor. On the
current tree, `handle_command()` has three production callers outside its own
definition: two in the CLI and one in the TUI. Tests, frontend examples, and
test doubles must also add `await` or become async.

Changing the one command API is smaller and safer than adding a parallel
`handle_command_async()` path. Two public paths would either duplicate dispatch
semantics or leave synchronous callers unable to execute valid extension
commands.

Extension command exceptions, including exceptions raised after an `await`,
keep the existing containment behavior: record a runtime diagnostic and return
an handled command result with an error message. Built-in synchronous command
handlers remain valid without modification.

## Runtime flow

```text
submitted text
  -> await session.handle_command(text)
       -> await extension command
            -> await tau.append_entry(...)
       -> command result

ordinary prompt
  -> input hooks
  -> skill/template expansion
  -> before_agent_start handlers
       -> chained ephemeral system prompt
  -> agent run and provider requests
  -> restore base system prompt
```

For Ponytail, a mode command mutates setup-generation-local state, awaits its
custom session entry, and then returns its confirmation. `session_start`
restores the latest valid Ponytail entry. `before_agent_start` reads the same
in-memory state and replaces the system prompt for that run.

## Compatibility and migration

- Existing synchronous built-in and extension command handler functions remain
  valid.
- Callers of public `CodingSession.handle_command()` must add `await`; the
  custom-frontend documentation must show the new call shape.
- Existing extension hooks and static prompt sections are unchanged.
- The unpublished `context` and `session_before_compact` work on the old local
  `feat/pi-extension-ports` branch is independent and must not be bundled into
  this minimal prerequisite change.
- Tau Ponytail will require the first Tau release containing both capabilities
  and should fail setup with an actionable minimum-version diagnostic on older
  Tau versions.

## Scope

Expected production changes are limited to:

- extension event/result definitions and exports;
- extension runtime dispatch and diagnostics;
- command handler typing and awaitable resolution;
- `CodingSession.prompt()` run-scoped system-prompt replacement;
- `CommandRegistry.execute()` and `CodingSession.handle_command()` async
  boundaries;
- the CLI and TUI command call sites; and
- public extension/custom-frontend documentation.

No provider wrapper, monkey patch, private session access, transcript message,
or new persistence API is introduced.

## Testing

The smallest complete test set proves:

1. two sync/async `before_agent_start` handlers see and replace the prompt in
   sequence;
2. a failing or malformed handler is diagnosed, skipped, and does not stop the
   run;
3. the transformed prompt reaches every provider request in one tool-using run
   but not the next run after state changes;
4. cancellation and provider failure restore the base system prompt;
5. queued input does not start a second prompt-transform chain;
6. synchronous extension commands still work;
7. an async extension command can await `append_entry()` and the entry exists
   before `handle_command()` returns; and
8. CLI and TUI command paths await command completion.

Run Tau's normal gates after implementation:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Non-goals

- porting Ponytail itself into Tau core;
- adding a generic provider-payload rewrite hook;
- reviving or merging the unrelated local `context` hook branch;
- making extension setup asynchronous;
- adding a synchronous persistence method; or
- matching every field of Pi's broader `before_agent_start` event before a real
  Tau extension needs it.
