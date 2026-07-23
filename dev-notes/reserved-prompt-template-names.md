# Reserved prompt-template names

## Problem

`CodingSession.handle_command()` checks prompt templates before the slash-command
registry. A template such as `new.md` therefore intercepted `/new` and expanded
into custom prompt text instead of starting a new session. Only `prompts.md`,
`skills.md`, and `tools.md` were reserved.

## Policy

At resource-loading time, Tau derives the reserved set from
`create_default_command_registry()`:

- every built-in command name (for example `new`, `quit`, `reload`)
- every built-in alias (for example `exit` for `/quit`)

Matching is case-insensitive on the markdown filename stem. Colliding templates
are not loaded; each one emits a resource diagnostic with its path and the
conflicting built-in command. Non-conflicting templates still expand before
ordinary command dispatch.

Command-first dispatch was rejected because it would silently change behavior for
installations that intentionally shadowed commands with templates.

Extension-provided commands are dynamic and may change on `/reload`, so they are
out of scope for this reservation pass.

## Verify

```bash
uv run pytest tests/test_prompt_templates.py \
  tests/test_coding_session.py -k reserved
uv run ruff check src/tau_coding tests
```

Manual check: add `~/.agents/prompts/new.md`, start `tau`, run `/new` — the
session should request a new session and report a prompt diagnostic for the
ignored template.
