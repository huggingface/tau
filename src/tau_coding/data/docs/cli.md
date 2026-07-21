# Tau CLI and commands

Tau supports print mode and a Textual interactive TUI. The CLI entry point is `tau_coding.cli:app`.

For current user-facing behavior in a Tau checkout, read:

- `website/content/reference/cli.md`
- `website/content/reference/slash-commands.md`
- `src/tau_coding/commands.py`

Keep command parsing and application-specific resource loading in `tau_coding`, not the reusable `tau_agent` harness. When changing behavior, test both command results and the relevant print/TUI integration, then update published reference documentation.
