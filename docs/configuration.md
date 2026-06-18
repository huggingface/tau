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
      "timeout_seconds": 120,
      "max_retries": 2,
      "max_retry_delay_seconds": 0.5
    }
  ]
}
```

API keys are not written to this file. Each provider entry names the environment
variable that should contain its API key. `timeout_seconds` is optional and
defaults to `60`; when present, it must be greater than zero. `max_retries`
defaults to `0`, and `max_retry_delay_seconds` defaults to `1`; both must be
zero or greater.

Useful commands:

```bash
tau providers
tau --provider local --model qwen --timeout-seconds 120 --max-retries 2 setup
```

Inside the TUI:

```text
/provider
/provider local
/model
/model qwen
```

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
    "session_picker": "ctrl+o",
    "accept_completion": "f2",
    "completion_next": "down",
    "completion_previous": "up",
    "quit": "ctrl+d"
  }
}
```

The built-in themes are:

- `tau-dark`, the default Toad-inspired dark theme with subtle left-accent
  conversation rows.
- `high-contrast`, a sharper dark theme for brighter terminal contrast.

The built-in sidebar is responsive: Tau shows it on medium or larger terminal
windows and hides it automatically when the terminal is narrow or short.

Any omitted keybinding uses the built-in default. Key names use Textual's key
syntax, such as `ctrl+k`, `tab`, `down`, `up`, and `f2`. Tau rejects unknown
themes, unknown keybinding names, empty keys, and duplicate assignments so
mistakes fail early instead of silently changing terminal behavior.

## Sessions

Tau indexes sessions under:

```text
~/.tau/sessions/
```

Session transcripts are append-only JSONL files. They preserve messages, model
changes, and the active leaf in the session tree.

Useful commands:

```bash
tau sessions
tau --resume <session-id>
tau --new-session
```

Inside the TUI:

```text
/sessions
/status
```

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

Useful TUI commands:

```text
/skills
/resources
/skill:<name> [request]
```

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
/context
/reload
```

## Context Management

`/status` shows a rough context-size estimate:

```text
Estimated context tokens: <count>
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
