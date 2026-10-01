"""Exercise actual terminal decoding, not just synthetic pilot key names."""

import asyncio

import pytest
from textual import events
from textual._xterm_parser import XTermParser
from textual.app import App, ComposeResult

from tau_coding.tui.app import PromptInput


@pytest.mark.parametrize(
    ("sequence", "key"),
    [
        ("\x1b[1;3D", "alt+left"),
        ("\x1b[1;3C", "alt+right"),
        ("\x1b[1;4D", "alt+shift+left"),
        ("\x1b[1;4C", "alt+shift+right"),
        ("\x1bb", "ctrl+left"),
        ("\x1bf", "ctrl+right"),
        ("\x1b[1;5D", "ctrl+left"),
        ("\x1b[1;5C", "ctrl+right"),
        ("\x1b[D", "left"),
        ("\x1b[C", "right"),
        ("\x1b", "escape"),
    ],
)
def test_terminal_word_navigation_decoding(sequence: str, key: str) -> None:
    parser = XTermParser()
    messages = list(parser.feed(sequence)) + list(parser.feed(""))
    assert len(messages) == 1
    assert isinstance(messages[0], events.Key)
    assert messages[0].key == key


@pytest.mark.parametrize("select", [False, True])
def test_option_arrows_move_prompt_by_word(select: bool) -> None:
    class EditorApp(App[None]):
        def compose(self) -> ComposeResult:
            yield PromptInput()

    async def run() -> None:
        app = EditorApp()
        async with app.run_test() as pilot:
            prompt = app.query_one(PromptInput)
            prompt.text = "one two three"
            prompt.cursor_position = len(prompt.text)
            prompt.focus()
            modifier = 4 if select else 3
            for direction, position in [("D", 8), ("D", 4), ("C", 7)]:
                messages = list(XTermParser().feed(f"\x1b[1;{modifier}{direction}"))
                assert len(messages) == 1
                app.post_message(messages[0])
                await pilot.pause()
                assert prompt.cursor_position == position
            assert prompt.selected_text == (" three" if select else "")

    asyncio.run(run())
