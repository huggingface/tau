---
title: "Phase 6: Non-interactive Print-mode CLI"
---

Phase 6 wired Tau's provider layer, agent harness, and built-in coding tools into
a usable non-interactive CLI. Later phases moved print mode onto the
`CodingSession` wrapper so it shares the same coding-agent environment as the
TUI.

The CLI entry point lives in:

```text
src/tau_coding/cli.py
```

## What was added

The `tau` command can run a single prompt in print mode with `-p`/`--prompt`:

```bash
tau -p "write tests for main.py"
tau --prompt "explain this repo"
tau --model gpt-5.5 -p "summarize README.md"
```

Without `-p`/`--prompt`, Tau starts the interactive TUI. A positional prompt is
treated as the initial TUI prompt:

```bash
tau
tau "explain this repo"
```

The original Phase 6 command:

1. loads OpenAI-compatible provider settings from the environment
2. creates Tau's built-in coding tools
3. builds a minimal default system prompt
4. creates an `AgentHarness`
5. streams assistant text to stdout
6. prints tool execution summaries to stderr

## Provider configuration

Print mode uses Tau's configured provider settings.

If no provider login has been saved, the default OpenAI-compatible provider
expects:

```bash
export OPENAI_API_KEY="..."
```

Optional environment:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
```

The default model is currently:

```text
gpt-5.5
```

Use `--model` to choose another model supported by the configured endpoint.

## Working directory

Tools are rooted at the current working directory by default.

Use `--cwd` to run tools somewhere else:

```bash
tau --cwd /path/to/project -p "inspect the tests"
```

## Current behavior

Print mode now composes the higher-level `CodingSession` environment. A one-shot
run still remains non-interactive and script-friendly, but it now shares:

- full Tau system prompt assembly
- discovered project instructions
- loaded skills and prompt templates
- built-in coding tools
- append-only session persistence under `~/.tau/sessions/`
- the same provider/model resolution used by the TUI
- configured provider timeout and retry settings
- Rich-backed transcript rendering for tool activity

This keeps the user-facing command simple while reducing drift between print
mode and interactive mode.

## Historical minimal system prompt

Phase 6 originally built a small prompt containing:

- Tau's identity
- the list of available tools
- each tool's `prompt_snippet`
- each tool's `prompt_guidelines`

That was enough for the first CLI slice. The current implementation uses the
shared system prompt builder instead.

## Boundary

The CLI lives in `tau_coding`. It depends on provider settings,
`CodingSession`, and renderers, but the reusable `tau_agent` package still has
no CLI, Rich, Textual, config-file, or session-storage dependency.

## Tests

The phase is covered by `tests/test_cli.py`, including:

- `tau --version`
- default TUI launch when no print-mode prompt is provided
- positional prompts launching the TUI as initial prompts
- shared system prompt contents
- print-mode streaming with a fake provider
- print-mode session persistence
- print-mode skill expansion

## Next phase

The next roadmap phase is append-only session tree persistence.
