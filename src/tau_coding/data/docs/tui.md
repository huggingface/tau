# Tau TUI

Tau's full interactive interface uses Textual behind an adapter boundary. `tau_agent` emits provider-neutral events; `tau_coding.tui` consumes and renders them.

For current behavior in a Tau checkout, read:

- `website/content/guides/tui.md`
- `website/content/reference/keybindings.md`
- `src/tau_coding/tui/`

Do not introduce Textual dependencies into `tau_agent`. Keep reusable behavior in the harness/session layers and UI behavior in the adapter. Use Textual pilot tests and fake providers for deterministic interaction tests.
