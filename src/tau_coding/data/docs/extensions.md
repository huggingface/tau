# Tau extensions

Tau extensions are Python modules that can register custom tools and slash commands, observe lifecycle events, intercept tool calls and results, show UI dialogs, and customize message rendering.

## Start here

For complete API documentation, read the repository's published guide when working in a Tau checkout:

- `website/content/guides/extensions.md`
- `dev-notes/architecture/phase-21-extensions.md`

Installed examples are under `examples/extensions/` next to these docs. Read the relevant example completely before implementing an extension.

## Locations

- `~/.tau/extensions/`: discovered by default.
- `<project>/.tau/extensions/`: enabled explicitly with `--project-extensions`.
- `tau -e PATH`: explicitly load a file or directory.

An extension defines `setup(tau)`. Project extensions execute arbitrary Python and are disabled by default; enable only trusted repositories.

## Development checklist

1. Read this document and the closest installed example under `examples/extensions/` completely before implementing.
2. In a Tau checkout, also read `website/content/guides/extensions.md` and the relevant public extension API implementation.
3. Confirm the requested capability exists in the extension API before inventing a workaround.
4. Define `setup(tau)` and use documented registration APIs; do not reach into private session or Textual internals.
5. Keep extension behavior out of `tau_agent`; extensions belong to `tau_coding`. Use `tau_agent` types for portable messages and tools, and keep Textual behind Tau's UI adapter APIs.
6. Put user extensions in `~/.tau/extensions/`. Project extensions require explicit trust through `--project-extensions`; never enable one from an untrusted repository. Use `tau -e PATH` for isolated testing.
7. Test through the real extension runtime so discovery, imports, and `setup` registration are exercised. For Tau core changes, add deterministic tests with fake providers/tools and cover reload and lifecycle behavior when applicable.
8. Run focused tests followed by the repository's full pytest, Ruff, formatting, and mypy checks.
9. Update `website/content/guides/extensions.md` and add a development note for user-facing architectural changes.
