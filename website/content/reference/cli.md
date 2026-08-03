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
- `-p/--print` (or `--mode`) runs that same positional prompt in [print mode]({{< relref "../guides/print-mode.md" >}}) instead of the TUI.
- Put flags before the prompt — Tau treats everything after the last recognized flag as prompt text, including tokens that look like flags.

On TUI and text print-mode startup, Tau may show a non-blocking notice when a
newer `tau-ai` release is available on PyPI. In the TUI, this notice is the first
transcript item and appears in bright yellow. Run `tau update` to upgrade. Disable
the check with `TAU_NO_UPDATE_CHECK=1`; utility commands such as `tau --version`,
`tau update`, `tau sessions`, and `tau export` do not run it. After an upgrade,
the TUI also adds a one-time release-notes message to the transcript with the new
features and fixes.

## Commands

| Command | What it does |
| --- | --- |
| `tau` | Open the interactive TUI |
| `tau "<prompt>"` | Open the TUI with an initial prompt |
| `tau update` | Upgrade Tau with the installer that owns its environment. Windows uv-tool updates are handed off and begin after Tau exits; follow the printed log path for the final result. |
| `tau sessions` | List indexed sessions (id, title, model, cwd) |
| `tau export <ref> [dest] [--format html\|jsonl]` | Export a session id or JSONL path (HTML default) |
| `tau --export <ref> [dest]` | Same as `tau export`, as a top-level flag |
| `tau providers` | List configured providers and how each authenticates |
| `tau [setup options] setup` | Create/update an OpenAI-compatible provider |

## Options

| Flag | Description |
| --- | --- |
| `-p, --print` | Run the positional prompt in non-interactive print mode |
| `-m, --model TEXT` | Model to request from the provider |
| `--provider TEXT` | Configured provider name to use |
| `--cwd PATH` | Working directory for the built-in tools |
| `--mode [text\|json\|transcript]` | Output mode for print mode (default `text`); also triggers print mode on its own |
| `--session TEXT` | Resume a session id in the TUI |
| `--new-session` | Start a new session instead of resuming the default |
| `--session-id TEXT` | Set the exact id for a newly created print-mode session; errors if it already exists |
| `--system-prompt TEXT_OR_PATH` | Replace Tau's default system-prompt base with literal text or an existing UTF-8 file |
| `--append-system-prompt TEXT_OR_PATH` | Append literal text or an existing UTF-8 file (repeatable) |
| `--auto-compact-threshold INT` | Auto-compact above this rough token estimate |
| `-e, --extension PATH` | Load an [extension]({{< relref "../guides/extensions.md" >}}) file or directory (repeatable) |
| `--no-extensions` | Disable extension directory discovery (explicit `-e` paths still load) |
| `--project-extensions` | Also load `<project>/.tau/extensions` (runs project-supplied code at startup) |
| `-v, --version` | Print the version and exit |

### System prompt input

`--system-prompt` replaces Tau's default base prompt. Repeat
`--append-system-prompt` to add sections in command-line order; Tau separates each
resolved value with exactly one blank line. Put these flags before the positional
prompt, like other recognized options:

```bash
tau --system-prompt "You are a focused reviewer." \
  --append-system-prompt ./team-rules.md \
  --append-system-prompt "Report risky changes first." \
  -p "review this repository"
```

For either option, Tau reads the value as a UTF-8 file when that path exists.
Otherwise it uses the value verbatim, so a nonexistent path is literal prompt
text. Existing directories, unreadable files, and invalid UTF-8 files stop
startup with an error naming the option and path. `~` is expanded when checking
for a file.

A custom base still receives appended text, discovered project instructions,
eligible skills when the `read` tool is enabled, the current date, and the
working directory. The options apply to print mode and interactive startup;
when used with `--session`, they configure the resumed session's next provider
request. They are startup controls and are not stored in session history, so
pass them again on a later resume when needed.

Without flags, Tau also discovers `SYSTEM.md` and `APPEND_SYSTEM.md` under the
project or user `.tau` directory. CLI values win over project files, and project
files win over user files. Use `/reload` after changing a file. These are
Tau-specific configuration files, not `.agents` resources. See
[Configuration & files]({{< relref "./configuration.md#system-prompt-files" >}})
for paths, precedence, diagnostics, and the project-resource security warning.

`--resume`, `--prompt`, `-o/--output`, and `-x` are removed; each now exits
with an error naming its replacement (`--session`, `--print`, `--mode`, and
`-e/--extension`, respectively).

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
