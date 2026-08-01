from pathlib import Path
from subprocess import CompletedProcess

import pytest

from tau_coding.tui.external_editor import (
    ExternalEditorError,
    edit_prompt_in_external_editor,
    resolve_external_editor_command,
)


def test_external_editor_command_prefers_visual_then_editor() -> None:
    assert resolve_external_editor_command({"VISUAL": "nvim", "EDITOR": "vim"}) == "nvim"
    assert resolve_external_editor_command({"VISUAL": "  ", "EDITOR": "vim"}) == "vim"
    assert resolve_external_editor_command({"EDITOR": "vim"}) == "vim"


def test_external_editor_updates_prompt_and_removes_one_final_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_path: Path | None = None

    def fake_run(arguments: list[str], *, check: bool) -> CompletedProcess[str]:
        nonlocal observed_path
        assert arguments[:2] == ["code", "--wait"]
        assert check is False
        observed_path = Path(arguments[-1])
        assert observed_path.read_text(encoding="utf-8") == "original prompt"
        observed_path.write_text("edited prompt\n", encoding="utf-8")
        return CompletedProcess(arguments, 0)

    monkeypatch.setattr("tau_coding.tui.external_editor.subprocess.run", fake_run)

    edited = edit_prompt_in_external_editor(
        "original prompt",
        editor_command="code --wait",
    )

    assert edited == "edited prompt"
    assert observed_path is not None
    assert not observed_path.exists()


def test_external_editor_failure_preserves_temp_file_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_path: Path | None = None

    def fake_run(arguments: list[str], *, check: bool) -> CompletedProcess[str]:
        nonlocal observed_path
        del check
        observed_path = Path(arguments[-1])
        return CompletedProcess(arguments, 7)

    monkeypatch.setattr("tau_coding.tui.external_editor.subprocess.run", fake_run)

    with pytest.raises(ExternalEditorError, match="exited with code 7"):
        edit_prompt_in_external_editor("unchanged", editor_command="vim")

    assert observed_path is not None
    assert not observed_path.exists()
