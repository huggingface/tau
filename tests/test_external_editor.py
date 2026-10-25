"""Tests for external-editor resolution and invocation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tau_coding.tui.external_editor import (
    ExternalEditorError,
    edit_text_in_external_editor,
    resolve_editor_command,
)


def test_resolve_editor_command_prefers_visual_over_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISUAL", "code --wait")
    monkeypatch.setenv("EDITOR", "nano")

    assert resolve_editor_command() == ["code", "--wait"]


def test_resolve_editor_command_uses_editor_when_visual_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "nano")

    assert resolve_editor_command() == ["nano"]


def test_resolve_editor_command_falls_back_when_neither_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("os.name", "posix")

    assert resolve_editor_command() == ["vi"]


def test_resolve_editor_command_falls_back_to_notepad_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("os.name", "nt")

    assert resolve_editor_command() == ["notepad"]


def test_resolve_editor_command_splits_quoted_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", '"my editor" --flag')

    assert resolve_editor_command() == ["my editor", "--flag"]


def test_resolve_editor_command_ignores_blank_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISUAL", "   ")
    monkeypatch.setenv("EDITOR", "nano")

    assert resolve_editor_command() == ["nano"]


def _fake_editor_that_appends(text: str) -> subprocess.CompletedProcess[bytes]:
    """Simulate an editor that appends to the file and exits cleanly."""

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        path = Path(argv[-1])
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    return run  # type: ignore[return-value]


def test_edit_text_in_external_editor_returns_saved_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDITOR", "fake-editor")
    monkeypatch.setattr(
        "tau_coding.tui.external_editor.subprocess.run",
        _fake_editor_that_appends(" world\n"),
    )

    result = edit_text_in_external_editor("hello")

    assert result.text == "hello world"


def test_edit_text_in_external_editor_cleans_up_temp_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDITOR", "fake-editor")
    seen_paths: list[Path] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        path = Path(argv[-1])
        seen_paths.append(path)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("tau_coding.tui.external_editor.subprocess.run", run)

    edit_text_in_external_editor("hello")

    assert seen_paths
    assert not seen_paths[0].parent.exists()


def test_edit_text_in_external_editor_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDITOR", "fake-editor")

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr("tau_coding.tui.external_editor.subprocess.run", run)

    with pytest.raises(ExternalEditorError, match="exited with status 1"):
        edit_text_in_external_editor("hello")


def test_edit_text_in_external_editor_raises_when_editor_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDITOR", "nonexistent-editor-binary")

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr("tau_coding.tui.external_editor.subprocess.run", run)

    with pytest.raises(ExternalEditorError, match="Could not start editor"):
        edit_text_in_external_editor("hello")
