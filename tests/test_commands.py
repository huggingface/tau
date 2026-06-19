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


def test_help_lists_registered_commands(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/help")

    assert result.handled is True
    assert result.message is not None
    assert "/help" in result.message
    assert "/name <new name>" in result.message
    assert "/new" in result.message
    assert "/clear" not in result.message
    assert "/export" in result.message
    assert "/skills" in result.message


def test_exit_and_new_return_control_flags(tmp_path: Path) -> None:
    registry = create_default_command_registry()
    session = FakeSession(tmp_path)

    assert registry.execute(session, "/exit").exit_requested is True
    assert registry.execute(session, "/q").exit_requested is True
    assert registry.execute(session, "/new").new_session_requested is True
    assert registry.execute(session, "/clear").message == "Unknown command: /clear"


def test_compact_command_requires_and_returns_summary(tmp_path: Path) -> None:
    registry = create_default_command_registry()
    session = FakeSession(tmp_path)

    missing = registry.execute(session, "/compact")
    requested = registry.execute(session, "/compact Summary of prior work.")

    assert missing.message == "Usage: /compact <summary>"
    assert requested.compact_summary == "Summary of prior work."


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


def test_status_includes_session_details(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/status")

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


def test_model_command_requests_picker_and_switches_models(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)
    registry = create_default_command_registry()

    list_result = registry.execute(session, "/model")
    switch_result = registry.execute(session, "/model other-model")

    assert list_result.model_picker_requested is True
    assert switch_result.message == "Current model: other-model"
    assert session.model == "other-model"


def test_model_command_rejects_unknown_model(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)

    result = create_default_command_registry().execute(session, "/model missing")

    assert result.message is not None
    assert "Unknown model for provider openai: missing" in result.message
    assert session.model == "fake-model"


def test_thinking_command_lists_and_requests_modes(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)
    registry = create_default_command_registry()

    list_result = registry.execute(session, "/thinking")
    switch_result = registry.execute(session, "/thinking high")
    unknown_result = registry.execute(session, "/thinking maximum")

    assert list_result.message is not None
    assert "Thinking mode: medium" in list_result.message
    assert "Available modes: off, minimal, low, medium, high, xhigh" in list_result.message
    assert switch_result.thinking_level == "high"
    assert unknown_result.message is not None
    assert "Unknown thinking mode: maximum" in unknown_result.message


def test_thinking_command_reports_unavailable_modes(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)
    session.available_thinking_levels = ()
    registry = create_default_command_registry()

    list_result = registry.execute(session, "/thinking")
    switch_result = registry.execute(session, "/thinking high")

    assert list_result.message is not None
    assert "Thinking controls: unavailable" in list_result.message
    assert switch_result.message is not None
    assert "Thinking controls are unavailable for openai:fake-model" in switch_result.message


def test_thinking_command_rejects_unsupported_available_mode(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)
    session.available_thinking_levels = ("off", "low")

    result = create_default_command_registry().execute(session, "/thinking high")

    assert result.message is not None
    assert "Thinking mode high is not available for openai:fake-model" in result.message
    assert "Available modes: off, low" in result.message


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


def test_provider_command_is_not_registered(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/provider")

    assert result.handled is True
    assert result.message == "Unknown command: /provider"


def test_login_command_requests_provider_picker(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/login")

    assert result.handled is True
    assert result.login_picker_requested is True


def test_login_command_requests_provider_login(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/login openai")

    assert result.handled is True
    assert result.login_provider == "openai"


def test_skills_lists_loaded_skills(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/skills")

    assert result.message is not None
    assert "Available skills:" in result.message
    assert "- review: Review code" in result.message
    assert "/skill:review" not in result.message


def test_resources_lists_discovery_diagnostics(tmp_path: Path) -> None:
    session = FakeSession(tmp_path)
    session.resource_diagnostics = (
        ResourceDiagnostic(
            kind="skill",
            name="review",
            path=tmp_path / "review.md",
            message="overrides lower-precedence resource",
        ),
    )

    result = create_default_command_registry().execute(session, "/resources")

    assert result.message is not None
    assert "Skills: 1" in result.message
    assert "Prompt templates: 0" in result.message
    assert "Context files: 1" in result.message
    assert "Resource diagnostics:" in result.message
    assert "warning skill review" in result.message


def test_context_lists_active_context_files(tmp_path: Path) -> None:
    result = create_default_command_registry().execute(FakeSession(tmp_path), "/context")

    assert result.message is not None
    assert "Active project context files:" in result.message
    assert f"- {tmp_path / 'AGENTS.md'}" in result.message


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
        handler=lambda context: create_default_command_registry().execute(context.session, "/help"),
    )
    registry.register(command)

    try:
        registry.register(command)
    except ValueError as exc:
        assert "Duplicate slash command" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected duplicate command to fail")
