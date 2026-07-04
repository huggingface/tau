import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tau_agent import AssistantMessage, UserMessage
from tau_agent.session import JsonlSessionStorage, MessageEntry
from tau_ai import (
    FakeProvider,
    ProviderErrorEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderTextDeltaEvent,
)
from tau_coding import CodingSessionRecord, SessionManager, cli
from tau_coding.cli import app, run_print_mode
from tau_coding.paths import TauPaths
from tau_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderSettings,
    load_provider_settings,
)
from tau_coding.rendering import PrintOutputMode
from tau_coding.resources import TauResourcePaths
from tau_coding.system_prompt import BuildSystemPromptOptions, build_system_prompt
from tau_coding.tools import create_coding_tools
from tau_coding.update_check import (
    ReleaseNoteSection,
    ReleaseNotesEntry,
    ReleaseNotesNotice,
    UpdateNotice,
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", value)


def test_version_command() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "tau 0.1.2"


def test_version_command_does_not_check_for_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "_startup_update_notice",
        lambda: (_ for _ in ()).throw(AssertionError("no update check")),
    )

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "tau 0.1.2"


def test_print_mode_writes_update_notice_to_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_openai_print_mode(
        prompt: str,
        model: str | None,
        cwd: Path,
        output: PrintOutputMode,
        provider_name: str | None,
    ) -> bool:
        del prompt, model, cwd, output, provider_name
        return True

    monkeypatch.setattr(
        cli,
        "_startup_update_notice",
        lambda: UpdateNotice(current_version="0.1.0", latest_version="0.2.0"),
    )
    monkeypatch.setattr(cli, "run_openai_print_mode", fake_run_openai_print_mode)

    result = CliRunner().invoke(app, ["-p", "hello"])

    assert result.exit_code == 0
    assert "Tau 0.2.0 is available (installed: 0.1.0)" in result.stderr


def test_json_print_mode_suppresses_update_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_openai_print_mode(
        prompt: str,
        model: str | None,
        cwd: Path,
        output: PrintOutputMode,
        provider_name: str | None,
    ) -> bool:
        del prompt, model, cwd, output, provider_name
        return True

    monkeypatch.setattr(
        cli,
        "_startup_update_notice",
        lambda: UpdateNotice(current_version="0.1.0", latest_version="0.2.0"),
    )
    monkeypatch.setattr(cli, "run_openai_print_mode", fake_run_openai_print_mode)

    result = CliRunner().invoke(app, ["-p", "hello", "--output", "json"])

    assert result.exit_code == 0
    assert result.stderr == ""


def test_utility_command_does_not_check_for_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "_startup_update_notice",
        lambda: (_ for _ in ()).throw(AssertionError("no update check")),
    )
    monkeypatch.setattr(cli.SessionManager, "list_sessions", lambda self: [])

    result = CliRunner().invoke(app, ["sessions"])

    assert result.exit_code == 0
    assert "No sessions found." in result.stdout


def test_cli_without_prompt_invokes_tui_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str | None, Path, str | None, bool, str | None, int | None, str | None]] = []

    async def fake_run_openai_tui(
        model: str | None,
        cwd: Path,
        session_id: str | None,
        new_session: bool,
        provider_name: str | None,
        auto_compact_token_threshold: int | None,
        initial_prompt: str | None,
        update_notice: object | None = None,
    ) -> None:
        del update_notice
        calls.append(
            (
                model,
                cwd,
                session_id,
                new_session,
                provider_name,
                auto_compact_token_threshold,
                initial_prompt,
            )
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_startup_update_notice", lambda: None)
    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0
    assert calls == [(None, tmp_path, None, False, None, None, None)]


def test_cli_positional_prompt_invokes_tui_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str | None, Path, str | None, bool, str | None, int | None, str | None]] = []

    async def fake_run_openai_tui(
        model: str | None,
        cwd: Path,
        session_id: str | None,
        new_session: bool,
        provider_name: str | None,
        auto_compact_token_threshold: int | None,
        initial_prompt: str | None,
        update_notice: object | None = None,
    ) -> None:
        del update_notice
        calls.append(
            (
                model,
                cwd,
                session_id,
                new_session,
                provider_name,
                auto_compact_token_threshold,
                initial_prompt,
            )
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_startup_update_notice", lambda: None)
    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(app, ["explain this repo"])

    assert result.exit_code == 0
    assert calls == [(None, tmp_path, None, False, None, None, "explain this repo")]


@pytest.mark.anyio
async def test_run_openai_tui_combines_release_notes_and_update_notice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_tui_app(**kwargs: object) -> None:
        calls.append(kwargs["startup_notices"])  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "run_tui_app", fake_run_tui_app)
    monkeypatch.setattr(
        cli,
        "startup_release_notes_notice",
        lambda version: ReleaseNotesNotice(
            current_version=version,
            previous_version="0.1.1",
            entries=(
                ReleaseNotesEntry(
                    version=version,
                    date=None,
                    sections=(ReleaseNoteSection(title="New", items=("Release note",)),),
                ),
            ),
        ),
    )

    await cli.run_openai_tui(
        model=None,
        cwd=tmp_path,
        update_notice=UpdateNotice(current_version="0.1.2", latest_version="0.1.3"),
    )

    assert calls == [
        (
            "Tau updated to 0.1.2\n\n**New**\n- Release note",
            "Tau 0.1.3 is available (installed: 0.1.2). Update with: uv tool upgrade tau-ai",
        )
    ]


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
async def test_run_print_mode_system_command_prints_prompt_without_provider_call(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider([])

    ok = await run_print_mode(
        prompt="/system",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        storage=storage,
        resource_paths=TauResourcePaths(root=tmp_path / "resources", agents_root=None),
    )

    captured = capsys.readouterr()
    expected_system = build_system_prompt(
        BuildSystemPromptOptions(cwd=tmp_path, tools=create_coding_tools(cwd=tmp_path))
    )
    assert ok is True
    assert captured.out == f"{expected_system}\n"
    assert captured.err == ""
    assert provider.calls == []
    assert await storage.read_all() == []


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
async def test_run_print_mode_includes_discovered_context(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text("Use the local rules.", encoding="utf-8")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
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

    _captured = capsys.readouterr()
    assert ok is True
    assert "Use the local rules." in provider.calls[0][1]
    assert f'<project_instructions path="{tmp_path / "AGENTS.md"}">' in provider.calls[0][1]


@pytest.mark.anyio
async def test_run_print_mode_persists_session_entries(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    storage = JsonlSessionStorage(tmp_path / "print-session.jsonl")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        storage=storage,
    )

    _captured = capsys.readouterr()
    entries = await storage.read_all()
    messages = [entry.message for entry in entries if isinstance(entry, MessageEntry)]

    assert ok is True
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "Say hello"
    assert messages[1].content == "Done"
    assert any(entry.type == "leaf" for entry in entries)


@pytest.mark.anyio
async def test_run_print_mode_terminal_command_adds_context(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    storage = JsonlSessionStorage(tmp_path / "print-session.jsonl")
    provider = FakeProvider([])

    ok = await run_print_mode(
        prompt="! printf hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        storage=storage,
    )

    captured = capsys.readouterr()
    entries = await storage.read_all()
    messages = [entry.message for entry in entries if isinstance(entry, MessageEntry)]

    assert ok is True
    assert "$ printf hello" in captured.out
    assert "[added to context]" in captured.out
    assert "hello" in captured.out
    assert len(messages) == 1
    assert "Terminal command executed by the user." in messages[0].content
    assert provider.calls == []


@pytest.mark.anyio
async def test_run_print_mode_terminal_command_can_skip_context(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    storage = JsonlSessionStorage(tmp_path / "print-session.jsonl")
    provider = FakeProvider([])

    ok = await run_print_mode(
        prompt="!! printf hidden",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        storage=storage,
    )

    captured = capsys.readouterr()
    entries = await storage.read_all()

    assert ok is True
    assert "$ printf hidden" in captured.out
    assert "[not added to context]" in captured.out
    assert "hidden" in captured.out
    assert not any(isinstance(entry, MessageEntry) for entry in entries)
    assert provider.calls == []


@pytest.mark.anyio
async def test_run_print_mode_expands_skill_commands(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "testing.md").write_text("# Testing\nRun pytest.", encoding="utf-8")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="/skill:testing add tests",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
    )

    _captured = capsys.readouterr()

    assert ok is True
    assert '<skill name="testing" location="' in provider.calls[0][2][0].content
    assert "References are relative to" in provider.calls[0][2][0].content
    assert provider.calls[0][2][0].content.endswith("</skill>\n\nadd tests")


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
        prompt: str,
        model: str | None,
        cwd: Path,
        output: PrintOutputMode,
        provider_name: str | None,
    ) -> bool:
        return False

    monkeypatch.setattr(cli, "_startup_update_notice", lambda: None)
    monkeypatch.setattr(cli, "run_openai_print_mode", fake_run_openai_print_mode)

    result = CliRunner().invoke(app, ["-p", "hello"])

    assert result.exit_code == 1


def test_default_tui_invokes_tui_runner_with_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str | None, Path, str | None, bool, str | None, int | None, str | None]] = []

    async def fake_run_openai_tui(
        model: str | None,
        cwd: Path,
        session_id: str | None,
        new_session: bool,
        provider_name: str | None,
        auto_compact_token_threshold: int | None,
        initial_prompt: str | None,
        update_notice: object | None = None,
    ) -> None:
        del update_notice
        calls.append(
            (
                model,
                cwd,
                session_id,
                new_session,
                provider_name,
                auto_compact_token_threshold,
                initial_prompt,
            )
        )

    monkeypatch.setattr(cli, "_startup_update_notice", lambda: None)
    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--model",
            "fake",
            "--provider",
            "local",
            "--resume",
            "session-1",
            "--auto-compact-threshold",
            "1000",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("fake", tmp_path, "session-1", False, "local", 1000, None)]


def test_default_tui_rejects_resume_with_new_session(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--resume",
            "session-1",
            "--new-session",
        ],
    )

    assert result.exit_code != 0
    assert "--resume and --new-session cannot be used together" in _strip_ansi(result.output)


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


@pytest.mark.anyio
async def test_export_session_command_writes_html_for_indexed_session(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(
        cwd=tmp_path,
        model="fake",
        title="Exported Session",
        session_id="session-1",
    )
    await JsonlSessionStorage(record.path).append(
        MessageEntry(id="root", message=UserMessage(content="Export this"))
    )

    output_path = await cli.export_session_command(
        "session-1",
        tmp_path / "session.html",
        session_manager=manager,
    )

    html = output_path.read_text(encoding="utf-8")
    assert output_path == tmp_path / "session.html"
    assert "<title>Exported Session</title>" in html
    assert "Export this" in html
    assert str(record.path) in html


@pytest.mark.anyio
async def test_export_session_command_writes_html_for_jsonl_path(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    cwd = Path.cwd()
    await JsonlSessionStorage(session_path).append(
        MessageEntry(id="root", message=UserMessage(content="Path export"))
    )

    try:
        import os

        os.chdir(tmp_path)
        output_path = await cli.export_session_command(str(session_path))
    finally:
        os.chdir(cwd)

    html = output_path.read_text(encoding="utf-8")
    assert output_path == tmp_path / "session.html"
    assert "<title>Tau session session</title>" in html
    assert "Path export" in html


@pytest.mark.anyio
async def test_export_session_command_writes_jsonl_format_to_cwd(tmp_path: Path) -> None:
    session_path = tmp_path / ".tau" / "sessions" / "session.jsonl"
    cwd = Path.cwd()
    await JsonlSessionStorage(session_path).append(
        MessageEntry(id="root", message=UserMessage(content="JSONL export"))
    )

    try:
        import os

        os.chdir(tmp_path)
        output_path = await cli.export_session_command(str(session_path), export_format="jsonl")
    finally:
        os.chdir(cwd)

    assert output_path == tmp_path / "session.jsonl"
    assert "JSONL export" in output_path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_export_session_command_treats_suffixless_output_as_directory(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "source" / "session.jsonl"
    await JsonlSessionStorage(session_path).append(
        MessageEntry(id="root", message=UserMessage(content="Directory export"))
    )

    output_path = await cli.export_session_command(str(session_path), tmp_path / "exports")

    assert output_path == tmp_path / "exports" / "session.html"
    assert "Directory export" in output_path.read_text(encoding="utf-8")


def test_export_command_invokes_exporter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, Path | None, str | None]] = []
    output_path = tmp_path / "out.html"

    async def fake_export_session_command(
        session_ref: str,
        requested_output_path: Path | None = None,
        requested_export_format: str | None = None,
    ) -> Path:
        calls.append((session_ref, requested_output_path, requested_export_format))
        return output_path

    monkeypatch.setattr(cli, "export_session_command", fake_export_session_command)

    result = CliRunner().invoke(app, ["export", "session-1", str(output_path)])

    assert result.exit_code == 0
    assert calls == [("session-1", output_path, None)]
    assert f"Exported session to {output_path}" in result.stdout


def test_export_command_accepts_format_option(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, Path | None, str | None]] = []
    output_path = tmp_path / "out.jsonl"

    async def fake_export_session_command(
        session_ref: str,
        requested_output_path: Path | None = None,
        requested_export_format: str | None = None,
    ) -> Path:
        calls.append((session_ref, requested_output_path, requested_export_format))
        return output_path

    monkeypatch.setattr(cli, "export_session_command", fake_export_session_command)

    result = CliRunner().invoke(app, ["export", "session-1", "--format", "jsonl"])

    assert result.exit_code == 0
    assert calls == [("session-1", None, "jsonl")]


def test_providers_command_lists_default_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(app, ["providers"])

    assert result.exit_code == 0
    assert "*\topenai\topenai-compatible\tgpt-5.5" in result.stdout
    assert " \topenai-codex\topenai-codex\tgpt-5.5" in result.stdout
    assert " \tanthropic\tanthropic\tclaude-sonnet-4-6" in result.stdout
    assert " \topenrouter\topenai-compatible\topenai/gpt-5.5" in result.stdout
    assert " \thuggingface\topenai-compatible\topenai/gpt-oss-120b" in result.stdout


def test_render_provider_settings_shows_credential_source(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("STORED_API_KEY", raising=False)
    monkeypatch.setenv("ENV_API_KEY", "env-key")
    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    settings = ProviderSettings(
        default_provider="stored",
        providers=(
            OpenAICompatibleProviderConfig(
                name="stored",
                api_key_env="STORED_API_KEY",
                credential_name="stored",
            ),
            OpenAICompatibleProviderConfig(
                name="env",
                api_key_env="ENV_API_KEY",
                credential_name=None,
            ),
            OpenAICompatibleProviderConfig(
                name="missing",
                api_key_env="MISSING_API_KEY",
                credential_name="missing",
            ),
        ),
    )

    class FakeCredentials:
        def get(self, name: str) -> str | None:
            return "stored-key" if name == "stored" else None

    cli.render_provider_settings(settings, credential_reader=FakeCredentials())

    output = capsys.readouterr().out
    assert "*\tstored\topenai-compatible\tgpt-5.5" in output
    assert "\tSTORED_API_KEY\tstored:stored\t" in output
    assert "\tENV_API_KEY\tenv:ENV_API_KEY\t" in output
    assert "\tMISSING_API_KEY\tmissing\t" in output


def test_setup_command_writes_provider_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")

    result = CliRunner().invoke(
        app,
        [
            "--provider",
            "local",
            "--base-url",
            "http://localhost:11434/v1/",
            "--api-key-env",
            "LOCAL_API_KEY",
            "--timeout-seconds",
            "120",
            "--max-retries",
            "2",
            "--max-retry-delay-seconds",
            "0.5",
            "--model",
            "qwen",
            "setup",
        ],
    )

    settings = load_provider_settings(TauPaths(home=tmp_path / ".tau"))
    provider = settings.get_provider("local")
    assert result.exit_code == 0
    assert "Saved provider 'local'" in result.stdout
    assert settings.default_provider == "local"
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.api_key_env == "LOCAL_API_KEY"
    assert provider.default_model == "qwen"
    assert provider.timeout_seconds == 120
    assert provider.max_retries == 2
    assert provider.max_retry_delay_seconds == 0.5


def test_setup_command_warns_when_api_key_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MISSING_API_KEY", raising=False)

    result = CliRunner().invoke(
        app,
        [
            "--provider",
            "missing",
            "--api-key-env",
            "MISSING_API_KEY",
            "--model",
            "test-model",
            "setup",
        ],
    )

    assert result.exit_code == 0
    assert "Set MISSING_API_KEY before running Tau with this provider." in result.stderr
