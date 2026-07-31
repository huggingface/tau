from __future__ import annotations

from io import StringIO

from tau_coding.tui.terminal_notification import (
    TerminalNotificationController,
    desktop_notification_protocol,
    desktop_notification_sequence,
    osc9_notification_sequence,
    osc99_notification_sequence,
    terminal_notification_supported,
)


class TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True


class PipeStringIO(StringIO):
    def isatty(self) -> bool:
        return False


def test_notification_sequences_sanitize_control_bytes() -> None:
    assert osc9_notification_sequence("finished\x07\n") == "\x1b]9;finished\x07"
    assert osc99_notification_sequence("finished\x07\n") == "\x1b]99;;finished\x1b\\"


def test_desktop_notification_protocol_detects_supported_terminals() -> None:
    assert desktop_notification_protocol(environ={"TERM_PROGRAM": "ghostty"}) == "osc9"
    assert desktop_notification_protocol(environ={"TERM_PROGRAM": "iTerm.app"}) == "osc9"
    assert desktop_notification_protocol(environ={"TERM_PROGRAM": "mintty"}) == "osc9"
    assert desktop_notification_protocol(environ={"TERM": "xterm-kitty"}) == "osc99"
    assert desktop_notification_protocol(environ={"KITTY_WINDOW_ID": "1"}) == "osc99"
    assert desktop_notification_protocol(environ={"TERM_PROGRAM": "Apple_Terminal"}) is None


def test_desktop_notification_sequence_uses_terminal_protocol() -> None:
    assert (
        desktop_notification_sequence("Tau turn finished", environ={"TERM_PROGRAM": "ghostty"})
        == "\x1b]9;Tau turn finished\x07"
    )
    assert (
        desktop_notification_sequence("Tau turn finished", environ={"TERM": "xterm-kitty"})
        == "\x1b]99;;Tau turn finished\x1b\\"
    )
    assert (
        desktop_notification_sequence(
            "Tau turn finished", environ={"TERM_PROGRAM": "Apple_Terminal"}
        )
        is None
    )


def test_terminal_notification_supported_requires_interactive_terminal() -> None:
    assert terminal_notification_supported(environ={"TERM": "xterm-256color"}, stream=TtyStringIO())
    assert not terminal_notification_supported(
        environ={"TERM": "xterm-256color"}, stream=PipeStringIO()
    )
    assert not terminal_notification_supported(environ={"TERM": "dumb"}, stream=TtyStringIO())
    assert not terminal_notification_supported(
        environ={"TERM": "xterm-256color", "CI": "1"}, stream=TtyStringIO()
    )


def test_terminal_notification_controller_writes_configured_sequence() -> None:
    bell_writes: list[str] = []
    desktop_writes: list[str] = []

    TerminalNotificationController(
        "bell", enabled=True, writer=bell_writes.append
    ).notify_turn_finished()
    TerminalNotificationController(
        "desktop",
        enabled=True,
        writer=desktop_writes.append,
        environ={"TERM_PROGRAM": "ghostty"},
    ).notify_turn_finished()

    assert bell_writes == ["\a"]
    assert desktop_writes == ["\x1b]9;Tau turn finished\x07"]


def test_terminal_notification_controller_noops_when_off_disabled_or_unknown() -> None:
    writes: list[str] = []

    TerminalNotificationController("off", enabled=True, writer=writes.append).notify_turn_finished()
    TerminalNotificationController(
        "bell", enabled=False, writer=writes.append
    ).notify_turn_finished()
    TerminalNotificationController(
        "desktop",
        enabled=True,
        writer=writes.append,
        environ={"TERM_PROGRAM": "Apple_Terminal"},
    ).notify_turn_finished()

    assert writes == []


def test_terminal_notification_controller_disables_after_write_failure() -> None:
    calls = 0

    def failing_writer(sequence: str) -> None:
        nonlocal calls
        del sequence
        calls += 1
        raise OSError("terminal is gone")

    controller = TerminalNotificationController(
        "desktop",
        enabled=True,
        writer=failing_writer,
        environ={"TERM_PROGRAM": "ghostty"},
    )

    controller.notify_turn_finished()
    controller.notify_turn_finished()

    assert calls == 1
    assert controller.enabled is False
