---
title: Quickstart
description: Install Tau, connect a model, and run your first coding session.
type: doc
---

This page takes you from nothing to your first Tau session. It should take a few
minutes.

## 1. Install Tau

Tau is a Python tool. The easiest way to install it is with
[`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install tau-ai
```

Tau requires Python 3.12 or newer.

Check it worked:

```bash
tau --version
```

{{% tip title="Don't have uv?" %}}
You can install Tau with `pipx install tau-ai` or
`python -m pip install tau-ai`. If you prefer `uv`, install it with
`curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux), or see the
[uv install docs](https://docs.astral.sh/uv/getting-started/installation/).
{{% /tip %}}

### Upgrade Tau

For a normal tool install, upgrade with:

```bash
uv tool upgrade tau-ai
```

If you installed a local checkout with `uv tool install --editable .`, run the
install command again after pulling changes:

```bash
uv tool install --editable --force .
```

Editable installs expose source changes immediately, but installed package
metadata (including the version), dependencies, and entry points are refreshed
only when uv reinstalls the tool.

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
OpenRouter, and Hugging Face. See [Providers & models]({{< relref "./guides/providers-and-models.md" >}})
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

{{% tip title="Useful first keys" %}}
**Enter** submits · **Esc** cancels the current run · **Ctrl+K** opens the
command palette · **Ctrl+D** quits. Full list in
[Keyboard shortcuts]({{< relref "./reference/keybindings.md" >}}).
{{% /tip %}}

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
[Sessions]({{< relref "./guides/sessions.md" >}}) for resuming, branching, and exporting.

## One-shot mode

Don't need the UI? Run a single prompt and get the result on stdout — handy for
scripts and pipes:

```bash
tau -p "summarize the changes in the last commit"
```

More in [Print mode & scripting]({{< relref "./guides/print-mode.md" >}}).

## Where to go next

- **[Core concepts]({{< relref "./concepts.md" >}})** — understand what's actually happening.
- **[The interactive session]({{< relref "./guides/tui.md" >}})** — get fluent in the TUI.
- **[Providers & models]({{< relref "./guides/providers-and-models.md" >}})** — switch models,
  add providers, use local models.
