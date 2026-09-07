"""Tests for drag-and-drop file insertion in the TUI prompt."""

from pathlib import Path

import pytest
from textual import events

from tau_coding.tui.app import PromptInput, TauTuiApp
from tau_coding.tui.file_drop import normalize_dropped_paths


def _escaped(path: Path) -> str:
    """Render a path the way terminals do when a file is dropped."""
    return str(path).replace(" ", "\\ ")


class TestNormalizeDroppedPaths:
    def test_plain_absolute_path_passes_through(self, tmp_path: Path) -> None:
        file = tmp_path / "notes.txt"
        file.touch()

        assert normalize_dropped_paths(str(file)) == str(file)

    def test_escaped_spaces_are_unescaped_and_quoted(self, tmp_path: Path) -> None:
        file = tmp_path / "my file.png"
        file.touch()

        assert normalize_dropped_paths(_escaped(file)) == f'"{file}"'

    def test_bare_path_with_spaces_is_quoted(self, tmp_path: Path) -> None:
        file = tmp_path / "my file.png"
        file.touch()

        assert normalize_dropped_paths(str(file)) == f'"{file}"'

    def test_double_quoted_path_is_normalized(self, tmp_path: Path) -> None:
        file = tmp_path / "my file.png"
        file.touch()

        assert normalize_dropped_paths(f'"{file}"') == f'"{file}"'

    def test_multiple_dropped_files_join_with_spaces(self, tmp_path: Path) -> None:
        first = tmp_path / "first.txt"
        second = tmp_path / "second file.txt"
        first.touch()
        second.touch()

        dropped = f"{first} {_escaped(second)}"

        assert normalize_dropped_paths(dropped) == f'{first} "{second}"'

    def test_newline_separated_paths_join_with_spaces(self, tmp_path: Path) -> None:
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.touch()
        second.touch()

        assert normalize_dropped_paths(f"{first}\n{second}\n") == f"{first} {second}"

    def test_directory_path_is_accepted(self, tmp_path: Path) -> None:
        directory = tmp_path / "some dir"
        directory.mkdir()

        assert normalize_dropped_paths(_escaped(directory)) == f'"{directory}"'

    def test_file_uri_is_converted_to_local_path(self, tmp_path: Path) -> None:
        file = tmp_path / "my file.png"
        file.touch()
        uri = "file://" + str(file).replace(" ", "%20")

        assert normalize_dropped_paths(uri) == f'"{file}"'

    def test_missing_path_is_not_a_drop(self, tmp_path: Path) -> None:
        assert normalize_dropped_paths(str(tmp_path / "missing.txt")) is None

    def test_relative_path_is_not_a_drop(self) -> None:
        assert normalize_dropped_paths("pyproject.toml") is None

    def test_prose_is_not_a_drop(self) -> None:
        assert normalize_dropped_paths("please summarize /tmp") is None

    def test_blank_text_is_not_a_drop(self) -> None:
        assert normalize_dropped_paths("   \n ") is None

    def test_unbalanced_quotes_are_not_a_drop(self, tmp_path: Path) -> None:
        file = tmp_path / "notes.txt"
        file.touch()

        assert normalize_dropped_paths(f'"{file}') is None


class TestPromptInputFileDrop:
    def test_drop_inserts_path_into_empty_prompt(self, tmp_path: Path) -> None:
        prompt = PromptInput()
        file = tmp_path / "notes.txt"
        file.touch()

        prompt.on_paste(events.Paste(str(file)))

        assert prompt.text == f"{file} "

    def test_drop_quotes_path_with_spaces(self, tmp_path: Path) -> None:
        prompt = PromptInput()
        file = tmp_path / "my file.png"
        file.touch()

        prompt.on_paste(events.Paste(_escaped(file)))

        assert prompt.text == f'"{file}" '

    def test_drop_preserves_existing_text(self, tmp_path: Path) -> None:
        prompt = PromptInput()
        file = tmp_path / "notes.txt"
        file.touch()
        prompt.text = "summarize "
        prompt.move_cursor((0, len(prompt.text)))

        prompt.on_paste(events.Paste(str(file)))

        assert prompt.text == f"summarize {file} "

    def test_drop_separates_from_preceding_text(self, tmp_path: Path) -> None:
        prompt = PromptInput()
        file = tmp_path / "notes.txt"
        file.touch()
        prompt.text = "summarize"
        prompt.move_cursor((0, len(prompt.text)))

        prompt.on_paste(events.Paste(str(file)))

        assert prompt.text == f"summarize {file} "

    def test_drop_mid_text_separates_both_sides(self, tmp_path: Path) -> None:
        prompt = PromptInput()
        file = tmp_path / "notes.txt"
        file.touch()
        prompt.text = "comparethese"
        prompt.cursor_position = len("compare")

        prompt.on_paste(events.Paste(str(file)))

        assert prompt.text == f"compare {file} these"

    def test_non_path_paste_keeps_default_behavior(self) -> None:
        prompt = PromptInput()
        event = events.Paste("just some regular text")

        prompt.on_paste(event)

        assert prompt.text == ""

    def test_insert_pasted_text_inserts_dropped_path(self, tmp_path: Path) -> None:
        prompt = PromptInput()
        file = tmp_path / "notes.txt"
        file.touch()

        prompt.insert_pasted_text(str(file))

        assert prompt.text == f"{file} "

    def test_insert_pasted_text_inserts_plain_text_verbatim(self) -> None:
        prompt = PromptInput()

        prompt.insert_pasted_text("just some regular text")

        assert prompt.text == "just some regular text"


class TestUnfocusedDropRouting:
    """Drops delivered while the terminal reports no focus must not be lost.

    Textual clears widget focus on ``AppBlur`` and discards pastes when nothing
    is focused, which is how macOS Dock drags arrive.
    """

    @pytest.mark.anyio
    async def test_drop_while_blurred_reaches_prompt(self, tmp_path: Path) -> None:
        from test_tui_app import FakeSession

        file = tmp_path / "notes.txt"
        file.touch()
        app = TauTuiApp(FakeSession())

        async with app.run_test() as pilot:
            app.post_message(events.AppBlur())
            await pilot.pause()
            assert app.focused is None

            app.post_message(events.Paste(str(file)))
            await pilot.pause()

            prompt = app.query_one("#prompt", PromptInput)
            assert prompt.text == f"{file} "

    @pytest.mark.anyio
    async def test_focused_drop_is_not_inserted_twice(self, tmp_path: Path) -> None:
        from test_tui_app import FakeSession

        file = tmp_path / "notes.txt"
        file.touch()
        app = TauTuiApp(FakeSession())

        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt", PromptInput)
            prompt.focus()
            await pilot.pause()

            app.post_message(events.Paste(str(file)))
            await pilot.pause()

            assert prompt.text == f"{file} "
