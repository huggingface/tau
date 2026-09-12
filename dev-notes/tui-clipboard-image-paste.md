# TUI clipboard image paste

## What was added

Tau's Textual prompt now handles `Ctrl+V` on macOS like Pi:

1. Read an image from the native clipboard through Pillow's `ImageGrab` support.
2. Save it under the system temporary directory as `tau-clipboard-<uuid>.png`.
3. Insert that absolute path at the prompt cursor using the same spacing rules as file drops.
4. Fall back to `pbpaste` text when the clipboard contains no image.

The shortcut is configured as `keybindings.paste_clipboard` in `~/.tau/tui.json` and defaults to `ctrl+v`.

## Why paths instead of inline prompt images

Tau's provider-neutral image flow already lives in the built-in `read` tool. Inserting a temporary path lets the agent inspect clipboard images through that existing validated pipeline, including model capability checks, image normalization, and provider serialization. The TUI does not need its own image-message type or terminal image renderer.

This preserves Tau's layer boundary:

- `tau_agent` remains independent of the operating system and Textual.
- `tau_coding.tui.clipboard` owns native clipboard access.
- `tau_coding.tools.read` remains responsible for model-facing image attachments.

## Platform scope

The first implementation is macOS-only. It uses Pillow's built-in macOS clipboard reader and `pbpaste` for the text fallback. Other platforms return no clipboard content rather than failing the TUI.

## Verification

```bash
uv run pytest tests/test_tui_clipboard.py tests/test_tui_app.py tests/test_tui_config.py
uv run ruff check src tests
uv run mypy
```

Manual macOS check:

1. Copy a screenshot to the clipboard.
2. Start Tau with `uv run tau`.
3. Focus the prompt and press `Ctrl+V`.
4. Confirm that a `/var/folders/.../T/tau-clipboard-<uuid>.png` path appears.
5. Ask Tau to inspect the screenshot and confirm the `read` tool attaches it for a vision-capable model.
