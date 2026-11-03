"""Read macOS clipboard images or text for the TUI prompt."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageGrab

CLIPBOARD_TIMEOUT_SECONDS = 3

# Injectable seam used by PromptInput pilot tests.
type ClipboardReader = Callable[[], Awaitable[str | None]]


async def read_clipboard_for_prompt() -> str | None:
    """Return a temporary image path or clipboard text on supported platforms."""
    return await asyncio.to_thread(_read_clipboard_for_prompt_sync)


def _read_clipboard_for_prompt_sync() -> str | None:
    """Read macOS clipboard contents without blocking Textual's event loop."""
    if sys.platform != "darwin":
        return None

    image_path = materialize_clipboard_image()
    if image_path is not None:
        return str(image_path)
    return _read_macos_clipboard_text()


def materialize_clipboard_image(*, temp_dir: Path | None = None) -> Path | None:
    """Save a clipboard image as a temporary PNG and return its path."""
    try:
        clipboard = ImageGrab.grabclipboard()
    except (ChildProcessError, NotImplementedError, OSError):
        return None
    if not isinstance(clipboard, Image.Image):
        return None

    directory = temp_dir or Path(tempfile.gettempdir())
    path = directory / f"tau-clipboard-{uuid4()}.png"
    try:
        clipboard.save(path, format="PNG")
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    finally:
        clipboard.close()
    return path


def _read_macos_clipboard_text() -> str | None:
    try:
        result = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            timeout=CLIPBOARD_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.decode("utf-8", errors="replace")
