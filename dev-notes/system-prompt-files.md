# Tau-native system prompt files

Issue: https://github.com/huggingface/tau/issues/531

## What changed

Tau now discovers optional replacement and append files:

```text
~/.tau/SYSTEM.md
~/.tau/APPEND_SYSTEM.md
<cwd>/.tau/SYSTEM.md
<cwd>/.tau/APPEND_SYSTEM.md
```

Explicit `--system-prompt` or `--append-system-prompt` input wins over discovery.
Otherwise a project file wins over its user counterpart. Replacement and append
precedence are independent, so a project replacement can be paired with a user
append. Project and user append files are alternatives rather than cumulative
layers.

The files use the existing custom-base prompt builder. Replacement content still
receives selected append text, project context, eligible skills, date, and cwd.
Prompt contents remain request configuration and are not added to session JSONL.

## Why `.agents` is excluded

Pi uses `.agents/skills` as a portable Agent Skills compatibility location, but
keeps `SYSTEM.md` and `APPEND_SYSTEM.md` under its native `.pi` configuration.
Tau follows the same boundary: existing `.agents` support for skills, templates,
and instructions is unchanged, while system prompt files remain Tau-specific.

## Reload and diagnostics

`CodingSession` stores the discovered content and source paths separately from
explicit startup values. `/reload` compares both source and content signatures,
so adding, changing, removing, or shadowing a file rebuilds the next-turn prompt.
Explicit startup values remain authoritative across reloads.

Resource diagnostics identify selected, shadowed, and CLI-overridden files
without exposing their contents. Missing files are ignored. A selected path that
cannot be inspected, read, or decoded as UTF-8 fails startup/reload rather than
silently weakening precedence. A failed reload leaves the previous prompt active.

## Trust boundary

Project prompt files load automatically in this phase, consistent with Tau's
current project-resource behavior. They can change the model's highest-priority
instructions, so published documentation tells users to inspect them. A unified
Pi-compatible project trust boundary is tracked separately in issue #535; prompt
file discovery is isolated in `tau_coding.resources` so that trust can later gate
project candidates without changing `tau_agent` or prompt assembly.

## Verification

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
cd website && hugo --minify
cd website && npx --yes pagefind@latest --site public
```
