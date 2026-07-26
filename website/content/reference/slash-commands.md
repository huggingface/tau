---
title: Slash commands
description: Every in-session slash command in the Tau TUI.
---

Type these inside the interactive [TUI]({{< relref "../guides/tui.md" >}}). Open the searchable
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
| `/name <new name>` | Rename the current session and, in supported terminals, the terminal tab title |
| `/model` | Open the model picker |
| `/tools` | Browse active tools and open their full descriptions |
| `/scoped-models` | Choose favorite models for the Ctrl+P quick-cycle |
| `/theme [name]` | Show or set the TUI theme |
| `/login [provider]` | Connect a built-in provider with OAuth or an API key; Anthropic uses `anthropic-subscription` or `anthropic-api` |
| `/logout [provider]` | Remove saved credentials for a provider |
| `/reload` | Reload local skills, prompts, extensions, and project context |
| `/prompts` | Search loaded prompt templates and insert an invocation for editing |
| `/hotkeys` | Show the keyboard shortcuts |
| `/skills` | Open a searchable picker of loaded skills and insert a selection into the prompt |
| `/skill:<name> [request]` | Expand a loaded skill into your prompt |

{{% note title="`/skill:` is special" %}}
`/skill:<name>` is a *prompt-expansion* path, not a normal command — Tau expands
the named skill into your prompt and runs it as a turn. See
[Skills & prompt templates]({{< relref "../guides/skills-and-prompts.md" >}}).
{{% /note %}}

Only registered commands are consumed locally. Other slash-prefixed input, including
absolute paths such as `/tmp` or `/Users/me/file.png`, is sent to the model as a normal
prompt.

Related:

- **Thinking mode** is keyboard-driven, not a slash command — see
  [Keyboard shortcuts]({{< relref "./keybindings.md" >}}) and [Managing context]({{< relref "../guides/context.md#thinking-modes" >}}).
- **Prompt templates** use slash invocations (for example, `/wt …`). Use `/prompts` to search loaded templates and insert an invocation without submitting it.
