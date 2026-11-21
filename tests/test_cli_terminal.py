"""Keep help discovery and headless invocations out of Textual's input loop."""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

from tau_coding import cli


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class _BrokenStream:
    def isatty(self) -> bool:
        raise OSError("unavailable descriptor")


@pytest.mark.parametrize(
    "command", ["sessions", "providers", "install", "update", "export", "setup", "skills"]
)
def test_command_help_never_starts_tui(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Help must not prepare a session or start the TUI")

    monkeypatch.setattr(cli, "run_openai_tui", unexpected)
    monkeypatch.setattr(cli, "_cached_startup_update_notice", unexpected)
    result = CliRunner().invoke(cli.app, [command, "--help"])

    output = Text.from_ansi(result.output).plain
    assert result.exit_code == 0
    assert "Usage:" in output
    assert "Tau coding-agent harness" in output


@pytest.mark.parametrize(
    ("args", "prompt"),
    [
        (["skills --help"], "skills --help"),
        (["--", "skills", "--help"], "skills --help"),
        (["explain", "skills", "--help"], "explain skills --help"),
        (["explain skills", "--help"], "explain skills --help"),
    ],
)
def test_literal_help_tokens_remain_prompt_text(
    monkeypatch: pytest.MonkeyPatch, args: list[str], prompt: str
) -> None:
    prompts: list[object] = []

    async def fake_tui(*args: object, **kwargs: object) -> None:
        prompts.append(args[6])

    monkeypatch.setattr(cli, "run_openai_tui", fake_tui)
    monkeypatch.setattr(cli, "_cached_startup_update_notice", lambda: None)
    result = CliRunner().invoke(cli.app, args)

    assert result.exit_code == 0
    assert prompts == [prompt]


@pytest.mark.parametrize("mode_args", [["--print"], ["--mode", "json"]])
def test_print_mode_preserves_help_tokens_without_terminal_validation(
    monkeypatch: pytest.MonkeyPatch, mode_args: list[str]
) -> None:
    prompts: list[object] = []

    async def fake_print(*args: object, **kwargs: object) -> bool:
        prompts.append(args[0])
        return True

    def unexpected() -> None:
        pytest.fail("Headless modes must not require a terminal")

    monkeypatch.setattr(cli, "run_openai_print_mode", fake_print)
    monkeypatch.setattr(cli, "_startup_update_notice", lambda: None)
    monkeypatch.setattr(cli, "_require_interactive_terminal", unexpected)
    result = CliRunner().invoke(cli.app, [*mode_args, "skills", "--help"])

    assert result.exit_code == 0
    assert prompts == ["skills --help"]


def test_rpc_mode_does_not_require_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_rpc(*args: object, **kwargs: object) -> None:
        calls.append(True)

    def unexpected() -> None:
        pytest.fail("RPC mode must not require a terminal")

    monkeypatch.setattr(cli, "run_openai_rpc_mode", fake_rpc)
    monkeypatch.setattr(cli, "_require_interactive_terminal", unexpected)
    result = CliRunner().invoke(cli.app, ["--mode", "rpc"])

    assert result.exit_code == 0
    assert calls == [True]


@pytest.mark.parametrize("stream_name", ["stdin", "stdout"])
@pytest.mark.parametrize("invalid", [None, object(), io.StringIO(), _BrokenStream()])
def test_terminal_validation_rejects_unusable_streams(stream_name: str, invalid: object) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli.sys, "stdin", _Terminal())
        mp.setattr(cli.sys, "stdout", _Terminal())
        mp.setattr(cli.sys, stream_name, invalid)
        with pytest.raises(RuntimeError, match="requires a terminal") as error:
            cli._require_interactive_terminal()
        assert "--print" in str(error.value)
        assert "--mode" in str(error.value)


@pytest.mark.parametrize("stream_name", ["stdin", "stdout"])
def test_terminal_validation_rejects_closed_streams(stream_name: str) -> None:
    closed = io.StringIO()
    closed.close()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli.sys, "stdin", _Terminal())
        mp.setattr(cli.sys, "stdout", _Terminal())
        mp.setattr(cli.sys, stream_name, closed)
        with pytest.raises(RuntimeError, match="requires a terminal"):
            cli._require_interactive_terminal()


def test_terminal_validation_accepts_interactive_streams() -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli.sys, "stdin", _Terminal())
        mp.setattr(cli.sys, "stdout", _Terminal())
        cli._require_interactive_terminal()


@pytest.mark.parametrize("args", [[], ["a prompt"], ["--session", "existing-session"]])
def test_headless_tui_fails_before_preparation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str]
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Headless TUI startup must not prepare a session or start Textual")

    monkeypatch.setattr(cli, "run_tui_app", unexpected)
    monkeypatch.setattr(cli, "startup_release_notes_notice", unexpected)
    monkeypatch.setattr(cli, "_cached_startup_update_notice", lambda: None)
    result = CliRunner().invoke(cli.app, args)

    output = Text.from_ansi(result.output).plain
    assert result.exit_code == 2
    assert "requires a terminal" in output
    assert "--print" in output
    assert not (tmp_path / ".tau" / "sessions").exists()


@pytest.mark.parametrize("pipe_stdin", [False, True], ids=["devnull", "exhausted-pipe"])
@pytest.mark.parametrize("args", [[], ["skills", "--help"], ["sessions", "--help"]])
def test_headless_subprocess_exits_without_creating_a_session(
    tmp_path: Path, pipe_stdin: bool, args: list[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TAU_HOME": str(home / ".tau"),
        "TAU_OFFLINE": "1",
        "TAU_NO_UPDATE_CHECK": "1",
        "NO_COLOR": "1",
    }
    # subprocess.run kills and waits for its child on timeout, so a regression
    # cannot leave another CPU-burning process behind in local or CI runs.
    result = subprocess.run(
        [sys.executable, "-c", "from tau_coding.cli import app; app()", *args],
        cwd=tmp_path,
        env=env,
        stdin=None if pipe_stdin else subprocess.DEVNULL,
        input="" if pipe_stdin else None,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    stdout = Text.from_ansi(result.stdout).plain
    stderr = Text.from_ansi(result.stderr).plain
    if args:
        assert result.returncode == 0, stderr
        assert "Usage:" in stdout
    else:
        assert result.returncode == 2, stderr
        assert "requires a terminal" in stderr
        assert "--print" in stderr
    assert not (home / ".tau" / "sessions").exists()
