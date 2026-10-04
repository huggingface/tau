# Extension-owned system-prompt sections

Issue: https://github.com/huggingface/tau/issues/648

## What changed

Extensions can now call:

```python
tau.add_prompt_section(title, body)
```

The body is free-form Markdown suitable for paragraphs, lists, procedures, and
code blocks. A non-empty title renders as a level-two heading; `None` or a blank
title creates an unlabeled block. Sections follow CLI/resource append content
and retain extension load and registration order.

## Why

`add_prompt_guideline` intentionally contributes one flat bullet to Tau's
`Guidelines` section. It cannot represent structured instructions cleanly.
Always-on extension context also should not be moved into an optional skill or a
global `AGENTS.md` when it applies only while that extension is active.

## Design

`PromptSection` is a frontend-free system-prompt assembly value. The extension
runtime stores sections with their canonical source owner, just like standalone
guidelines. Failed setup removes partial registrations; reload and retirement
clear the outgoing generation. Empty bodies and multi-line titles produce
bounded extension diagnostics rather than malformed prompt markup.

`CodingSession` passes runtime sections through
`BuildSystemPromptOptions.extra_sections` after the existing
`append_system_prompt` value. Reload compares section snapshots so adding,
changing, or removing an extension section rebuilds the next-turn prompt.
An exact low-level `CodingSessionConfig.system` override remains exact and does
not receive generated extension contributions.

Pi currently supports more general per-turn system-prompt mutation through its
`before_agent_start` hook. Tau does not yet expose that broader context-rewriting
surface. This narrower registration API addresses the always-on structured
context use case while preserving deterministic startup/reload assembly and
source ownership.

## Validation

```bash
uv run pytest tests/test_system_prompt.py tests/test_extensions.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For manual validation, load `examples/extensions/prompt_section.py` with `tau -e`
and use `/system` to confirm its heading, paragraphs, and fenced command block
appear after any `APPEND_SYSTEM.md` content. Remove the extension or run `/reload`
after deleting its registration and confirm the section disappears.
