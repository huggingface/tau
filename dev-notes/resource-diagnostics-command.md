# Resource diagnostics command

Issue #459 exposes Tau's existing non-fatal resource diagnostics through a
built-in `/diagnostics` command. The command reads
`CodingSession.resource_diagnostics` when invoked, so its output reflects both
initial resource discovery and the latest `/reload`.

The shared command handler renders an explicit empty state or every diagnostic's
severity, resource kind, optional name, optional source path, and message. Print
mode writes the same text directly. The Textual frontend uses its existing
scrollable command-output modal, which already renders command messages with
markup disabled, so paths and error text containing Rich/Textual markup
characters remain literal.

`diagnostics.md` is a reserved prompt-template name. Ignoring a colliding
template prevents prompt expansion from shadowing the built-in command and
records a diagnostic explaining the collision.

Validate manually:

1. Add an invalid skill, prompt, context file, or extension.
2. Start Tau and run `/diagnostics`.
3. Confirm the modal shows the resource kind, source path, severity, and
   remediation message.
4. Fix the resource, run `/reload`, and run `/diagnostics` again.
5. Confirm the modal shows `No resource diagnostics.`

Automated coverage lives in `tests/test_commands.py`, `tests/test_cli.py`,
`tests/test_prompt_templates.py`, and `tests/test_tui_app.py`.
