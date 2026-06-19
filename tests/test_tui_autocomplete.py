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
        "/st",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert [item.display for item in state.items] == ["/status"]
    assert state.selected is not None
    assert state.selected.apply("/st") == "/status"


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
    assert [item.display for item in sessions_state.items] == ["/resume"]
    assert sessions_state.selected is not None
    assert sessions_state.selected.apply("/sess") == "/resume"


def test_skill_command_completion_prefers_colon_form() -> None:
    state = build_completion_state(
        "/ski",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert "/skill:" in [item.display for item in state.items]


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

    assert [item.display for item in state.items] == ["high"]
    assert state.selected is not None
    assert state.selected.apply("/thinking h") == "/thinking high"


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
