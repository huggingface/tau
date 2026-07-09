from pathlib import Path

from rich.console import Console

from tau_coding.commands import create_default_command_registry
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.skills import Skill
from tau_coding.tui.autocomplete import CompletionOption, build_completion_state
from tau_coding.tui.widgets import render_completion_suggestions


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


def test_slash_completion_groups_commands_and_custom_prompts() -> None:
    state = build_completion_state(
        "/",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(
            PromptTemplate(
                name="example",
                path=Path("example.md"),
                content="Example prompt.",
                description="Run example.",
            ),
        ),
    )

    assert state.items[0].category == "Commands"
    assert state.items[-1].display == "/example"
    assert state.items[-1].category == "Custom prompts"
    console = Console(width=100, record=True)
    console.print(render_completion_suggestions(state))
    rendered = console.export_text()
    assert "Commands" in rendered
    assert "Custom prompts" in rendered
    assert rendered.index("Commands") < rendered.index("/compact")
    assert rendered.index("Custom prompts") < rendered.index("/example")


def test_completion_rendering_aligns_wrapped_descriptions() -> None:
    state = build_completion_state(
        "/skill:r",
        command_registry=create_default_command_registry(),
        skills=(
            Skill(
                name="review",
                path=Path("review.md"),
                content="Review code",
                description="Review code with a long description that wraps onto another line.",
            ),
        ),
        prompt_templates=(),
    )

    console = Console(width=48, record=True)
    console.print(render_completion_suggestions(state))
    rendered = console.export_text()

    assert "› /skill:review" in rendered
    assert "                 another line." in rendered
    assert not any(line.startswith("another line.") for line in rendered.splitlines())


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


def test_skill_name_completion_preserves_request_text_for_incomplete_name() -> None:
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


def test_skill_name_completion_hides_after_completed_skill_command_space() -> None:
    trailing_space_state = build_completion_state(
        "/skill:review ",
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
    request_state = build_completion_state(
        "/skill:review fix tests",
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

    assert trailing_space_state.items == ()
    assert request_state.items == ()


def test_custom_prompt_completion_hides_after_completed_prompt_command_space() -> None:
    trailing_space_state = build_completion_state(
        "/example ",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(
            PromptTemplate(
                name="example",
                path=Path("example.md"),
                content="Example prompt.",
                description="Run example.",
            ),
        ),
    )
    request_state = build_completion_state(
        "/example fix tests",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(
            PromptTemplate(
                name="example",
                path=Path("example.md"),
                content="Example prompt.",
                description="Run example.",
            ),
        ),
    )

    assert trailing_space_state.items == ()
    assert request_state.items == ()


def test_builtin_command_completion_hides_after_completed_command_space() -> None:
    trailing_space_state = build_completion_state(
        "/compact ",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )
    request_state = build_completion_state(
        "/compact summarize old context",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert trailing_space_state.items == ()
    assert request_state.items == ()


def test_builtin_command_argument_completion_wins_over_completed_command_hide() -> None:
    state = build_completion_state(
        "/model fak",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        model_names=("fake-model",),
    )

    assert [item.display for item in state.items] == ["fake-model"]


def test_builtin_command_argument_completion_wins_over_custom_prompt_name() -> None:
    state = build_completion_state(
        "/model fak",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(
            PromptTemplate(
                name="model",
                path=Path("model.md"),
                content="Choose a model.",
            ),
        ),
        model_names=("fake-model",),
    )

    assert [item.display for item in state.items] == ["fake-model"]


def test_custom_prompt_completion_reappears_when_deleting_back_to_command_token() -> None:
    state = build_completion_state(
        "/exa",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(
            PromptTemplate(
                name="example",
                path=Path("example.md"),
                content="Example prompt.",
                description="Run example.",
            ),
        ),
    )

    assert [item.display for item in state.items] == ["/example"]


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


def test_logout_argument_completion_uses_available_providers() -> None:
    state = build_completion_state(
        "/logout op",
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
