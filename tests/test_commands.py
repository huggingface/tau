from pathlib import Path

from tau_coding.commands import CommandRegistry, SlashCommand, create_default_command_registry
from tau_coding.paths import TauPaths
from tau_coding.resources import ResourceDiagnostic
from tau_coding.session import ModelChoice
from tau_coding.session_manager import SessionManager
from tau_coding.skills import Skill
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.tools import create_coding_tools


class FakeSession:
    def __init__(self, tmp_path: Path, manager: SessionManager | None = None) -> None:
        self.cwd = tmp_path
        self.provider_name = "openai"
        self.model = "fake-model"
        self.available_models = ("fake-model", "other-model")
        self.available_model_choices = (
            ModelChoice(provider_name="openai", model="fake-model"),
            ModelChoice(provider_name="openai", model="other-model"),
            ModelChoice(provider_name="local", model="local-model"),
        )
        self.available_providers = ("openai", "local")
        self.tools = tuple(create_coding_tools(cwd=tmp_path))
        self.skills = (
            Skill(
                name="review",
                path=tmp_path / "review.md",
                content="Review code",
                description="Review code",
            ),
        )
        self.prompt_templates = ()
        self.context_files = (
            ProjectContextFile(path=str(tmp_path / "AGENTS.md"), content="Follow instructions."),
        )
        self.context_token_estimate = 123
        self.auto_compact_token_threshold = 200
        self.thinking_level = "medium"
        self.available_thinking_levels = ("off", "minimal", "low", "medium", "high", "xhigh")
        self.tui_theme = "tau-dark"
        self.resource_diagnostics = ()
        self.session_id = "session-1"
        self.session_manager: SessionManager | None = manager
        self.reload_called = False

    def set_model(self, model: str) -> None:
        self.model = model

    def set_provider(self, provider_name: str) -> None:
        self.provider_name = provider_name
        self.model = "local-model"
        self.available_models = ("local-model",)

    def reload(self) -> None:
        self.reload_called = True


def test_registry_ignores_ordinary_prompts_and_skill_expansion(tmp_path: Path) -> None:
    registry = create_default_command_registry()
    session = FakeSession(tmp_path)

    assert registry.execute(session, "hello").handled is False
    assert registry.execute(session, "/skill:review fix this").handled is False


def test_registered_commands_are_pi_aligned(tmp_path: Path) -> None:
    commands = create_default_command_registry().list_commands()

    assert [command.name for command in commands] == [
        "compact",
        "export",
        "hotkeys",
        "login",
        "model",
        "name",
        "new",
        "quit",
        "reload",
        "resume",
        "scoped-models",
        "session",
        "theme",
        "tree",
    ]


def test_quit_and_new_return_control_flags(tmp_path: Path) -> None:
    registry = create_default_command_registry()
    session = FakeSession(tmp_path)

    assert registry.execute(session, "/quit").exit_requested is True
    assert registry.execute(session, "/exit").message == "Unknown command: /exit"
    assert registry.execute(session, "/q").message == "Unknown command: /q"
    assert registry.execute(session, "/new").new_session_requested is True
    assert registry.execute(session, "/clear").message == "Unknown command: /clear"


def test_compact_command_requires_and_returns_summary(tmp_path: Path) -> None:
    registry = create_default_command_registry()
    session = FakeSession(tmp_path)

    missing = registry.execute(session, "/compact")
    requested = registry.execute(session, "/compact Summary of prior work.")

    assert missing.message == "Usage: /compact <summary>"
    assert requested.compact_summary == "Summary of prior work."


def test_tree_command_requests_picker(tmp_path: Path) -> None:
    registry = create_default_command_registry()
    session = FakeSession(tmp_path)

    result = registry.execute(session, "/tree")
    with_args = registry.execute(session, "/tree root")

    assert result.handled is True
    assert result.tree_picker_requested is True
    assert with_args.message == "Usage: /tree"


def test_export_command_requests_default_export(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/export")

    assert result.handled is True
    assert result.export_requested is True
    assert result.export_destination is None
    assert result.export_format is None


def test_export_command_parses_format_and_destination(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(
        FakeSession(tmp_path),
        "/export --format jsonl exports/session.jsonl",
    )

    assert result.export_requested is True
    assert result.export_format == "jsonl"
    assert result.export_destination == Path("exports/session.jsonl")


def test_session_command_includes_session_details(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/session")

    assert result.message is not None
    assert "Model: fake-model" in result.message
    assert f"CWD: {tmp_path}" in result.message
    assert "Tools: 4" in result.message
    assert "Skills: 1" in result.message
    assert "Context files: 1" in result.message
    assert "Estimated context tokens: 123" in result.message
    assert "Thinking mode: medium" in result.message
    assert "Auto compact threshold: 200" in result.message
    assert "Resource diagnostics: 0" in result.message
    assert "Session: session-1" in result.message
    assert create_default_command_registry().execute(FakeSession(tmp_path), "/status").message == "Unknown command: /status"


def test_hotkeys_command_lists_common_tui_shortcuts(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/hotkeys")

    assert result.message is not None
    assert "Common keyboard shortcuts:" in result.message
    assert "Ctrl+K: open slash-command completions" in result.message
    assert "Ctrl+R: open session picker" in result.message
    assert "Shift+Tab: cycle thinking mode" in result.message


def test_model_command_requests_picker_and_switches_models(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)
    registry = create_default_command_registry()

    list_result = registry.execute(session, "/model")
    switch_result = registry.execute(session, "/model other-model")

    assert list_result.model_picker_requested is True
    assert switch_result.message == "Current model: other-model"
    assert session.model == "other-model"


def test_scoped_models_command_requests_scoped_picker(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)
    registry = create_default_command_registry()

    dashed_result = registry.execute(session, "/scoped-models")
    pi_style_result = registry.execute(session, "/scoped models")

    assert dashed_result.scoped_models_picker_requested is True
    assert pi_style_result.scoped_models_picker_requested is True


def test_model_command_rejects_unknown_model(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)

    result = create_default_command_registry().execute(session, "/model missing")

    assert result.message is not None
    assert "Unknown model for provider openai: missing" in result.message
    assert session.model == "fake-model"


def test_theme_command_requests_picker_and_sets_theme(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)
    registry = create_default_command_registry()

    list_result = registry.execute(session, "/theme")
    switch_result = registry.execute(session, "/theme tau-light")
    unknown_result = registry.execute(session, "/theme solarized")

    assert list_result.theme_picker_requested is True
    assert switch_result.theme == "tau-light"
    assert unknown_result.message is not None
    assert "Unknown theme: solarized" in unknown_result.message


def test_non_pi_commands_are_not_registered(tmp_path: Path) -> None:
    registry = create_default_command_registry()
    session = FakeSession(tmp_path)

    for command in ("/provider", "/skills", "/resources", "/context", "/help"):
        result = registry.execute(session, command)
        assert result.handled is True
        assert result.message == f"Unknown command: {command}"


def test_login_command_requests_provider_picker(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/login")

    assert result.handled is True
    assert result.login_picker_requested is True


def test_login_command_requests_provider_login(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/login openai")

    assert result.handled is True
    assert result.login_provider == "openai"


def test_reload_command_refreshes_session_resources(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)

    result = create_default_command_registry().execute(session, "/reload")

    assert result.message is not None
    assert "Reloaded resources and provider configuration." in result.message
    assert "Skills: 1" in result.message
    assert "Prompt templates: 0" in result.message
    assert "Context files: 1" in result.message
    assert "Providers: 2" in result.message
    assert session.reload_called is True


def test_resume_without_argument_requests_picker(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    session = FakeSession(tmp_path, manager=manager)

    result = create_default_command_registry().execute(session, "/resume")

    assert result.resume_picker_requested is True
    assert result.message is None
    assert create_default_command_registry().execute(session, "/sessions").message == (
        "Unknown command: /sessions"
    )


def test_resume_command_requests_indexed_session(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake-model", title="Test session")
    session = FakeSession(tmp_path, manager=manager)

    result = create_default_command_registry().execute(session, f"/resume {record.id}")

    assert result.resume_session_id == record.id
    assert result.message is None


def test_resume_command_rejects_missing_or_unknown_session(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    session = FakeSession(tmp_path, manager=manager)

    unknown = create_default_command_registry().execute(session, "/resume missing")

    assert unknown.message == "Unknown session: missing"


def test_name_command_shows_current_name_and_usage(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake-model", title="Test session")
    session = FakeSession(tmp_path, manager=manager)
    session.session_id = record.id

    result = create_default_command_registry().execute(session, "/name")

    assert result.message == "Current session name: Test session\nUsage: /name <new name>"


def test_name_command_renames_current_session(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake-model", title="Old name")
    session = FakeSession(tmp_path, manager=manager)
    session.session_id = record.id

    result = create_default_command_registry().execute(session, "/name Customer bugfix")

    assert result.message == "Session renamed: Customer bugfix"
    renamed = manager.get_session(record.id)
    assert renamed is not None
    assert renamed.title == "Customer bugfix"
    assert renamed.model == "fake-model"
    assert renamed.updated_at >= record.updated_at


def test_name_command_reports_missing_session_manager(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/name Work")

    assert result.message == "Session manager is not available."


def test_name_command_rejects_multiline_name(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake-model")
    session = FakeSession(tmp_path, manager=manager)
    session.session_id = record.id

    result = create_default_command_registry().execute(session, "/name Bad\nName")

    assert result.message == "Session name must be a single line."
    assert manager.get_session(record.id) == record


def test_unknown_command_returns_message(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/missing")

    assert result.handled is True
    assert result.message == "Unknown command: /missing"


def test_registry_rejects_duplicate_commands_and_aliases() -> None:
    registry = CommandRegistry()
    command = SlashCommand(
        name="test",
        usage="/test",
        description="Test",
        handler=lambda context: create_default_command_registry().execute(context.session, "/session"),
    )
    registry.register(command)

    try:
        registry.register(command)
    except ValueError as exc:
        assert "Duplicate slash command" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected duplicate command to fail")
