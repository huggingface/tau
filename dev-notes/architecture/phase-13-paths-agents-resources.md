---
title: "Phase 13: Tau Home, Paths, and `.agents` Resources"
---

Phase 13 makes Tau's user/project filesystem locations explicit and starts loading `.agents` resources automatically for every normal Tau session.

The implementation lives in:

```text
src/tau_coding/paths.py
src/tau_coding/resources.py
src/tau_coding/skills.py
src/tau_coding/prompt_templates.py
```

## What was added

Tau now has a canonical path helper:

```python
TauPaths
```

It defines user-level locations such as:

```text
~/.tau/
~/.tau/sessions/
~/.tau/skills/
~/.tau/prompts/
~/.agents/
~/.agents/skills/
~/.agents/prompts/
```

and project-level locations such as:

```text
<project>/.tau/skills/
<project>/.tau/prompts/
<project>/.agents/
<project>/.agents/skills/
<project>/.agents/prompts/
```

## Session location

The early TUI default session path moved from project-local storage:

```text
<project>/.tau/sessions/default.jsonl
```

to user-home storage keyed by project path:

```text
~/.tau/sessions/<project-hash>/default.jsonl
```

This keeps JSONL transcripts in a consistent user-owned location while still separating sessions by project.

## Automatic `.agents` resources

TauResourcePaths now includes `.agents` directories by default.

When no cwd is supplied, Tau loads user resources from:

```text
~/.tau/skills/
~/.agents/skills/
~/.agents/
~/.tau/prompts/
~/.agents/prompts/
```

When a cwd is supplied, Tau also loads project resources from:

```text
<project>/.tau/skills/
<project>/.agents/skills/
<project>/.agents/
<project>/.tau/prompts/
<project>/.agents/prompts/
```

This makes `.agents` part of the normal Tau session environment, not an optional extension mechanism.

## Precedence

Resource directories are loaded in increasing precedence order:

1. user Tau resources
2. user `.agents` resources
3. project Tau resources
4. project `.agents` resources

If two directories define the same skill or prompt template name, the later/higher-precedence resource wins. This lets project resources override user defaults.

Duplicate names within the same directory remain invalid and raise `ResourceError`.

`AGENTS.md` files found directly inside `.agents` directories are not treated as skills. Project instruction discovery is reserved for a later context-discovery phase.

## CodingSession integration

`CodingSession.load()` now builds default resource paths with the session cwd:

```python
TauResourcePaths(cwd=config.cwd)
```

That means normal coding sessions automatically see both user and project `.agents` resources.

Print mode also passes cwd-aware resource paths when loading skills for system prompt assembly.

## Tests

The phase is covered by:

```text
tests/test_paths.py
tests/test_resources.py
tests/test_skills.py
tests/test_prompt_templates.py
tests/test_cli.py
tests/test_coding_session.py
```

The tests verify:

- canonical Tau path construction
- user-home project session paths
- `.agents` skill discovery
- `.agents` prompt discovery
- project resources overriding user resources
- `AGENTS.md` not being loaded as a skill
- isolated resource paths for deterministic tests

## Next phase

The next phase should add a real session manager and resume flow on top of these stable user-home session locations.
