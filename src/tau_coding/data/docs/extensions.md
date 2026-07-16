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
- `tau -x PATH`: explicitly load a file or directory.

An extension defines `setup(tau)`. Project extensions execute arbitrary Python and are disabled by default; enable only trusted repositories.

## Development checklist

1. Confirm the requested capability exists in the extension API before inventing a workaround.
2. Keep extension behavior out of `tau_agent`; extensions belong to `tau_coding`.
3. Use `tau_agent` types for portable messages and tools, and keep Textual behind Tau's UI adapter APIs.
4. Start from the closest installed example.
5. Add deterministic tests with fake providers/tools when changing Tau's extension implementation.
6. Run the repository's documented tests, Ruff checks, formatting, and mypy.
