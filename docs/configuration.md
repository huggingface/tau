# Configuration and Files

Tau keeps durable application state in the user's home directory and reads
project-local instructions from the active working directory.

## Tau Home

The default Tau home is:

```text
~/.tau/
```

Important files and directories:

```text
~/.tau/providers.json
~/.tau/tui.json
~/.tau/sessions/
~/.tau/skills/
~/.tau/prompts/
~/.tau/AGENTS.md
```

Tau also reads user-level `.agents` resources:

```text
~/.agents/skills/
~/.agents/prompts/
~/.agents/AGENTS.md
```

## Provider Settings

Provider metadata is stored in:

```text
~/.tau/providers.json
```

Example:

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
      "headers": {
        "X-Provider-Header": "value"
      },
      "timeout_seconds": 120,
      "max_retries": 2,
      "max_retry_delay_seconds": 0.5
    }
  ],
  "scoped_models": [
    {"provider": "local", "model": "qwen"},
    {"provider": "local", "model": "llama"}
  ]
}
```

API keys and OAuth refresh credentials are not written to this file. Tau resolves
credentials in this order: stored API key or OAuth credential from
`~/.tau/credentials.json`, then the provider-specific environment variable named
by `api_key_env`. Built-in providers added through `/login` read their saved
credential using `credential_name`. Providers without a `credential_name`, such
as custom local providers, read the environment variable named by `api_key_env`.
`timeout_seconds` is optional and defaults to `60`; when present, it must be
greater than zero. `max_retries` defaults to `2`, and `max_retry_delay_seconds`
defaults to `1`; both must be zero or greater. Streaming renderers show retry
progress when Tau retries a transient provider failure.
`headers` is optional and must be an object with string keys and string values.
Tau sends these headers with provider requests, while keeping its own
authentication headers under runtime control.

OAuth-backed providers, such as `openai-codex`, store a structured credential
object in `~/.tau/credentials.json` and refresh expired access tokens before a
model request. Use `/login openai-codex` to authenticate with a Codex
subscription account.

For example, Hugging Face organization billing can be configured with:

```json
{
  "headers": {
    "X-HF-Bill-To": "my-org"
  }
}
```

Useful commands:

```bash
tau providers
tau --provider local --model qwen --timeout-seconds 120 --max-retries 2 setup
```

Inside the TUI:

```text
/model
/model qwen
/login
```

The optional `scoped_models` list stores favorite provider/model pairs for fast
TUI cycling. Open `/model`, highlight a model, and press `Space` to add or
remove it from the scoped list. Press `Tab` in the picker to show only scoped
models. Press `Ctrl+P` in the prompt to cycle through scoped models without
opening the picker.

## TUI Settings

The built-in Textual frontend reads optional settings from:

```text
~/.tau/tui.json
```

Example:

```json
{
  "theme": "high-contrast",
  "keybindings": {
    "cancel": "escape",
    "command_palette": "ctrl+j",
    "session_picker": "ctrl+r",
    "queue_follow_up": "alt+enter",
    "accept_completion": "f2",
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

The built-in themes are:

- `tau-dark`, the default Toad-inspired dark theme with subtle left-accent
  conversation rows.
- `tau-light`, a light theme using the same TUI components with light
  backgrounds and darker foreground colors.
- `high-contrast`, a sharper dark theme for brighter terminal contrast.

In the Textual TUI, run `/theme` to open an interactive theme picker. You can
also run `/theme tau-light`, `/theme tau-dark`, or `/theme high-contrast` to
switch directly. The selection is saved to `~/.tau/tui.json` and applies
immediately.

The built-in sidebar is responsive: Tau shows it on medium or larger terminal
windows and hides it automatically when the terminal is narrow or short.
When visible, it includes the active provider/model, thinking mode, loaded tools,
skills, prompt templates, and context files such as `AGENTS.md`.

The TUI uses Textual's built-in footer to show shortcut hints for prompt
submission, newlines, command/session pickers, thinking controls, queued
follow-ups, and prompt clear actions. The visible footer bindings switch when
autocomplete is open or an agent turn is running, so the bottom toolbar stays in
Textual's standard footer instead of using a separate custom hint row.

Assistant Markdown renders fenced code blocks with syntax highlighting when the
fence language is known. Unknown fence languages fall back to plain code
formatting so assistant output remains readable.

Any omitted keybinding uses the built-in default. Key names use Textual's key
syntax, such as `ctrl+k`, `tab`, `shift+tab`, `down`, `up`, and `f2`. Tau rejects unknown
themes, unknown keybinding names, empty keys, and duplicate assignments so
mistakes fail early instead of silently changing terminal behavior.

## Sessions

Tau stores sessions under:

```text
~/.tau/sessions/
```

Each working directory gets a readable, hash-stabilized subdirectory:

```text
~/.tau/sessions/<cleaned-path-suffix>-<short-hash>/
```

For example, `/Users/alejandro/repos/exploration/tau` becomes a name like
`home-repos-exploration-tau-a1b2c3`.

Session transcripts are append-only JSONL files. They preserve messages, model
changes, and the active leaf in the session tree. Session metadata is indexed in
the project subdirectory so interactive resume flows can focus on the current
working directory.

Useful commands:

```bash
tau sessions
tau --resume <session-id>
tau --new-session
tau export <session-id>
tau export <session-id> session.html
tau export <session-id> --format jsonl
```

`tau export` writes a standalone HTML file with the preserved session tree and
the storage-order transcript. The source can be an indexed session id or a path
to a JSONL session file. When no output path is provided, Tau writes the export
artifact to the current working directory. HTML is the default format; pass
`--format jsonl` or a `.jsonl` destination to export JSONL.

Inside the TUI:

```text
/resume
/tree
/name <new name>
/session
/theme [name]
/export [--format html|jsonl] [destination]
```

`/name <new name>` renames the current indexed session. The new name is shown
in the `/resume` picker and in session-id completions.

`/tree` opens the current session tree. Select an earlier entry with `Enter` to
continue from that point while preserving the existing branch. Press `S` instead
to add a branch summary for the messages left behind before Tau moves the active
leaf.

## Skills and Prompt Templates

Tau loads markdown skills from these locations in increasing precedence order:

```text
~/.tau/skills/
~/.agents/skills/
~/.agents/
<cwd>/.tau/skills/
<cwd>/.agents/skills/
<cwd>/.agents/
```

Prompt templates are loaded from:

```text
~/.tau/prompts/
~/.agents/prompts/
<cwd>/.tau/prompts/
<cwd>/.agents/prompts/
```

Project resources override user resources with the same name. Duplicate or
overridden resources are reported through diagnostics instead of preventing Tau
from starting.

Tau lists loaded skills in the system prompt so the model can read a relevant
skill file through the `read` tool.

## Project Context

Tau discovers instruction files and includes them in the generated system
prompt. The current discovery order is:

```text
~/.tau/AGENTS.md
~/.agents/AGENTS.md
<project root>/AGENTS.md
<project root>/.../<cwd>/AGENTS.md
<cwd>/.tau/AGENTS.md
<cwd>/.agents/AGENTS.md
```

The project root is the nearest ancestor containing a marker such as `.git`,
`pyproject.toml`, `uv.lock`, `setup.py`, or `package.json`.

Useful TUI commands:

```text
/reload
/theme
/theme tau-light
```

In the TUI, `Shift-Tab` cycles the active thinking mode by default. `Ctrl+T`
toggles display of streamed thinking/reasoning tokens when the active provider
emits them. Thinking tokens are hidden by default and can be remapped in
`~/.tau/tui.json` with the `toggle_thinking` keybinding.

Remap the thinking-mode cycle shortcut with the `thinking_cycle` keybinding.

While the agent is running in the TUI, `Enter` queues the prompt as steering for
the active run. `Alt-Enter` queues the prompt as a follow-up that waits until the
active run would otherwise stop. Remap the follow-up shortcut with
`queue_follow_up`. Press `Up` on an empty prompt while the agent is running to
edit the most recently queued follow-up; Tau removes it from the queue and puts
its text back in the prompt.

Input beginning with `!` runs a terminal command in the session working
directory and records the command plus output in the agent context. Input
beginning with `!!` runs the command and displays the output without adding it
to context/history. Terminal input commands use the same shell execution,
output truncation, failure reporting, and timeout assumptions as Tau's built-in
`bash` tool.

Thinking controls are model-aware. Tau enables them only when the active
provider configuration declares supported levels for the active model. Custom
OpenAI-compatible providers can opt in by adding `thinking_levels`,
`thinking_default`, and `thinking_parameter: "reasoning_effort"` to their
provider entry. Add `thinking_models` when only some configured models support
those levels.

Typing `@` in the TUI prompt opens file-reference suggestions for the current
session working directory. Suggestions include matching files and directories
from the project tree and insert paths such as `@src/app.py`. Tau intentionally
skips hidden paths and common generated/cache directories such as `.git`,
`.venv`, `node_modules`, `__pycache__`, `build`, and `dist`.

## Context Management

`/session` shows a rough context-size estimate:

```text
Estimated context tokens: <count>
Context token breakdown: system=<count>, messages=<count>, tools=<count>
Thinking mode: <mode>
```

Manual compaction is available inside the TUI:

```text
/compact <summary>
```

Tau can also compact automatically before a new TUI turn when the estimated
context exceeds an opt-in threshold:

```bash
tau --auto-compact-threshold 100000
```

Automatic compaction currently uses a deterministic extractive summary of prior
messages. It does not call a model to generate the summary yet.
