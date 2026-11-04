# Reserved prompt-template names

## What

A prompt template is invoked by its markdown filename stem before ordinary
slash-command dispatch (`CodingSession.handle_command()` expands templates
first). A template named `new.md` therefore intercepted `/new` and expanded
custom text instead of starting a new session. Tau now reserves every built-in
slash-command name **and alias** at resource-loading time:

- The reserved set is derived from `create_default_command_registry()` via
  `builtin_prompt_template_reservations()` in
  `src/tau_coding/prompt_templates.py`, so it cannot drift from the actual
  built-in commands. No hard-coded name list is maintained.
- Matching is case-insensitive on the filename stem (`NEW.md` collides with
  `/new`).
- A colliding template is not loaded. Each one emits a `ResourceDiagnostic`
  (`kind="prompt"`) naming the file path and the conflicting built-in command
  or alias, for example
  `prompt template name is reserved by the built-in /exit command; template ignored`.
- Non-conflicting templates keep the existing prompt-expansion path, so the
  behavior is identical at startup and after `/reload`.

Because rejection happens at load time, `session.prompt_templates` only ever
contains invocable templates — TUI autocomplete and the `/prompts` picker
cannot advertise a rejected template.

## Why

Command-first dispatch was considered and rejected: it would silently turn an
existing `new.md` template from a prompt expansion into a `/new` session reset,
with no signal to the user. Reserving names at load time makes the collision
visible (a diagnostic) and deterministic (the built-in always wins), at the
cost of a one-time rename for installations that shadowed commands on purpose.

## Scope

Extension-provided command names are **not** reserved. Extensions are dynamic —
they can appear or disappear on `/reload` — so a stable reservation set cannot
be derived from them. If an extension command and a template collide, the
template keeps precedence (the pre-existing expansion-first behavior).

The `/scoped models` shorthand inside `CommandRegistry.execute()` is a parsing
special case, not a registered command, so `scoped.md` is not reserved; the
canonical `/scoped-models` name is.

## How to test

```bash
uv run pytest tests/test_prompt_templates.py tests/test_coding_session.py -k reserved
```

Manual check: add `~/.agents/prompts/new.md`, start `tau`, and run `/new` — the
session starts a new session, and the resource diagnostics report the ignored
template. Rename the file to `new-feature.md` and `/new-feature` expands the
template again.
