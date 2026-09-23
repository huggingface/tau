---
title: "ADR 0004 — Expose run-scoped prompt and awaitable command extension seams"
---

## Status

Accepted on 2026-09-17 by the project owner in the Tau Ponytail planning
session.

## Context

Stateful prompt extensions such as Ponytail need to select system-prompt
guidance from mutable session state immediately before an agent run. Tau's
static prompt sections are fixed during extension setup, while its input hooks
cannot replace the system prompt without changing user-message semantics.

These extensions also need slash commands to persist state before reporting
success. Tau's persistence API is asynchronous, but its command dispatch path
is synchronous. Scheduling persistence in a background task creates races with
shutdown and session replacement.

The approved behavioral design is
[`dev-notes/design/ponytail-extension-prerequisites.md`](../design/ponytail-extension-prerequisites.md).

## Decision

Add two provider-neutral public extension seams:

1. A `before_agent_start` lifecycle hook that receives the expanded prompt and
   current system prompt. Synchronous and asynchronous handlers compose in
   extension order by returning full system-prompt replacements. Invalid or
   failing handlers are diagnosed and skipped. The final prompt is scoped to
   one agent run and is never persisted.
2. Awaitable slash-command dispatch. `CommandRegistry.execute()` and
   `CodingSession.handle_command()` become async and resolve both synchronous
   and asynchronous handlers. CLI, TUI, tests, and frontend documentation use
   the single awaited API.

Do not add a second `handle_command_async()` API. A dual path would duplicate
semantics or make synchronous callers unable to execute valid registered
commands.

Do not implement this through provider wrappers, durable message injection,
monkey patches, or a synchronous persistence API.

## Consequences

- Extensions can change run-scoped system guidance without transcript effects
  or private Tau access.
- Extension commands can await `append_entry()` and other public async APIs
  before returning.
- Existing synchronous command handlers remain valid.
- Callers of the public `CodingSession.handle_command()` method must add
  `await`. On the current tree this is a focused migration: two CLI call sites,
  one TUI call site, tests, test doubles, and documentation.
- The extension runtime must restore the base system prompt after success,
  failure, or cancellation and contain hook failures.
- This decision does not revive or merge the unpublished local `context` hook
  experiment; per-request context additions remain an independent capability.
