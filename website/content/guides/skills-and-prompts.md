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
<cwd>/.tau/skills/
<cwd>/.agents/skills/
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
fatal errors. At TUI startup, Tau also shows a red transcript alert when skills or
prompt templates in different locations share a name. The alert lists both paths
so you can rename or remove the unintended duplicate; Tau still uses the
higher-precedence resource.

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

Tau's own extension and provider workflows are packaged documentation rather
than built-in skills. They remain available to the agent without appearing in
your skill list, competing with your skill names, or being disabled by
`--no-skills`.

Tau lists loaded user and project skills in the system prompt so the model knows they exist and
can read the full file (via the `read` tool) when relevant. Run **`/skills`** to search names and
descriptions, then select one to insert its invocation into the prompt for further instructions.
In the picker, **F1** opens the complete header description and **Ctrl+Enter** displays the
full `SKILL.md` in the transcript for inspection without adding it to model context. Or invoke one
explicitly:

```text
/skill:security-review check the changes on this branch
```

For longer instructions, put the request on following lines:

```text
/skill:security-review

Check the changes on this branch.
Pay special attention to authentication boundaries.
```

`/skill:<name>` is a *prompt-expansion* path — Tau expands the skill and any
inline or multiline request into your prompt, then runs it as a normal turn.

## Prompt templates

A prompt template is a saved prompt you trigger by its filename. For example,
`~/.agents/prompts/wt.md` is invoked with `/wt`. Run `/prompts` in the TUI to
search every loaded template. Press **Enter** to insert its invocation without
submitting it, or **Ctrl+E** to edit its Markdown directly. Save with **Ctrl+S**;
Tau reloads resources automatically. The filenames `prompts.md` and `tools.md` are
reserved for built-in commands; Tau ignores templates with those names and
reports a resource diagnostic. Templates can include variables with `{{ name }}`:

```md
---
description: Implement a feature in an isolated git worktree.
---

Implement this feature safely in a new worktree:
{{ feature }}
```

If a template has no placeholders, your arguments are appended after a blank
line. Variables are filled from the arguments you pass after the invocation,
for example `/wt add caching`.

The filenames `prompts.md` and `skills.md` are reserved for the built-in `/prompts`
and `/skills` pickers. Tau ignores either template and reports a resource diagnostic;
rename the file to load it as a custom prompt.

## Skill vs. prompt template — which?

- Use a **prompt template** when you keep typing the *same instructions* and
  just want a shortcut (with optional fill-in variables).
- Use a **skill** when you want to give the model *reference know-how* it can
  pull in when a task calls for it, invoked with `/skill:<name>`.

{{% tip %}}
Keep personal, cross-project helpers in `~/.agents/`. Keep project-specific ones
in the repo's `.tau/` or `.agents/` so they're shared with collaborators.
{{% /tip %}}
