from __future__ import annotations

from io import StringIO

from tau_coding.tui.terminal_title import (
    MAX_TERMINAL_TITLE_LENGTH,
    TerminalTitleController,
    build_terminal_title,
    osc_terminal_title_sequence,
    sanitize_terminal_title,
    terminal_title_supported,
)


class TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True


class PipeStringIO(StringIO):
    def isatty(self) -> bool:
        return False


def test_build_terminal_title_uses_session_name_and_running_frame() -> None:
    assert build_terminal_title("build notes", running=False) == "τ | build notes"
    assert build_terminal_title("build notes", running=True, frame=1) == "⠙ τ | build notes"


def test_build_terminal_title_falls_back_for_unnamed_sessions() -> None:
    assert build_terminal_title(None, running=False) == "τ"
    assert build_terminal_title(" Untitled session ", running=True) == "⠋ τ"


def test_sanitize_terminal_title_strips_control_bytes_and_caps_length() -> None:
    malicious = "\x1b]0;bad\x07\n" + ("x" * MAX_TERMINAL_TITLE_LENGTH)

    sanitized = sanitize_terminal_title(malicious)

    assert "\x1b" not in sanitized
    assert "\x07" not in sanitized
    assert "\n" not in sanitized
    assert len(sanitized) == MAX_TERMINAL_TITLE_LENGTH
    assert sanitized.endswith("…")


def test_osc_terminal_title_sequence_sanitizes_payload() -> None:
    assert osc_terminal_title_sequence("hello\x07") == "\x1b]0;hello\x07"


def test_terminal_title_supported_requires_tty_and_allows_opt_out() -> None:
    assert terminal_title_supported(environ={"TERM": "xterm-256color"}, stream=TtyStringIO())
    assert terminal_title_supported(
        environ={"TERM": "xterm-256color", "NO_COLOR": "1"},
        stream=TtyStringIO(),
    )
    assert not terminal_title_supported(environ={"TERM": "xterm-256color"}, stream=PipeStringIO())
    assert not terminal_title_supported(
        environ={"TERM": "xterm-256color", "TAU_TERMINAL_TITLE": "0"},
        stream=TtyStringIO(),
    )
    assert not terminal_title_supported(environ={"TERM": "dumb"}, stream=TtyStringIO())


def test_terminal_title_controller_writes_running_idle_and_restore_titles() -> None:
    writes: list[str] = []
    controller = TerminalTitleController(enabled=True, writer=writes.append)

    controller.update("build notes", running=False)
    controller.update("build notes", running=False)
    controller.update("build notes", running=True, frame=2)
    controller.restore()

    assert writes == [
        "\x1b]0;τ | build notes\x07",
        "\x1b]0;⠹ τ | build notes\x07",
        "\x1b]0;τ\x07",
    ]


def test_terminal_title_controller_noops_when_disabled() -> None:
    writes: list[str] = []
    controller = TerminalTitleController(enabled=False, writer=writes.append)

    controller.update("build notes", running=True)
    controller.restore()

    assert writes == []


def test_terminal_title_controller_disables_itself_after_write_failure() -> None:
    calls = 0

    def failing_writer(sequence: str) -> None:
        nonlocal calls
        calls += 1
        raise OSError("terminal is gone")

    controller = TerminalTitleController(enabled=True, writer=failing_writer)

    controller.update("build notes", running=False)
    controller.update("other", running=False)

    assert calls == 1
    assert controller.enabled is False
