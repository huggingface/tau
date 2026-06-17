from pathlib import Path

import pytest
from typer.testing import CliRunner

from tau_agent import AssistantMessage
from tau_ai import (
    FakeProvider,
    ProviderErrorEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderTextDeltaEvent,
)
from tau_coding import CodingSessionRecord, cli
from tau_coding.cli import app, run_print_mode
from tau_coding.rendering import PrintOutputMode
from tau_coding.resources import TauResourcePaths
from tau_coding.system_prompt import BuildSystemPromptOptions, build_system_prompt
from tau_coding.tools import create_coding_tools


def test_version_command() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "tau 0.1.0"


def test_cli_without_prompt_invokes_tui_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, Path, str | None, bool]] = []

    async def fake_run_openai_tui(
        model: str, cwd: Path, session_id: str | None, new_session: bool
    ) -> None:
        calls.append((model, cwd, session_id, new_session))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0
    assert calls == [("gpt-4.1-mini", tmp_path, None, False)]


@pytest.mark.anyio
async def test_run_print_mode_prints_final_assistant_text(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="Hel"),
                ProviderTextDeltaEvent(delta="lo"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Hello")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        resource_paths=TauResourcePaths(root=tmp_path / "resources", agents_root=None),
    )

    captured = capsys.readouterr()
    assert ok is True
    assert captured.out == "Hello\n"
    assert captured.err == ""
    assert provider.calls[0][0] == "fake"
    assert provider.calls[0][1] == build_system_prompt(
        BuildSystemPromptOptions(cwd=tmp_path, tools=create_coding_tools(cwd=tmp_path))
    )
    assert [tool.name for tool in provider.calls[0][3]] == ["read", "write", "edit", "bash"]


@pytest.mark.anyio
async def test_run_print_mode_fails_on_non_recoverable_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderErrorEvent(message="provider failed"),
            ]
        ]
    )

    ok = await run_print_mode(prompt="Say hello", model="fake", cwd=tmp_path, provider=provider)

    captured = capsys.readouterr()
    assert ok is False
    assert captured.out == ""
    assert "Error: provider failed" in captured.err


@pytest.mark.anyio
async def test_run_print_mode_can_emit_json_events(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="Hello"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Hello")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        output=PrintOutputMode.json,
    )

    captured = capsys.readouterr()
    assert ok is True
    assert '"type":"agent_start"' in captured.out
    assert '"type":"message_delta"' in captured.out
    assert captured.err == ""


@pytest.mark.anyio
async def test_run_print_mode_can_emit_live_transcript(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="Hel"),
                ProviderTextDeltaEvent(delta="lo"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Hello")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        output=PrintOutputMode.transcript,
    )

    captured = capsys.readouterr()
    assert ok is True
    assert captured.out == "Hello\n"
    assert captured.err == ""


def test_cli_exits_nonzero_when_print_mode_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_openai_print_mode(
        prompt: str, model: str, cwd: Path, output: PrintOutputMode
    ) -> bool:
        return False

    monkeypatch.setattr(cli, "run_openai_print_mode", fake_run_openai_print_mode)

    result = CliRunner().invoke(app, ["hello"])

    assert result.exit_code == 1


def test_tui_command_invokes_tui_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, Path, str | None, bool]] = []

    async def fake_run_openai_tui(
        model: str, cwd: Path, session_id: str | None, new_session: bool
    ) -> None:
        calls.append((model, cwd, session_id, new_session))

    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--model",
            "fake",
            "--resume",
            "session-1",
            "tui",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("fake", tmp_path, "session-1", False)]


def test_tui_command_rejects_resume_with_new_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run_openai_tui(
        model: str, cwd: Path, session_id: str | None, new_session: bool
    ) -> None:
        raise RuntimeError("--resume and --new-session cannot be used together")

    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--resume",
            "session-1",
            "--new-session",
            "tui",
        ],
    )

    assert result.exit_code != 0
    assert "--resume and --new-session cannot be used together" in result.output


def test_sessions_command_lists_indexed_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = CodingSessionRecord(
        id="session-1",
        path=tmp_path / "session.jsonl",
        cwd=tmp_path,
        model="fake",
        title="Test session",
        created_at=1.0,
        updated_at=2.0,
    )

    class FakeSessionManager:
        def list_sessions(self) -> list[CodingSessionRecord]:
            return [record]

    monkeypatch.setattr(cli, "SessionManager", FakeSessionManager)

    result = CliRunner().invoke(app, ["sessions"])

    assert result.exit_code == 0
    assert "session-1" in result.stdout
    assert "Test session" in result.stdout


def test_sessions_command_handles_empty_index(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSessionManager:
        def list_sessions(self) -> list[CodingSessionRecord]:
            return []

    monkeypatch.setattr(cli, "SessionManager", FakeSessionManager)

    result = CliRunner().invoke(app, ["sessions"])

    assert result.exit_code == 0
    assert "No sessions found." in result.stdout
