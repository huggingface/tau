---
title: "Phase 10: System Prompt Assembly"
---

Phase 10 adds Tau's canonical system prompt builder.

The implementation lives in:

```text
src/tau_coding/system_prompt.py
```

## What was added

Tau can now build a deterministic Pi-style system prompt from:

- Tau's default coding-agent identity
- enabled tools
- tool prompt snippets
- tool prompt guidelines
- extra guidelines
- optional custom prompt text
- optional appended prompt text
- project context files
- loaded skills
- current date
- current working directory

## Why this exists

Earlier phases added tools, skills, prompt templates, and coding sessions. Before this phase, the CLI used a small local prompt builder that only listed tools. Tau now has one shared prompt assembly layer that can be used by print mode, coding sessions, and later UI/session frontends.

## Default prompt shape

The default prompt starts with Tau's identity:

```text
You are an expert coding assistant operating inside Tau, a coding agent harness.
```

It then includes an available-tools section using each tool's `prompt_snippet`:

```text
Available tools:
- read: Read file contents
- write: Create or overwrite files
- edit: Make precise file edits ...
- bash: Execute bash commands ...
```

Then it includes guidelines gathered from enabled tools:

```text
Guidelines:
- Use bash for file operations like ls, rg, find
- Use read to examine files instead of cat or sed.
- Use write only for new files or complete rewrites.
- Be concise in your responses
- Show file paths clearly when working with files
```

Guidelines are de-duplicated in deterministic order.

The default prompt also mirrors Pi's progressive-disclosure documentation block,
adapted for Tau. It points to packaged Tau docs and examples for extensions,
skills, providers/models, commands, TUI behavior, and architecture, and tells the
model to read them only for Tau-specific questions. Custom prompts still replace
this default documentation block.

## Custom prompts

If `custom_prompt` is supplied, it replaces the default Tau identity, tools, and guidelines sections.

Tau still appends these after a custom prompt, matching Pi's behavior:

1. appended system prompt text
2. project context
3. skills section, if the `read` tool is enabled
4. current date
5. current working directory

## Project context

Project instructions are represented with `ProjectContextFile` values and formatted in Pi's XML-like style:

```xml
<project_context>

Project-specific instructions and guidelines:

<project_instructions path="/repo/AGENTS.md">
...content...
</project_instructions>

</project_context>
```

Phase 10 formats provided context files. Full discovery of files such as `AGENTS.md` and `CLAUDE.md` can be expanded later.

## Skills

Skills are formatted in Pi's XML-style skill index:

```xml
<available_skills>
  <skill>
    <name>python-testing</name>
    <description>Write and run Python tests.</description>
    <location>/home/user/.tau/skills/python-testing/SKILL.md</location>
  </skill>
</available_skills>
```

Tau includes the skills section only when the `read` tool is enabled. This mirrors Pi: the model should use `read` to load the full skill file when a task matches a skill description.

Prompt templates are not inserted into the system prompt. They are user prompt expansion resources.

## Integration

Print mode now uses `build_system_prompt()` instead of a local minimal prompt builder.

`CodingSessionConfig.system` is now optional. If omitted, `CodingSession.load()` builds a system prompt from:

- cwd
- configured tools or default coding tools
- loaded skills
- custom/append prompt options
- provided context files

Existing callers can still pass an explicit `system` string.

## Tests

The phase is covered by:

```text
tests/test_system_prompt.py
tests/test_cli.py
tests/test_coding_session.py
```

The tests verify:

- default prompt sections
- available tool formatting
- hidden tools without snippets
- guideline de-duplication
- custom prompt behavior
- project context formatting
- skill XML escaping
- skill inclusion only with `read`
- print-mode integration
- coding-session integration

## Next phase

The next roadmap phase is Rich console rendering. The system prompt builder introduced here gives future frontends a shared prompt assembly layer instead of duplicating prompt logic.
