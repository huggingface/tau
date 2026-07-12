# Desktop Notifications for Tau

Fires a desktop notification when the agent finishes its turn and the terminal
is not focused (enabled via `notifications: true` in `~/.tau/tui.json`).

Uses **OSC 9** escape sequences — supported by Ghostty, iTerm2, Kitty, WezTerm,
and Warp. A `\r\x1b[K` cleanup sequence appended after the notification prevents
text leakage on terminals without OSC 9 support (e.g. macOS Terminal.app).
The notification fires in `TauTuiApp._run_prompt` after the agent event stream
completes normally. Suppressed via Textual's `App.app_focus` when the terminal
is frontmost.

```bash
uv run pytest tests/test_notification.py tests/test_tui_config.py
```
