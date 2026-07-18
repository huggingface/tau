from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from rich.console import Console

from tau_coding.rich_tui import RichTuiCompleter, RichTuiRenderer
from tau_coding.session import CodingSession
from tau_coding.tui.state import TuiState


class FakeRegistry:
    def list_commands(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="session"), SimpleNamespace(name="model")]


def fake_session(tmp_path: Path) -> CodingSession:
    return cast(
        CodingSession,
        SimpleNamespace(
            command_registry=FakeRegistry(),
            skills=(SimpleNamespace(name="review"),),
            prompt_templates=(SimpleNamespace(name="fix-tests"),),
            cwd=tmp_path,
            provider_name="test-provider",
            model="test-model",
            thinking_level="medium",
            context_window_tokens=1000,
            context_token_estimate=250,
        ),
    )


def test_completer_offers_commands_skills_and_templates(tmp_path: Path) -> None:
    completer = RichTuiCompleter(fake_session(tmp_path))

    slash = list(
        completer.get_completions(
            Document("/s"), cast(Any, CompleteEvent(completion_requested=True))
        )
    )
    skill = list(
        completer.get_completions(
            Document("/skill:r"), cast(Any, CompleteEvent(completion_requested=True))
        )
    )
    template = list(
        completer.get_completions(
            Document("fix"), cast(Any, CompleteEvent(completion_requested=True))
        )
    )

    assert [item.text for item in slash] == ["/session", "/skill:review"]
    assert [item.text for item in skill] == ["/skill:review"]
    assert [item.text for item in template] == ["fix-tests"]


def test_renderer_keeps_session_details_in_one_status_line(tmp_path: Path) -> None:
    state = TuiState()
    state.add_item("assistant", "Hello **Tau**")
    renderer = RichTuiRenderer(fake_session(tmp_path), state)
    console = Console(record=True, width=100, height=12)

    console.print(renderer.layout())
    output = console.export_text()

    assert "Hello Tau" in output
    assert "test-provider:test-model" in output
    assert "medium" in output
    assert "ctx 25%" in output
