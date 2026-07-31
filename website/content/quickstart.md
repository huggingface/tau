---
title: Quickstart
description: Install Tau, connect a model, and run your first coding session.
type: doc
---

This page takes you from nothing to your first Tau session. It should take a few
minutes.

## 1. Install Tau

Tau is a Python tool requiring Python 3.12 or newer. Its installer uses
[`uv`](https://docs.astral.sh/uv/) to create an isolated environment and installs
`uv` first when it is not already available.

On macOS or Linux, run:

```bash
curl -LsSf https://twotimespi.dev/install.sh | sh
```

On Windows, run in PowerShell:

```powershell
irm https://twotimespi.dev/install.ps1 | iex
```

The installer announces before installing `uv`, never uses `sudo`, verifies the
installed `tau` command, and tells you if you need to restart your shell for a
`PATH` update. To review code before executing it, download and inspect
[`install.sh`](/install.sh) or [`install.ps1`](/install.ps1) first.

Check it worked:

```bash
tau --version
```

{{% tip title="Already have a package manager?" %}}
Install Tau directly with `uv tool install tau-ai`, `pipx install tau-ai`, or
`python -m pip install tau-ai`.
{{% /tip %}}

### Upgrade Tau

For a normal install, let Tau detect and reuse the installer that owns its environment:

```bash
tau update
```

Tau reuses uv or pipx when their environment receipt is present. For uv tools,
it installs the latest stable version explicitly so an older version pin cannot
block the update. For ordinary Python installs, standard package metadata tells
Tau whether to use uv or pip, and Tau targets the exact Python environment
running it. It stops instead of
switching installers for editable, direct-URL, Conda/Pixi, or unrecognized
installations.

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
tau --session <session-id>
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
