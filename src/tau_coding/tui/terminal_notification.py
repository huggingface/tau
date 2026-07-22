"""Best-effort terminal attention notifications for completed Tau turns."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import TextIO, cast

from tau_coding.tui.config import TurnNotificationMode
from tau_coding.tui.terminal_title import sanitize_terminal_title

OSC_TERMINATOR = "\a"
TURN_FINISHED_MESSAGE = "Tau turn finished"


def terminal_notification_supported(
    *,
    environ: Mapping[str, str] | None = None,
    stream: TextIO | None = None,
) -> bool:
    """Return whether Tau may write attention sequences to this terminal."""
    env = os.environ if environ is None else environ
    target = sys.__stdout__ if stream is None else stream
    if not getattr(target, "isatty", lambda: False)():
        return False
    if env.get("TERM", "") == "dumb":
        return False
    return not bool(env.get("CI", ""))


def osc9_notification_sequence(message: str) -> str:
    """Build a sanitized OSC 9 desktop-notification sequence."""
    return f"\x1b]9;{sanitize_terminal_title(message)}{OSC_TERMINATOR}"


class TerminalNotificationController:
    """Write a configured terminal notification without affecting core agent code."""

    def __init__(
        self,
        mode: TurnNotificationMode,
        *,
        enabled: bool | None = None,
        writer: Callable[[str], object] | None = None,
        stream: TextIO | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.mode = mode
        self._stream = cast(TextIO, sys.__stdout__) if stream is None else stream
        self.enabled = (
            terminal_notification_supported(environ=environ, stream=self._stream)
            if enabled is None
            else enabled
        )
        self._writer = writer or self._default_write

    def notify_turn_finished(self) -> None:
        """Request attention for a completed turn, if notifications are enabled."""
        if not self.enabled or self.mode == "off":
            return
        sequence = (
            "\a" if self.mode == "bell" else osc9_notification_sequence(TURN_FINISHED_MESSAGE)
        )
        with suppress(OSError, ValueError):
            self._writer(sequence)
            return
        self.enabled = False

    def _default_write(self, sequence: str) -> None:
        self._stream.write(sequence)
        self._stream.flush()
