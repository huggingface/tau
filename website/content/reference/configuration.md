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
├── catalog.toml        # optional provider/model catalog overlay
├── providers.json      # provider/model preferences
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

## Network proxies

Tau uses `httpx` for provider requests, OAuth token refreshes, and startup update
checks, so it honors standard proxy environment variables such as `HTTP_PROXY`,
`HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`.

SOCKS proxies are supported by the base installation. Use explicit schemes when
you can:

```bash
export ALL_PROXY=socks5://127.0.0.1:1080
# or, when proxy-side DNS resolution is required:
export ALL_PROXY=socks5h://127.0.0.1:1080
```

Tau also accepts the generic `socks://` form that some systems and tools set in
the environment. Before creating its own HTTP clients, Tau normalizes
`socks://...` to `socks5://...` because `httpx` does not recognize the generic
scheme directly.

This matters for users behind corporate proxies, VPNs, local tunnels, or
privacy/network-routing setups: without SOCKS support and normalization, Tau can
fail before making a model API request with an error like
`Unknown scheme for proxy URL URL('socks://...')`.

## Providers

Tau separates provider metadata from runtime preferences:

- `src/tau_coding/data/catalog.toml` ships the built-in provider/model catalog.
- `~/.tau/catalog.toml` optionally adds personal providers or overlays built-ins.
- `~/.tau/providers.json` stores runtime preferences such as the default provider,
  default model, scoped models, headers, and timeout/retry settings.

Tau intentionally reads catalog overlays only from the user-level
`~/.tau/catalog.toml`. There is no project-level `.tau/catalog.toml`, so cloning a
repository cannot silently redirect a provider's `base_url` or credentials to an
unexpected service.

### Provider catalog overlays

Add reusable custom provider definitions to `~/.tau/catalog.toml`:

```toml
schema_version = 1

[[providers]]
name = "local-gateway"
display_name = "Local Gateway"
kind = "openai-compatible"
base_url = "http://localhost:11434/v1"
api_key_env = "LOCAL_GATEWAY_API_KEY"
credential_name = "local-gateway"
models = ["qwen-coder"]
default_model = "qwen-coder"
docs_url = "https://example.test/local-gateway"

[providers.context_windows]
qwen-coder = 64000
```

Catalog entries support `kind` values of `openai-compatible`, `anthropic`, and
`openai-codex`. For most custom services, start with `openai-compatible`.

User catalog overlays can be partial when they use the same `name` as a built-in
provider. Scalar fields replace built-in values, `models` are merged with user
models first, `context_windows` are merged, and the thinking fields
(`thinking_levels`, `thinking_models`, `thinking_default`, `thinking_parameter`)
replace as a group when `thinking_levels` is present.

`catalog.toml` does not store runtime request options such as custom HTTP
headers, timeouts, or retry settings. Put those in `~/.tau/providers.json` on the
matching provider entry.

Invalid catalog files fail loudly. Tau rejects unknown keys, empty required
strings, empty model names, unsupported provider kinds, default models that are
not listed in `models`, `thinking_models` or `context_windows` entries for
unknown models, and non-positive or non-integer context-window values.

### Provider preferences

Provider preferences live in `~/.tau/providers.json`:

```json
{
  "default_provider": "local-gateway",
  "provider_preferences": {
    "local-gateway": {
      "default_model": "qwen-coder",
      "headers": { "X-Provider-Header": "value" },
      "thinking_defaults": { "qwen-coder": "low" },
      "timeout_seconds": 120,
      "max_retries": 2,
      "max_retry_delay_seconds": 0.5
    }
  },
  "scoped_models": [
    { "provider": "local-gateway", "model": "qwen-coder" }
  ]
}
```

- `provider_preferences` keys must refer to providers from the effective catalog
  (`src/tau_coding/data/catalog.toml` plus `~/.tau/catalog.toml`).
- `headers` is optional (string→string). For example, Hugging Face organization
  billing can be configured with `"headers": { "X-HF-Bill-To": "my-org" }` on
  the `huggingface` provider preference. `thinking_defaults` remembers the
  preferred thinking level per model for new sessions; resumed sessions still use
  their session history. `timeout_seconds` defaults to `60` (> 0); `max_retries`
  defaults to `2`; `max_retry_delay_seconds` defaults to `1` (both ≥ 0).
- API keys and OAuth credentials are **not** stored here — they live in
  `~/.tau/credentials.json`. Resolution order: stored credential, then the env
  var named by `api_key_env`.
- The selected model must be present in that provider's `models` list. Add
  custom or local model names to `models` before using them as defaults,
  CLI/TUI selections, or scoped models.
- `scoped_models` are favorites for the **Ctrl+P** quick-cycle.
- Older `providers.json` files that contain full `providers` entries are still
  accepted for compatibility. When Tau saves settings again, provider definitions
  are moved to `~/.tau/catalog.toml` and `providers.json` is rewritten as runtime
  preferences.
- Custom models declare thinking support in `catalog.toml` with
  `thinking_levels`, `thinking_default`, `thinking_models`, and
  `thinking_parameter` (`"reasoning_effort"`, `"reasoning.effort"`, or
  `"anthropic.thinking"`).

Writes after `/login`, `/model`, or scoped-model changes reload the file first,
apply only the requested change, write atomically, and keep a `.bak` backup.

See the [Providers & models guide]({{< relref "../guides/providers-and-models.md" >}}) for usage.

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
unknown themes/keybinding names, empty keys, and duplicate assignments.

- `sidebar_position`: `"left"` (default), `"right"`, or `"off"`. Controls
  placement of the session metadata sidebar. `"off"` hides the sidebar entirely;
  the compact session info row below the prompt still works.

Full list in [Keyboard shortcuts]({{< relref "./keybindings.md" >}}).

## Sessions

```text
~/.tau/sessions/<cleaned-path>-<short-hash>/
```

Each working directory gets its own subdirectory; transcripts are append-only
JSONL preserving messages, model changes, and the active leaf of the session
tree. Metadata is indexed per project. See the
[Sessions guide]({{< relref "../guides/sessions.md" >}}).

## Skills, prompts & project context

Resource discovery order (later overrides earlier) is documented in
[Skills & prompt templates]({{< relref "../guides/skills-and-prompts.md" >}}) and
[Project instructions]({{< relref "../guides/project-instructions.md" >}}). In short: user-level
`~/.tau` and `~/.agents`, then project-level `.tau` and `.agents`, with
`AGENTS.md` discovered from the project root down to your current directory.

## Context

`/session` reports a rough context estimate and breakdown. Auto-compaction
triggers near the model's context window minus a reserve; override per run with
`--auto-compact-threshold`. Details in [Managing context]({{< relref "../guides/context.md" >}}).
