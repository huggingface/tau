---
title: "Phase 16: Robust Resource Discovery"
---

Phase 16 makes skill and prompt-template discovery tolerant enough for real user
resource directories.

The implementation lives in:

```text
src/tau_coding/resources.py
src/tau_coding/skills.py
src/tau_coding/prompt_templates.py
src/tau_coding/session.py
src/tau_coding/commands.py
```

## What was added

Tau now has a `ResourceDiagnostic` type for non-fatal resource discovery notes.
It records:

- the resource kind, such as `skill` or `prompt`
- the affected resource name when known
- the path when known
- a short message
- a severity string

Skills and prompt templates now have diagnostic loaders:

```python
load_skills_with_diagnostics(...)
load_prompt_templates_with_diagnostics(...)
```

The older strict loaders still exist for callers that want discovery errors to
raise `ResourceError`.

## Discovery behavior

Resource directories are still loaded in increasing precedence order:

1. user Tau resources
2. user `.agents` resources
3. project Tau resources
4. project `.agents` resources

If a higher-precedence resource has the same name as a lower-precedence
resource, the higher-precedence resource wins. The diagnostic loaders report the
override instead of hiding it.

If one directory contains two skills with the same name, Tau keeps the first
deterministic match and reports a duplicate-name diagnostic. This prevents one
bad local resource from stopping the TUI from opening.

## Coding session integration

`CodingSession.load()` uses the diagnostic loaders. Loaded diagnostics are
available on:

```python
session.resource_diagnostics
```

That keeps discovery diagnostics in `tau_coding`, where local resources,
commands, and UI behavior live. The reusable `tau_agent` package remains
independent of resource paths and markdown discovery.

## Command and TUI visibility

The slash-command registry now includes:

```text
/resources
```

It shows how many skills and prompt templates loaded, plus any discovery
diagnostics. `/status` also includes a resource diagnostic count, and `/skills`
shows skill diagnostics when present.

The Textual TUI does not need a special diagnostics API. It already renders
command results as transcript items, so `/resources` surfaces the same
information interactively.

## Tests

The phase is covered by:

```text
tests/test_skills.py
tests/test_prompt_templates.py
tests/test_commands.py
tests/test_coding_session.py
```

The tests verify:

- deterministic override handling
- duplicate skill diagnostics
- tolerant `CodingSession` loading
- `/status` resource diagnostic counts
- `/resources` command output
