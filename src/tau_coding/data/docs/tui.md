# Tau TUI

Tau's default full interactive interface uses Textual behind an adapter boundary. A minimalist Rich + prompt_toolkit frontend is available with `tau --tui rich`. Both consume provider-neutral events from `tau_agent`; frontend policy stays in `tau_coding`.

For current behavior in a Tau checkout, read:

- `website/content/guides/tui.md`
- `website/content/reference/keybindings.md`
- `src/tau_coding/tui/`

Do not introduce Textual dependencies into `tau_agent`. Keep reusable behavior in the harness/session layers and UI behavior in the adapter. Use Textual pilot tests and fake providers for deterministic interaction tests.
