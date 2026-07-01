---
title: Slash commands
description: Every in-session slash command in the Tau TUI.
---

Type these inside the interactive [TUI](../guides/tui.md). Open the searchable
command palette with **Ctrl+K**.

| Command | Description |
| --- | --- |
| `/quit` | Exit the session |
| `/new` | Start a new session |
| `/session` | Show session info and stats (model, cwd, tools, skills, context) |
| `/system` | Show the active system prompt without adding it to context or session history |
| `/compact [instructions]` | Summarize and compact the active context |
| `/export [--format html\|jsonl] [dest]` | Export the current session |
| `/resume [session-id]` | Resume a previous session, or open the picker |
| `/tree` | Branch from an earlier point in the session tree |
| `/name <new name>` | Rename the current session |
| `/model` | Open the model picker |
| `/scoped-models` | Choose favorite models for the Ctrl+P quick-cycle |
| `/theme [name]` | Show or set the TUI theme |
| `/login [provider]` | Save credentials for a built-in provider |
| `/logout [provider]` | Remove saved credentials for a provider |
| `/reload` | Reload local skills, prompts, and project context |
| `/hotkeys` | Show the keyboard shortcuts |
| `/skill:<name> [request]` | Expand a loaded skill into your prompt |

:::note[`/skill:` is special]
`/skill:<name>` is a *prompt-expansion* path, not a normal command — Tau expands
the named skill into your prompt and runs it as a turn. See
[Skills & prompt templates](../guides/skills-and-prompts.md).
:::

Related:

- **Thinking mode** is keyboard-driven, not a slash command — see
  [Keyboard shortcuts](./keybindings.md) and [Managing context](../guides/context.md#thinking-modes).
- **Prompt templates** are invoked by filename (e.g. `wt …`), not with a slash.
