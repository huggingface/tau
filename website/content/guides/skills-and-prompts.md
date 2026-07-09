---
title: Skills & prompt templates
description: Teach Tau reusable know-how with skills, and stop retyping instructions with prompt templates.
---

Tau loads two kinds of reusable Markdown from disk: **skills** (how to do a
task) and **prompt templates** (a saved prompt you trigger by name). Both can
live at the user level (available everywhere) or inside a project.

## Where the files go

Skills are loaded from these locations, in increasing precedence (later
overrides earlier on name clashes):

```text
~/.tau/skills/
~/.agents/skills/
~/.agents/
<cwd>/.tau/skills/
<cwd>/.agents/skills/
<cwd>/.agents/
```

Prompt templates load from:

```text
~/.tau/prompts/
~/.agents/prompts/
<cwd>/.tau/prompts/
<cwd>/.agents/prompts/
```

After adding or editing files while the TUI is open, run **`/reload`** to
rediscover them. Duplicate/overridden resources are reported as diagnostics, not
fatal errors.

## Skills

A skill is a directory containing a `SKILL.md` file, following the
[Agent Skills spec](https://agentskills.io/specification#directory-structure).
The directory name is the skill name. Optional frontmatter gives it a
description:

```text
~/.tau/skills/security-review/SKILL.md
```

```md
---
description: Review a diff for security issues.
---

Steps to review the current diff for security problems...
```

Any supporting files (references, snippets) can live alongside `SKILL.md`
inside the same directory.

{{% tip %}}
Bare `.md` files at the root of a skills directory (for example
`~/.tau/skills/review.md`) are **not** loaded as skills. Tau will surface a
diagnostic telling you to move them into their own directory:

```bash
cd ~/.tau/skills
mkdir review && mv review.md review/SKILL.md
```

This matches the Agent Skills spec and applies uniformly across `.tau/` and
`.agents/` locations.
{{% /tip %}}

Tau lists loaded skills in the system prompt so the model knows they exist and
can read the full file (via the `read` tool) when relevant. Invoke one
explicitly:

```text
/skill:security-review check the changes on this branch
```

`/skill:<name>` is a *prompt-expansion* path — Tau expands the skill into your
prompt and runs it as a normal turn.

## Prompt templates

A prompt template is a saved prompt you trigger by its filename. For example
`~/.agents/prompts/wt.md` becomes the prompt `wt`. Templates can include
variables with `{{ name }}`:

```md
---
description: Implement a feature in an isolated git worktree.
---

Implement this feature safely in a new worktree:
{{ feature }}
```

If a template has no placeholders, your arguments are appended after a blank
line. Variables are filled from the arguments you pass when invoking it.

## Skill vs. prompt template — which?

- Use a **prompt template** when you keep typing the *same instructions* and
  just want a shortcut (with optional fill-in variables).
- Use a **skill** when you want to give the model *reference know-how* it can
  pull in when a task calls for it, invoked with `/skill:<name>`.

{{% tip %}}
Keep personal, cross-project helpers in `~/.agents/`. Keep project-specific ones
in the repo's `.tau/` or `.agents/` so they're shared with collaborators.
{{% /tip %}}
