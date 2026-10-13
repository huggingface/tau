"""Tests for clipboard image materialization in the TUI."""

from pathlib import Path

import pytest
from PIL import Image

from tau_coding.tui import clipboard
from tau_coding.tui.app import PromptInput, TauTuiApp


@pytest.mark.anyio
async def test_prompt_ctrl_v_inserts_clipboard_image_path(tmp_path: Path) -> None:
    from test_tui_app import FakeSession

    image_path = tmp_path / "tau-clipboard-test.png"
    image_path.touch()

    async def read_clipboard() -> str:
        return str(image_path)

    app = TauTuiApp(FakeSession())
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", PromptInput)
        prompt._clipboard_reader = read_clipboard
        prompt.text = "inspect"
        prompt.move_cursor((0, len(prompt.text)))

        await pilot.press("ctrl+v")
        await pilot.pause()

        assert prompt.text == f"inspect {image_path} "


@pytest.mark.anyio
async def test_prompt_ctrl_v_falls_back_to_clipboard_text() -> None:
    from test_tui_app import FakeSession

    async def read_clipboard() -> str:
        return "clipboard text"

    app = TauTuiApp(FakeSession())
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", PromptInput)
        prompt._clipboard_reader = read_clipboard

        await pilot.press("ctrl+v")
        await pilot.pause()

        assert prompt.text == "clipboard text"


def test_materialize_clipboard_image_writes_png(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(clipboard.ImageGrab, "grabclipboard", lambda: Image.new("RGB", (3, 2)))

    path = clipboard.materialize_clipboard_image(temp_dir=tmp_path)

    assert path is not None
    assert path.parent == tmp_path
    assert path.name.startswith("tau-clipboard-")
    assert path.suffix == ".png"
    with Image.open(path) as saved:
        assert saved.format == "PNG"
        assert saved.size == (3, 2)


def test_materialize_clipboard_image_returns_none_for_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(clipboard.ImageGrab, "grabclipboard", lambda: None)

    assert clipboard.materialize_clipboard_image(temp_dir=tmp_path) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_read_clipboard_prefers_image_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "tau-clipboard-test.png"
    image_path.touch()
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(clipboard, "materialize_clipboard_image", lambda: image_path)
    monkeypatch.setattr(clipboard, "_read_macos_clipboard_text", lambda: "fallback text")

    assert await clipboard.read_clipboard_for_prompt() == str(image_path)


@pytest.mark.anyio
async def test_read_clipboard_falls_back_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(clipboard, "materialize_clipboard_image", lambda: None)
    monkeypatch.setattr(clipboard, "_read_macos_clipboard_text", lambda: "clipboard text")

    assert await clipboard.read_clipboard_for_prompt() == "clipboard text"


@pytest.mark.anyio
async def test_read_clipboard_is_unsupported_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")

    assert await clipboard.read_clipboard_for_prompt() is None
