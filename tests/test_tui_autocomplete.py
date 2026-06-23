from pathlib import Path

from tau_coding.commands import create_default_command_registry
from tau_coding.skills import Skill
from tau_coding.tui.autocomplete import CompletionOption, build_completion_state


def test_command_completion_for_slash_lists_every_registered_command() -> None:
    registry = create_default_command_registry()
    state = build_completion_state(
        "/",
        command_registry=registry,
        skills=(),
        prompt_templates=(),
    )

    assert [item.display for item in state.items] == [
        f"/{command.name}" if command.name != "skill" else "/skill:"
        for command in registry.list_commands()
    ]


def test_command_completion_suggests_registered_commands() -> None:
    state = build_completion_state(
        "/se",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert [item.display for item in state.items] == ["/session"]
    assert state.selected is not None
    assert state.selected.apply("/se") == "/session"


def test_command_completion_matches_search_terms_with_canonical_replacement() -> None:
    clear_state = build_completion_state(
        "/cl",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )
    sessions_state = build_completion_state(
        "/sess",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert [item.display for item in clear_state.items] == ["/new"]
    assert clear_state.selected is not None
    assert clear_state.selected.apply("/cl") == "/new"
    assert [item.display for item in sessions_state.items] == ["/session"]
    assert sessions_state.selected is not None
    assert sessions_state.selected.apply("/sess") == "/session"


def test_command_completion_prioritizes_direct_matches_over_search_terms() -> None:
    state = build_completion_state(
        "/res",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert [item.display for item in state.items[:2]] == ["/resume", "/new"]
    assert state.selected is not None
    assert state.selected.apply("/res") == "/resume"


def test_skill_command_is_available_for_command_completion() -> None:
    state = build_completion_state(
        "/ski",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert [item.display for item in state.items] == ["/skill:"]
    assert state.selected is not None
    assert state.selected.apply("/ski") == "/skill:"


def test_skill_name_completion_preserves_request_text() -> None:
    state = build_completion_state(
        "/skill:r fix tests",
        command_registry=create_default_command_registry(),
        skills=(
            Skill(
                name="review",
                path=Path("review.md"),
                content="Review code",
                description="Review code",
            ),
        ),
        prompt_templates=(),
    )

    assert [item.display for item in state.items] == ["/skill:review"]
    assert state.selected is not None
    assert state.selected.apply("/skill:r fix tests") == "/skill:review fix tests"


def test_completion_selection_wraps() -> None:
    state = build_completion_state(
        "/s",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert len(state.items) > 1
    assert state.select_previous().selected_index == len(state.items) - 1
    assert state.select_next().selected_index == 1


def test_model_argument_completion_preserves_existing_text() -> None:
    state = build_completion_state(
        "/model fak continue",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        model_names=("fake-model", "other-model"),
    )

    assert [item.display for item in state.items] == ["fake-model"]
    assert state.selected is not None
    assert state.selected.apply("/model fak continue") == "/model fake-model continue"


def test_provider_argument_completion_is_not_available() -> None:
    state = build_completion_state(
        "/provider lo",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        provider_names=("openai", "local"),
    )

    assert state.items == ()


def test_login_argument_completion_uses_available_providers() -> None:
    state = build_completion_state(
        "/login op",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        provider_names=("openai", "openrouter", "anthropic"),
    )

    assert [item.display for item in state.items] == ["openai", "openrouter"]


def test_thinking_argument_completion_uses_available_modes() -> None:
    state = build_completion_state(
        "/thinking h",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        thinking_levels=("off", "minimal", "low", "medium", "high", "xhigh"),
    )

    assert state.items == ()


def test_theme_argument_completion_uses_theme_names() -> None:
    state = build_completion_state(
        "/theme tau-",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        theme_names=("tau-dark", "tau-light", "high-contrast"),
    )

    assert [item.display for item in state.items] == ["tau-dark", "tau-light"]
    assert state.selected is not None
    assert state.selected.apply("/theme tau-") == "/theme tau-dark"


def test_resume_argument_completion_uses_session_ids() -> None:
    state = build_completion_state(
        "/resume sess",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        session_ids=("session-1", "other"),
    )

    assert [item.display for item in state.items] == ["session-1"]
    assert state.selected is not None
    assert state.selected.apply("/resume sess") == "/resume session-1"


def test_resume_argument_completion_uses_session_options_with_descriptions() -> None:
    state = build_completion_state(
        "/resume sess",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        session_options=(
            CompletionOption(value="session-2", description="Newer - qwen - /repo"),
            CompletionOption(value="session-1", description="Older - gpt - /repo"),
        ),
    )

    assert [item.display for item in state.items] == ["session-2", "session-1"]
    assert [item.description for item in state.items] == [
        "Newer - qwen - /repo",
        "Older - gpt - /repo",
    ]


def test_file_reference_completion_matches_workspace_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / ".hidden").write_text("secret\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("", encoding="utf-8")

    state = build_completion_state(
        "please read @app",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        cwd=tmp_path,
    )

    assert [item.display for item in state.items] == ["@src/app.py"]
    assert state.selected is not None
    assert state.selected.apply("please read @app") == "please read @src/app.py"


def test_file_reference_completion_stays_off_for_slash_commands(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")

    state = build_completion_state(
        "/help @read",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        cwd=tmp_path,
    )

    assert state.items == ()


def test_shell_path_completion_preserves_bang_prefix(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")

    state = build_completion_state(
        "!cat READ",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        cwd=tmp_path,
    )

    assert [item.display for item in state.items] == ["README.md"]
    assert state.selected is not None
    assert state.selected.apply("!cat READ") == "!cat README.md"


def test_shell_path_completion_preserves_double_bang_prefix(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")

    state = build_completion_state(
        "!!cat READ",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        cwd=tmp_path,
    )

    assert [item.display for item in state.items] == ["README.md"]
    assert state.selected is not None
    assert state.selected.apply("!!cat READ") == "!!cat README.md"


def test_shell_path_completion_matches_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    state = build_completion_state(
        "!cat src/ma",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        cwd=tmp_path,
    )

    assert [item.display for item in state.items] == ["src/main.py"]
    assert state.selected is not None
    assert state.selected.apply("!cat src/ma") == "!cat src/main.py"


def test_shell_path_completion_adds_trailing_slash_for_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    directory_state = build_completion_state(
        "!cat sr",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        cwd=tmp_path,
    )
    child_state = build_completion_state(
        "!cat src/",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        cwd=tmp_path,
    )

    assert [item.display for item in directory_state.items] == ["src/"]
    assert directory_state.selected is not None
    assert directory_state.selected.apply("!cat sr") == "!cat src/"
    assert [item.display for item in child_state.items] == ["src/main.py"]
