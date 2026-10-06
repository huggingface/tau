---
title: What is Tau?
description: Tau is a small, readable coding agent for your terminal — and a working example of how coding agents are built.
type: doc
---

**Tau is a coding agent that lives in your terminal.** You type what you want in
plain English — "explain this repo", "add tests for the parser", "fix this
stack trace" — and Tau reads files, runs commands, and edits code to get it
done, streaming its work as it goes.

It's two things at once:

- **A tool you can use.** A real terminal coding agent with an interactive UI,
  multiple model providers, durable sessions you can resume and branch, and a
  resource system for your own skills and prompts.
- **A project you can learn from.** Tau is built to be *read*. Its source is
  organized into small, honest layers so you can see exactly how a coding agent
  works — from model streaming, to the agent loop, to tools and sessions.

> [!NOTE] Why "Tau"?
> The name is a small joke about picking the *right* foundation. See
> [Why "Tau"?](/why-tau/) for the (genuinely fun) math rant behind it.

## What can it do?

- Hold a conversation while **reading, writing, and editing files** and
  **running shell commands** in your project.
- Work in an **interactive TUI** or as a **one-shot command** for scripts and
  pipes.
- Talk to **OpenAI, Anthropic, OpenAI Codex, OpenRouter, Hugging Face**, or any
  OpenAI-compatible endpoint (including local models).
- **Remember** every session, let you **resume** it later, and **branch** from
  any earlier point to explore a different path.
- Stay usable in long sessions by **compacting** context automatically, and
  expose **thinking modes** on models that support them.
- Extend itself with your own **skills**, **prompt templates**, and per-project
  **instructions** (`AGENTS.md`).

## Who is it for?

- **You want a coding agent you can run and shape** — install it, point it at a
  model, and work. Start with the [Quickstart](/quickstart/).
- **You want to understand how coding agents are built** — read the
  [core concepts](/concepts/), then the [How Tau works](/internals/architecture/)
  section.

## Where to go next

- **[Quickstart](/quickstart/)** — install Tau and run your first session in
  a few minutes.
- **[Core concepts](/concepts/)** — the handful of ideas (agent loop,
  providers, tools, sessions, skills) that everything else builds on.
- **[Guides](/guides/tui/)** — task-focused how-tos for the TUI, sessions,
  providers, and more.
- **[Reference](/reference/cli/)** — exact CLI commands, slash commands, and
  keyboard shortcuts.
