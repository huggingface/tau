---
title: Core concepts
description: The handful of ideas behind Tau — agents, providers, tools, sessions, skills, context, and thinking.
type: doc
---

Tau is built from a small set of concepts. Understand these and the rest of the
docs (and the agent itself) will make sense.

## The agent loop

When you send a prompt, Tau runs an **agent loop**: it asks the model to respond,
streams that response, and when the model asks to use a **tool**, Tau runs the
tool, feeds the result back, and asks the model to continue — repeating until the
model has nothing left to do. That loop is the heart of every coding agent.
→ [How Tau works: the agent loop]({{< relref "./internals/agent-loop.md" >}})

## Providers and models

A **provider** is the service that hosts AI models (OpenAI, Anthropic, OpenAI
Codex, OpenRouter, Hugging Face, or any OpenAI-compatible endpoint). A **model**
is the specific brain you're talking to (e.g. `gpt-5.5`, `claude-sonnet-4-6`).
You pick a provider + model; you can switch either mid-session.
→ [Providers & models]({{< relref "./guides/providers-and-models.md" >}})

## Tools

**Tools** are the actions the agent can take in your project. Tau ships four:
`read`, `write`, `edit`, and `bash`. The model decides when to call them; Tau
executes them in your working directory and streams the results.
→ [Tools reference]({{< relref "./reference/tools.md" >}})

## Sessions

A **session** is one ongoing conversation plus everything the agent did in it.
Sessions are saved to disk as append-only files, so you can **resume** them
later. Because the history is a tree, you can also **branch** from any earlier
point to try a different direction, and **export** a session to HTML or JSONL.
→ [Sessions]({{< relref "./guides/sessions.md" >}})

## Project instructions (`AGENTS.md`)

An **`AGENTS.md`** file gives the agent standing instructions about *your*
project — conventions, gotchas, how to run tests. Tau discovers these files
automatically and folds them into the system prompt.
→ [Project instructions]({{< relref "./guides/project-instructions.md" >}})

## Skills and prompt templates

**Skills** are Markdown files describing how to do a specific task; you invoke
one with `/skill:<name>`. **Prompt templates** are reusable prompts you trigger
by name (with optional variables) instead of retyping. Both can live at the user
level (all projects) or per-project.
→ [Skills & prompt templates]({{< relref "./guides/skills-and-prompts.md" >}})

## Context and compaction

The model can only "see" a limited amount of text at once — its **context
window**. Long sessions fill it up. Tau estimates usage and **compacts**
automatically: it summarizes older messages so the conversation can keep going.
You can also compact on demand with `/compact`.
→ [Managing context]({{< relref "./guides/context.md" >}})

## Thinking modes

Some models can spend extra effort "thinking" before they answer. Tau exposes a
**thinking mode** (off → minimal → low → medium → high → xhigh) you can cycle
when the active model supports it, and optionally show the streamed reasoning.
→ [Managing context]({{< relref "./guides/context.md#thinking-modes" >}})

## Two interfaces

Tau runs as an **interactive TUI** (the default) or in **print mode** (`-p`) for
a single non-interactive prompt. Both share the same session environment.
→ [The interactive session]({{< relref "./guides/tui.md" >}}) · [Print mode]({{< relref "./guides/print-mode.md" >}})
