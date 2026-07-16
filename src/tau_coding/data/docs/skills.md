# Tau skills and prompt templates

Skills provide reusable task knowledge. Prompt templates save prompts that users invoke by name.

## Skills

A skill follows the Agent Skills structure:

```text
<skills-dir>/<skill-name>/SKILL.md
```

Tau loads skills in increasing precedence:

1. first-party skills bundled with Tau
2. `~/.tau/skills/`
3. `~/.agents/skills/`
4. `<cwd>/.tau/skills/`
5. `<cwd>/.agents/skills/`

A higher-precedence skill with the same name overrides the lower one. Tau places only each skill's name, description, and path in the system prompt; the model reads the full file when its description matches the task. Use `/skill:<name>` for explicit invocation.

## Prompt templates

Templates load from user and project `.tau/prompts/` and `.agents/prompts/` directories. They are prompt shortcuts, not background knowledge, and may contain `{{ variable }}` placeholders.

Use a skill for reference know-how and a template for a frequently repeated prompt. Run `/reload` after changing resources in an active TUI session.

When modifying Tau's resource system, read `src/tau_coding/skills.py`, `src/tau_coding/resources.py`, and `website/content/guides/skills-and-prompts.md`, then test discovery, precedence, diagnostics, prompt formatting, and reload behavior.
