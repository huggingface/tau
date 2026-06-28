---
title: Quickstart
description: Install Tau, connect a model, and run your first coding session.
---

This page takes you from nothing to your first Tau session. It should take a few
minutes.

## 1. Install Tau

Tau is a Python tool. The easiest way to install it is with
[`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install tau-ai
```

Check it worked:

```bash
tau --version
```

:::tip[Don't have uv?]
`uv` is a fast Python package manager. Install it with
`curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or see the
[uv install docs](https://docs.astral.sh/uv/getting-started/installation/).
:::

## 2. Connect a model

Tau needs an AI model to talk to. A **provider** is the service that hosts the
model (OpenAI, Anthropic, …). Start Tau and use `/login` to connect one:

```bash
tau
```

Then run one of these inside Tau:

```text
/login              # choose a provider
/login openai       # save an OpenAI API key
/login openai-codex # authenticate a Codex/ChatGPT subscription
```

Tau ships with built-in entries for OpenAI, Anthropic, OpenAI Codex,
OpenRouter, and Hugging Face. See [Providers & models](./guides/providers-and-models.md)
for switching models or adding a custom/local OpenAI-compatible endpoint.

## 3. Start a session

Run Tau from inside the project you want to work on:

```bash
cd my-project
tau
```

This opens the interactive terminal UI. Type a request and press **Enter**:

```text
explain what this project does
```

Tau streams its response, and when it needs to, it reads files and runs commands
to answer you. Try something that changes code:

```text
add a docstring to every function in src/utils.py
```

You'll see each tool call (read, edit, bash) as it happens.

:::tip[Useful first keys]
**Enter** submits · **Esc** cancels the current run · **Ctrl+K** opens the
command palette · **Ctrl+D** quits. Full list in
[Keyboard shortcuts](./reference/keybindings.md).
:::

## 4. Come back later

Tau saves every session. List them:

```bash
tau sessions
```

Resume the most recent one for this directory, or pick from a list:

```bash
tau --resume <session-id>
```

…or open the picker inside the TUI with `/resume`. See
[Sessions](./guides/sessions.md) for resuming, branching, and exporting.

## One-shot mode

Don't need the UI? Run a single prompt and get the result on stdout — handy for
scripts and pipes:

```bash
tau -p "summarize the changes in the last commit"
```

More in [Print mode & scripting](./guides/print-mode.md).

## Where to go next

- **[Core concepts](./concepts.md)** — understand what's actually happening.
- **[The interactive session](./guides/tui.md)** — get fluent in the TUI.
- **[Providers & models](./guides/providers-and-models.md)** — switch models,
  add providers, use local models.
