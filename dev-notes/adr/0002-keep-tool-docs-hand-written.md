---
title: "ADR 0002 — Keep built-in tool docs hand-written for now"
---

## Status

Accepted.

## Context

The built-in coding tools in `src/tau_coding/tools.py` have detailed Python
docstrings, and Tau also has beginner-friendly tool documentation in
`docs/03-tools.md`.

This creates some duplication. A generated API reference, for example through
`mkdocstrings`, could reduce drift between code and documentation by rendering
factory docstrings such as `create_read_tool_definition()` and
`create_bash_tool_definition()` directly into the docs site.

Tau's docs currently serve two different audiences:

- users and frontend authors who need to understand tool behavior, arguments,
  results, errors, and examples
- Python contributors who need API-level details about factory functions and
  implementation boundaries

## Decision

Keep `docs/03-tools.md` as the source of truth for user-facing built-in tool
behavior for now. Keep the Python docstrings as contributor-facing API
documentation, but do not add generated API pages to the MkDocs build yet.

Generated API documentation can be added later if Tau needs a formal API
reference. When that happens, it should supplement the conceptual docs rather
than replace them.

## Rationale

Hand-written tool docs are still the clearest way to explain behavior that is
important to agent users:

- which tool to choose for common coding tasks
- JSON argument shape and validation rules
- truncation behavior and continuation hints
- result metadata that TUI and logging layers may consume
- examples and practical error cases

Factory docstrings are useful, but they read like API reference material. They
do not replace examples, behavior notes, or the beginner-friendly explanation
that Tau needs while the architecture is still evolving.

Adding `mkdocstrings` now would also expand the documentation build surface:
new dependencies, plugin configuration, import-time behavior during strict docs
builds, and a new failure mode in CI. That tradeoff is not justified while the
public API is still small and the hand-written docs are explicit.

## Future API reference requirements

If Tau later adopts generated docstring documentation, the generated pages
should:

- live under a separate API Reference section
- include `ToolDefinition`, `create_coding_tools()`, and individual factory
  functions such as `create_read_tool_definition()`
- avoid replacing `docs/03-tools.md`
- keep examples and behavior notes in hand-written conceptual pages
- run in `mkdocs build --strict` without importing provider configuration,
  reading user files, or requiring optional runtime setup

## Consequences

- Tool behavior changes must update both relevant docstrings and
  `docs/03-tools.md` when the user-visible contract changes.
- CI stays simple because the docs build remains markdown-only.
- A future generated API reference can be introduced as a separate docs feature
  without changing the current user docs structure.
