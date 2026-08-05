"""Early process setup for Textual's kitty keyboard protocol.

Importing this module immediately opts iTerm2 out of the kitty keyboard
protocol, before any ``textual`` import runs and its ``DISABLE_KITTY_KEY``
constant is locked in. It must stay free of ``textual`` imports so the call
below runs before the protocol flag is read.
"""

from __future__ import annotations

import os

__all__ = ["opt_out_kitty_keyboard_protocol"]


def opt_out_kitty_keyboard_protocol(environ: dict[str, str] | None = None) -> None:
    """Disable the kitty keyboard protocol on terminals that break IME input.

    Textual's terminal driver arms the kitty keyboard protocol, enabling
    "report all keys + associated text" by default. Terminals that only
    partially implement that protocol (notably iTerm2 on macOS) relay IME
    composition poorly, which breaks Chinese/Japanese/Korean input. Opt out
    here -- before any ``textual`` import runs -- so those terminals fall
    back to delivering IME text as plain UTF-8.

    ``environ`` defaults to ``os.environ``. An explicit ``TEXTUAL_DISABLE_KITTY_KEY``
    value set by the user is always respected.
    """
    target = os.environ if environ is None else environ
    if "TEXTUAL_DISABLE_KITTY_KEY" not in target and target.get("TERM_PROGRAM") == "iTerm.app":
        target["TEXTUAL_DISABLE_KITTY_KEY"] = "1"


# Running at module import keeps the opt-out ahead of every other package
# import, which is the whole point of this module.
opt_out_kitty_keyboard_protocol()
