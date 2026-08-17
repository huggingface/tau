# Skill `disable-model-invocation` flag

## What changed

Skills can now opt out of being advertised to the model. Adding
`disable-model-invocation: true` to a `SKILL.md` frontmatter keeps the skill
loaded and usable via `/skill:<name>`, but removes it from the
`<available_skills>` block in the system prompt so the model never sees it and
cannot trigger it on its own.

```md
---
description: Cut a release. Only run when explicitly asked.
disable-model-invocation: true
---

Steps to prepare and publish a release...
```

Behavior matrix:

| Frontmatter | Listed in system prompt | `/skill:<name>` works |
| --- | --- | --- |
| (none) | yes | yes |
| `disable-model-invocation: true` | no | yes |
| `disable-model-invocation: false` | yes | yes |

The field defaults to `False`, so existing skills are unaffected.

## Why it exists

Every loaded skill is normally listed in the system prompt, which invites the
model to read and apply any of them proactively. Some skills are user-only:
workflows with side effects (releases, deployments, sending messages) or
sequences that should run strictly on request. This flag lets an author keep
such a skill discoverable and invocable by the user while withholding it from
the model's autonomous toolbox.

## Architecture notes

The change stays entirely inside `tau_coding`, consistent with the layer
boundary that skills and resource loading live there:

- `Skill` gains a `disable_model_invocation: bool = False` field
  (`src/tau_coding/skills.py`). It is parsed from the `disable-model-invocation`
  frontmatter key via the existing `parse_markdown_resource()` helper — no new
  parsing machinery.
- `build_skill_index()` and `format_skills_for_prompt()`
  (`src/tau_coding/skills.py`, `src/tau_coding/system_prompt.py`) filter out
  flagged skills before formatting, so the model-facing surfaces exclude them. A
  directory of only-disabled skills yields no `<available_skills>` block.
- `expand_skill_command()` is unchanged: it resolves names against the full
  loaded set, so `/skill:<name>` keeps expanding disabled skills.

No changes touch `tau_agent` or `tau_ai`; the harness is unaware of the flag.

## How to test

Automated checks:

```bash
uv run pytest tests/test_skills.py tests/test_system_prompt.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Manual check:

1. Create `.tau/skills/secret/SKILL.md` with `disable-model-invocation: true`
   in its frontmatter.
2. Run `uv run tau`.
3. Confirm the skill is absent from the system prompt / model-facing skill list.
4. Run `/skill:secret` and confirm it still expands and runs.
