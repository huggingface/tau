"""Helpers for updating the terminal window/tab title from Tau's TUI."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import TextIO, cast

MAX_TERMINAL_TITLE_LENGTH = 120
OSC_TERMINATOR = "\a"
TAU_TITLE_MARK = "τ"
RUNNING_TITLE_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def terminal_title_supported(
    *,
    environ: Mapping[str, str] | None = None,
    stream: TextIO | None = None,
) -> bool:
    """Return whether Tau should emit OSC title sequences in this process."""
    env = os.environ if environ is None else environ
    if env.get("TAU_TERMINAL_TITLE", "").lower() in {"0", "false", "no", "off"}:
        return False
    target = sys.__stdout__ if stream is None else stream
    if not getattr(target, "isatty", lambda: False)():
        return False
    if env.get("TERM", "") == "dumb":
        return False
    return not (env.get("CI", "") and env.get("TAU_TERMINAL_TITLE", "").lower() != "1")


def sanitize_terminal_title(
    value: str | None,
    *,
    max_length: int = MAX_TERMINAL_TITLE_LENGTH,
) -> str:
    """Strip OSC-breaking control bytes and cap terminal-title text."""
    if value is None:
        return ""
    sanitized = _CONTROL_CHARS_RE.sub("", value).strip()
    if len(sanitized) <= max_length:
        return sanitized
    if max_length <= 1:
        return sanitized[:max_length]
    return sanitized[: max_length - 1].rstrip() + "…"


def build_terminal_title(
    session_title: str | None,
    *,
    running: bool,
    frame: int = 0,
) -> str:
    """Return Tau's terminal tab title for the current session/running state."""
    title = sanitize_terminal_title(session_title)
    title = (
        TAU_TITLE_MARK
        if not title or title.lower() == "untitled session"
        else f"{TAU_TITLE_MARK} | {title}"
    )
    if not running:
        return title
    return f"{RUNNING_TITLE_FRAMES[frame % len(RUNNING_TITLE_FRAMES)]} {title}"


def osc_terminal_title_sequence(title: str) -> str:
    """Return an OSC 0 sequence that sets the terminal window/tab title."""
    return f"\x1b]0;{sanitize_terminal_title(title)}{OSC_TERMINATOR}"


class TerminalTitleController:
    """Small stateful writer that avoids duplicate OSC title writes."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        writer: Callable[[str], object] | None = None,
        stream: TextIO | None = None,
        environ: Mapping[str, str] | None = None,
        exit_title: str = TAU_TITLE_MARK,
    ) -> None:
        self._stream = cast(TextIO, sys.__stdout__) if stream is None else stream
        self.enabled = (
            terminal_title_supported(environ=environ, stream=self._stream)
            if enabled is None
            else enabled
        )
        self._writer = writer or self._default_write
        self._last_title: str | None = None
        self._exit_title = exit_title

    def _write(self, sequence: str) -> bool:
        """Best-effort title write; disable future writes if the stream fails."""
        with suppress(OSError, ValueError):
            self._writer(sequence)
            return True
        self.enabled = False
        return False

    def update(self, session_title: str | None, *, running: bool, frame: int = 0) -> None:
        """Write the current Tau title if it differs from the last emitted title."""
        if not self.enabled:
            return
        title = build_terminal_title(session_title, running=running, frame=frame)
        if title == self._last_title:
            return
        if self._write(osc_terminal_title_sequence(title)):
            self._last_title = title

    def restore(self) -> None:
        """Leave the terminal title in a neutral idle Tau state on shutdown."""
        if not self.enabled:
            return
        if self._write(osc_terminal_title_sequence(self._exit_title)):
            self._last_title = self._exit_title

    def _default_write(self, sequence: str) -> None:
        self._stream.write(sequence)
        self._stream.flush()
