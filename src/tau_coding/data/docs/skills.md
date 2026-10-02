# Tau skills and prompt templates

Skills provide reusable task knowledge. Prompt templates save prompts that users invoke by name.

## Skills

A skill follows the Agent Skills structure:

```text
<skills-dir>/<skill-name>/SKILL.md
```

Tau loads user and project skills in increasing precedence:

1. `~/.tau/skills/`
2. `~/.agents/skills/`
3. `<cwd>/.tau/skills/`
4. `<cwd>/.agents/skills/`

Tau's own product knowledge is regular packaged documentation, not a built-in skill, so it does not appear in the user's skill list or compete with user skill names.

A higher-precedence skill with the same name overrides the lower one. Tau places only each skill's name, description, and path in the system prompt; the model reads the full file when its description matches the task. Use `/skill:<name>` for explicit invocation.

A skill with `disable-model-invocation: true` in its `SKILL.md` frontmatter is excluded from the system prompt entirely, so the model cannot invoke it on its own. The skill stays loaded and remains available through explicit `/skill:<name>` invocation and the `/skills` picker.

## Learned lessons

`/learn` writes durable lessons in the same skill-file layout under a
dedicated namespace:

```text
~/.tau/lessons/<lesson-name>/SKILL.md
```

Lessons are deliberately not loaded by the skill loader: they live outside
`~/.tau/skills/` so they cannot shadow user skill names. Instead, each
session's system prompt carries a learned-context section listing every
lesson's name, description, and absolute path, and the model reads the
lesson file when a task matches it. Lesson files are additive-or-replace
(curator updates rewrite the same file; nothing deletes lessons) and their
names are sanitized to a filesystem-safe slug capped at 48 characters.

## Prompt templates

Templates load from user and project `.tau/prompts/` and `.agents/prompts/` directories. They are prompt shortcuts, not background knowledge, and support Pi-compatible argument placeholders such as `$1`, `$@`, `$ARGUMENTS`, defaults, and slices. Legacy `{{ arguments }}` and `{{ args }}` placeholders remain supported.

Use a skill for reference know-how and a template for a frequently repeated prompt. Run `/reload` after changing resources in an active TUI session.

When modifying Tau's resource system, read `src/tau_coding/skills.py`, `src/tau_coding/resources.py`, and `website/content/guides/skills-and-prompts.md`, then test discovery, precedence, diagnostics, prompt formatting, and reload behavior.
