# Tau TUI

Tau's full interactive interface uses Textual behind an adapter boundary. `tau_agent` emits provider-neutral events; `tau_coding.tui` consumes and renders them.

For current behavior in a Tau checkout, read:

- `website/content/guides/tui.md`
- `website/content/reference/keybindings.md`
- `src/tau_coding/tui/`

## Local backends

Type `/local` to open the generic local-backend host. It explicitly chooses a
registered backend, including when only one is available; a recommended backend
is merely preselected. Configure screens render backend-declared text, secret,
and choice fields without exposing backend UI objects to extension code.

The host supports asynchronous configure, refresh, status, use, doctor, reset,
and optional model actions. It displays structured status and progress, refuses
state-changing actions while the agent is busy, and cancels owned work when the
screen closes. Results from a replaced or retired extension generation are
ignored. Backends that do not declare an optional capability do not show its
control.

Do not introduce Textual dependencies into `tau_agent`. Keep reusable behavior in the harness/session layers and UI behavior in the adapter. Use Textual pilot tests and fake providers for deterministic interaction tests.
