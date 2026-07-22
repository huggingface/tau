---
title: CLI reference
description: Every Tau command-line command and flag.
---

The `tau` command launches the interactive TUI by default; subcommands and flags
cover everything else.

```text
tau [OPTIONS] [PROMPT] [COMMAND] [ARGS]
```

- With no arguments, `tau` opens the interactive [TUI]({{< relref "../guides/tui.md" >}}).
- A positional `PROMPT` opens the TUI and submits it as the first turn.
- `-p/--prompt` runs a single prompt in [print mode]({{< relref "../guides/print-mode.md" >}}).

On TUI and text print-mode startup, Tau may show a non-blocking notice when a
newer `tau-ai` release is available on PyPI. Disable it with
`TAU_NO_UPDATE_CHECK=1`; utility commands such as `tau --version`, `tau sessions`,
and `tau export` do not run the check. After an upgrade, the TUI also adds a
one-time release-notes message to the transcript with the new features and fixes.

## Commands

| Command | What it does |
| --- | --- |
| `tau` | Open the interactive TUI |
| `tau "<prompt>"` | Open the TUI with an initial prompt |
| `tau sessions` | List indexed sessions (id, title, model, cwd) |
| `tau export <ref> [dest] [--format html\|jsonl]` | Export a session id or JSONL path (HTML default) |
| `tau providers` | List configured providers and how each authenticates |
| `tau [setup options] setup` | Create/update an OpenAI-compatible provider |

## Options

| Flag | Description |
| --- | --- |
| `-p, --prompt TEXT` | Run this prompt in non-interactive print mode |
| `-m, --model TEXT` | Model to request from the provider |
| `--provider TEXT` | Configured provider name to use |
| `--cwd PATH` | Working directory for the built-in tools |
| `-o, --output [text\|json\|transcript]` | Output mode for print mode (default `text`) |
| `--resume TEXT` | Resume a session id in the TUI |
| `--new-session` | Start a new session instead of resuming the default |
| `--auto-compact-threshold INT` | Auto-compact above this rough token estimate |
| `-x, --extension PATH` | Load an [extension]({{< relref "../guides/extensions.md" >}}) file or directory (repeatable) |
| `--no-extensions` | Disable extension directory discovery (explicit `-x` paths still load) |
| `--project-extensions` | Also load `<project>/.tau/extensions` (runs project-supplied code at startup) |
| `--version` | Print the version and exit |

### Provider setup options

Tau's setup mode registers an OpenAI-compatible provider. Put these flags before the final `setup` argument:

| Flag | Default | Description |
| --- | --- | --- |
| `--provider TEXT` | `openai` | Provider name to create/update |
| `--model TEXT` | default model | Default model for the provider |
| `--base-url TEXT` | OpenAI URL | OpenAI-compatible base URL |
| `--api-key-env TEXT` | `OPENAI_API_KEY` | Env var holding the API key |
| `--timeout-seconds FLOAT` | `60.0` | HTTP timeout |
| `--max-retries INT` | `2` | Retry count for transient failures |
| `--max-retry-delay-seconds FLOAT` | `1.0` | Delay between retries |
| `--set-default / --no-set-default` | set-default | Make this the default provider |

Example:

```bash
tau --provider local \
  --base-url http://localhost:11434/v1 \
  --api-key-env LOCAL_API_KEY \
  --model qwen \
  setup
```

See also: [Slash commands]({{< relref "./slash-commands.md" >}}) (in-session) and
[Keyboard shortcuts]({{< relref "./keybindings.md" >}}).
