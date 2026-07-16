---
name: create-tau-extension
description: Create or modify a Tau Python extension with custom tools, commands, hooks, dialogs, or message rendering. Use for Tau extension requests.
---

# Create a Tau extension

1. Read the installed `docs/extensions.md` and the closest example under `examples/extensions/` relative to Tau's packaged documentation paths in the system prompt.
2. In a Tau checkout, also read `website/content/guides/extensions.md` and the relevant extension API implementation before coding.
3. Put user extensions in `~/.tau/extensions/`; project extensions require explicit trust through `--project-extensions`. Use `tau -x PATH` for isolated testing.
4. Define `setup(tau)` and use documented registration APIs. Do not reach into private session or Textual internals.
5. Keep portable tool/message types in `tau_agent`; keep application and UI integration in `tau_coding`.
6. For Tau core changes, add deterministic tests with fake providers/tools and cover reload/lifecycle behavior when applicable.
7. Run relevant focused tests followed by `uv run pytest`, Ruff lint, Ruff format check, and mypy.
8. Update `website/content/guides/extensions.md` and add a development note for user-facing architectural changes.

Never enable an untrusted project extension: extensions execute arbitrary Python in the Tau process.
