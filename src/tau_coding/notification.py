"""Desktop notification via OSC 9 terminal escape sequence."""

import contextlib
import os
import sys


def send_notification(message: str) -> None:
    stderr = sys.__stderr__
    if stderr is None:
        return
    with contextlib.suppress(OSError):
        os.write(
            stderr.fileno(),
            f"\x1b]9;{message.replace(chr(7), '').replace(chr(27), '')}\x07\r\x1b[K".encode(),
        )
