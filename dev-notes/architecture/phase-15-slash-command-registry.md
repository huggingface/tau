---
title: "Phase 15: Slash Command Registry"
---

Phase 15 replaces Tau's hardcoded slash-command handling with a small registry in
`tau_coding`.

The implementation lives in:

```text
src/tau_coding/commands.py
```

## What was added

Tau now has a `CommandRegistry` that can:

- register slash commands with names, aliases, descriptions, and usage text
- list registered commands for generated help output
- parse slash-command input
- dispatch commands to handlers
- return structured command results to UI layers

The core command types are:

```python
SlashCommand
CommandContext
CommandResult
CommandRegistry
```

`CodingSession.handle_command()` now delegates to the registry instead of
hardcoding command behavior.

## Built-in commands

The default registry includes:

- `/quit` — exit the current session
- `/new` — start a new session
- `/compact` — replace active context with a manual summary
- `/export` — export the current session
- `/session` — show session info and stats
- `/hotkeys` — show common TUI keyboard shortcuts
- `/resume` — open previous-session selection or resume a specific session id
- `/model` — choose or switch the current model
- `/login` — add or refresh a built-in provider login
- `/reload` — reload local resources and project context
- `/name` — rename the current session
- `/theme` — show or set the TUI theme

Autocomplete also uses non-executable search terms. For example, typing
`/clear` suggests `/new`, but search terms are not registered commands.

## Why this belongs in `tau_coding`

Slash commands are coding-agent application behavior. They depend on resources,
sessions, skills, provider/model UX, and UI expectations.

The reusable `tau_agent` package remains independent of slash commands, Textual,
Typer, local config directories, and Tau-specific product behavior.

## Pi command alignment

Pi's built-in command list includes:

```text
settings, model, scoped-models, export, import, share, copy, name, session,
changelog, hotkeys, fork, clone, tree, login, logout, new, compact, resume,
reload, quit
```

Tau mirrors the commands that map cleanly onto existing Tau capabilities:

- `/session` maps to Tau's existing session status/details output.
- `/hotkeys` reports Tau's current common TUI shortcuts.
- `/quit` is the only registered exit command; older Tau-specific `/exit` and
  `/q` aliases are intentionally not retained.
- `/model`, `/login`, `/new`, `/compact`, `/resume`, `/reload`, `/name`,
  `/theme`, and `/export` exist in Tau's command registry.

Tau intentionally removes older Tau-specific diagnostic commands from the
registry, including `/help`, `/status`, `/skills`, `/resources`, `/context`,
and `/thinking`. Equivalent information remains visible through
Pi-aligned commands, persistent UI surfaces, keybindings, or the model's system
prompt.

The remaining Pi commands are deferred because they require larger workflows
outside this registry cleanup:

- `/settings`
- `/scoped-models`
- `/import`
- `/share`
- `/copy`
- `/changelog`
- `/fork`
- `/clone`
- `/tree`
- `/logout`

## TUI integration

The TUI still calls:

```python
session.handle_command(text)
```

but the returned `CommandResult` can now request more than exit behavior.

For example, `/new` returns `new_session_requested=True`. The TUI responds by
creating and loading a fresh indexed session without deleting older durable JSONL
session history.

## Prompt expansion command behavior

`/skill:<name> [request]` remains a prompt-expansion path, not a normal slash
command.

Prompt templates also behave as dynamic prompt-expansion commands. For example,
`.agents/prompts/example.md` is invoked as `/example [arguments]` and expanded by
`CodingSession.prompt()` before the model sees the prompt.

The registry intentionally returns `handled=False` for skill and prompt-template
expansion directives so `CodingSession.prompt()` can replace them before sending
the prompt to the model.

There is no plain `/skill` slash command in the Pi-aligned registry.

## Future use

This registry is the foundation for later phases:

- TUI slash-command autocomplete can read command metadata from the registry.
- Extensions can eventually contribute commands.
- A future command palette can show the same command metadata.

## Tests

The phase is covered by:

```text
tests/test_commands.py
tests/test_coding_session.py
tests/test_tui_app.py
```

The tests verify:

- command parsing and dispatch
- generated help output
- exit and new-session control flags
- status and skills output
- `/skill:<name>` passthrough behavior
- indexed session listing
- structured `/resume <session-id>` requests
- TUI handling for `/new`
