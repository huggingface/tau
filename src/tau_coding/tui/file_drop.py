"""Detect and normalize files dragged into the terminal.

Terminals do not deliver OS drag-and-drop as a dedicated event. When a file is
dropped onto the terminal window, the terminal emulator types the file's path
into the running program instead. Because Textual enables bracketed-paste mode,
that typed path usually arrives as a single :class:`textual.events.Paste`
message.

The exact text depends on the terminal:

- most terminals shell-escape paths (``/tmp/my\\ file.png``) and separate
  multiple dropped files with spaces;
- some quote paths with spaces (``"/tmp/my file.png"``);
- some VTE-based terminals emit ``file://`` URIs;
- a few emit the bare path, even when it contains spaces.

This module recognizes pasted text that consists solely of one or more existing
absolute paths and normalizes it to clean, space-separated filesystem paths,
quoting any path that contains whitespace.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from urllib.parse import unquote, urlparse

__all__ = ["normalize_dropped_paths"]


def normalize_dropped_paths(text: str) -> str | None:
    """Return normalized prompt text when *text* looks like a file drop.

    The pasted text is treated as a drop only when it consists exclusively of
    one or more absolute paths that exist on disk (shell-escaped, quoted, or
    ``file://`` URI forms are accepted). Anything else returns ``None`` so the
    paste falls through to default handling.
    """
    stripped = text.strip()
    if not stripped:
        return None

    # A single dropped file may arrive as a bare path with unescaped spaces.
    whole = _token_to_path(stripped)
    if whole is not None:
        return _quote_path(whole)

    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None

    paths: list[str] = []
    for token in tokens:
        path = _token_to_path(token)
        if path is None:
            return None
        paths.append(path)
    return " ".join(_quote_path(path) for path in paths)


def _token_to_path(token: str) -> str | None:
    """Resolve one dropped token to an existing absolute path, if possible."""
    candidate = token
    if candidate.startswith("file://"):
        parsed = urlparse(candidate)
        if parsed.netloc not in ("", "localhost"):
            return None
        candidate = unquote(parsed.path)
    path = Path(candidate)
    if not path.is_absolute() or not path.exists():
        return None
    return candidate


def _quote_path(path: str) -> str:
    """Quote *path* with double quotes when it contains whitespace."""
    if not any(char.isspace() for char in path):
        return path
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
