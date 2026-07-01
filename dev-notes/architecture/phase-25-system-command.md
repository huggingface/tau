---
title: "Phase 25: System Prompt Display Command"
---

Phase 25 adds `/system`, a local-only slash command that displays the effective
system prompt Tau will send to the provider.

## What changed

- The coding command registry now includes `/system`.
- `CodingSession.system_prompt` exposes the harness's current effective system
  prompt to command handlers.
- Print mode handles local slash commands before starting a provider call.
- The TUI renders `/system` output inline in the visible thread, like `/reload`.

## Why it exists

The system prompt is important debugging context, especially when project
instructions, skills, tools, and custom prompts are composed together. Users need
a direct way to inspect it without changing the conversation that the model sees.

## Persistence and context behavior

`/system` returns a `CommandResult.message`; it does not append a user message,
assistant message, or custom durable entry. That means:

- no provider request is started for the command;
- the displayed system prompt is not added to `CodingSession.messages`;
- JSONL session storage is unchanged except for any normal initial session
  metadata that already exists.

This keeps the separation between user-visible UI output, model context, and
append-only session history.

## How to test

Run:

```bash
uv run pytest tests/test_commands.py tests/test_coding_session.py tests/test_cli.py
```
