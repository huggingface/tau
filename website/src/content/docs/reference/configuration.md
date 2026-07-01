---
title: Configuration & files
description: Where Tau stores state, and the shape of its config files.
---

Tau keeps durable state in your home directory (`~/.tau/`) and reads
project-local resources from your working directory. This page is a reference for
those locations and file formats.

## Tau home

```text
~/.tau/
├── providers.json      # configured providers
├── credentials.json    # saved API keys / OAuth tokens (private permissions)
├── settings.json       # general settings (e.g. shell command prefix)
├── tui.json            # TUI theme + keybindings
├── sessions/           # saved sessions, per project
├── skills/             # user-level skills
├── prompts/            # user-level prompt templates
├── AGENTS.md           # global project instructions
└── logs/               # diagnostics
```

Tau also reads user-level `.agents` resources: `~/.agents/skills/`,
`~/.agents/prompts/`, `~/.agents/AGENTS.md`.

Startup update checks cache their latest PyPI result in
`~/.tau/cache/update-check.json` and refresh at most once per day. Set
`TAU_NO_UPDATE_CHECK=1` to disable the check; Tau also skips it when `CI` is set.

## Providers

Provider metadata lives in `~/.tau/providers.json`:

```json
{
  "default_provider": "local",
  "providers": [
    {
      "name": "local",
      "type": "openai-compatible",
      "base_url": "http://localhost:11434/v1",
      "api_key_env": "LOCAL_API_KEY",
      "models": ["qwen", "llama"],
      "default_model": "qwen",
      "headers": { "X-Provider-Header": "value" },
      "timeout_seconds": 120,
      "max_retries": 2,
      "max_retry_delay_seconds": 0.5
    }
  ],
  "scoped_models": [
    { "provider": "local", "model": "qwen" }
  ]
}
```

- `headers` is optional (string→string). `timeout_seconds` defaults to `60`
  (> 0); `max_retries` defaults to `2`; `max_retry_delay_seconds` defaults to `1`
  (both ≥ 0).
- API keys and OAuth credentials are **not** stored here — they live in
  `~/.tau/credentials.json`. Resolution order: stored credential, then the env
  var named by `api_key_env`.
- `scoped_models` are favorites for the **Ctrl+P** quick-cycle.
- Custom models can declare thinking support with `thinking_levels`,
  `thinking_default`, `thinking_models`, and `thinking_parameter`
  (`"reasoning_effort"`, `"reasoning.effort"`, or `"anthropic.thinking"`).

Writes after `/login`, `/model`, or scoped-model changes reload the file first,
apply only the requested change, write atomically, and keep a `.bak` backup.

See the [Providers & models guide](../guides/providers-and-models.md) for usage.

## Shell settings

Tau runs shell commands in a **non-interactive** shell — both terminal-input
commands (`! gst`, `!! ll`) and the agent's `bash` tool. Non-interactive shells
don't load your aliases from `~/.zshrc` or `~/.bashrc`, and Tau deliberately
never reads those files (they can hold tokens and side effects).

To make your own aliases available, opt in with a `shellCommandPrefix` in
`~/.tau/settings.json` that loads a small Tau-specific alias file:

```bash
# ~/.tau/shell-aliases.bash
alias gst='git status'
alias ga='git add'
alias gc='git commit'
```

```json
{
  "shellCommandPrefix": "shopt -s expand_aliases\nsource ~/.tau/shell-aliases.bash"
}
```

Then start a new session and try `! gst`. Notes:

- Commands run through bash-style non-interactive execution, so keep aliases
  POSIX/bash-compatible (zsh-only syntax, functions, or interactive startup
  logic may not work).
- Changing `settings.json` affects **new** sessions; an already-running session
  keeps the prefix it started with.
- The snake_case key `shell_command_prefix` is also accepted.

## TUI settings

The built-in frontend reads optional settings from `~/.tau/tui.json`:

```json
{
  "theme": "high-contrast",
  "keybindings": {
    "cancel": "escape",
    "command_palette": "ctrl+k",
    "session_picker": "ctrl+r",
    "queue_follow_up": "alt+enter",
    "accept_completion": "tab",
    "completion_next": "down",
    "completion_previous": "up",
    "thinking_cycle": "shift+tab",
    "model_cycle": "ctrl+p",
    "toggle_thinking": "ctrl+t",
    "toggle_tool_results": "ctrl+o",
    "copy_message": "ctrl+c",
    "quit": "ctrl+d"
  }
}
```

Built-in themes: `tau-dark` (default), `tau-light`, `high-contrast`. Set one with
`/theme`. Keys use Textual syntax; omitted keys keep their defaults. Tau rejects
unknown themes/keybinding names, empty keys, and duplicate assignments. Full list
in [Keyboard shortcuts](./keybindings.md).

## Sessions

```text
~/.tau/sessions/<cleaned-path>-<short-hash>/
```

Each working directory gets its own subdirectory; transcripts are append-only
JSONL preserving messages, model changes, and the active leaf of the session
tree. Metadata is indexed per project. See the
[Sessions guide](../guides/sessions.md).

## Skills, prompts & project context

Resource discovery order (later overrides earlier) is documented in
[Skills & prompt templates](../guides/skills-and-prompts.md) and
[Project instructions](../guides/project-instructions.md). In short: user-level
`~/.tau` and `~/.agents`, then project-level `.tau` and `.agents`, with
`AGENTS.md` discovered from the project root down to your current directory.

## Context

`/session` reports a rough context estimate and breakdown. Auto-compaction
triggers near the model's context window minus a reserve; override per run with
`--auto-compact-threshold`. Details in [Managing context](../guides/context.md).
