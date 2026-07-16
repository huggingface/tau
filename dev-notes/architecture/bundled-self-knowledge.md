# Bundled Tau self-knowledge

Tau now follows Pi's progressive-disclosure approach to product knowledge. The default system prompt identifies installed documentation and examples, while first-party skills provide detailed contributor workflows only when a task matches.

## What changed

Packaged resources now live under `src/tau_coding/data/`:

```text
docs/       concise routing references for Tau topics
examples/   readable extension examples
skills/     first-party Agent Skills
```

The prompt mirrors Pi's documentation block, adapted to Tau's Python architecture and published concepts. It points to extensions, skills, models, CLI commands, TUI behavior, and architecture without injecting those documents into every request. The default guidelines also add general coding discipline for inspecting project instructions, preserving unrelated work, using repository-native commands, validating changes, reporting checks honestly, and asking before destructive or materially ambiguous operations.

Two skills ship initially:

- `create-tau-extension`
- `tau-model-catalog`

Bundled skills have the lowest precedence. User and project skills can override them by name, preserving Tau's existing resource precedence model.

## Why

The old prompt knew how to operate tools but did not tell the model where Tau's own APIs and workflows were documented. Repository `AGENTS.md` helped only when Tau was running inside its source checkout. Installed Tau sessions now retain product knowledge without bloating the base prompt, and general coding tasks remain governed by project context and task-specific skills.

## Architecture

Self-documentation belongs to `tau_coding`, not the portable `tau_agent` harness. `self_docs.py` resolves installed resource paths, `resources.py` includes bundled skills before filesystem resources, and `system_prompt.py` emits Pi-style routing hints. Hatch packages the resources as part of `tau_coding`.

## Verification

```bash
uv run pytest tests/test_system_prompt.py tests/test_skills.py tests/test_resources.py tests/test_package_metadata.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
cd website && hugo --minify && npx --yes pagefind@latest --site public
```
