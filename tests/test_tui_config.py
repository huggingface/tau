from pathlib import Path

import pytest

from tau_coding.paths import TauPaths
from tau_coding.tui.config import (
    HIGH_CONTRAST_THEME,
    TuiConfigError,
    TuiKeybindings,
    TuiSettings,
    get_tui_theme,
    load_tui_settings,
    save_tui_settings,
    tui_settings_from_json,
    tui_settings_path,
)


def test_tui_settings_path_uses_tau_home(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    assert tui_settings_path(paths) == tmp_path / ".tau" / "tui.json"


def test_load_tui_settings_returns_defaults_when_file_is_missing(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    assert load_tui_settings(paths) == TuiSettings()
    assert load_tui_settings(paths).keybindings.quit == "ctrl+d"


def test_load_tui_settings_reads_keybindings(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    path = tui_settings_path(paths)
    path.parent.mkdir(parents=True)
    path.write_text(
        """
        {
          "keybindings": {
            "command_palette": "ctrl+j",
            "session_picker": "ctrl+y",
            "queue_follow_up": "f5",
            "accept_completion": "f2",
            "thinking_cycle": "f3",
            "model_cycle": "f6",
            "toggle_thinking": "f4",
            "copy_message": "ctrl+b"
          },
          "theme": "high-contrast"
        }
        """,
        encoding="utf-8",
    )

    settings = load_tui_settings(paths)

    assert settings.keybindings.command_palette == "ctrl+j"
    assert settings.keybindings.session_picker == "ctrl+y"
    assert settings.keybindings.queue_follow_up == "f5"
    assert settings.keybindings.toggle_tool_results == "ctrl+o"
    assert settings.keybindings.toggle_thinking == "f4"
    assert settings.keybindings.accept_completion == "f2"
    assert settings.keybindings.thinking_cycle == "f3"
    assert settings.keybindings.model_cycle == "f6"
    assert settings.keybindings.copy_message == "ctrl+b"
    assert settings.keybindings.cancel == "escape"
    assert settings.theme == "high-contrast"
    assert settings.resolved_theme == HIGH_CONTRAST_THEME


def test_save_tui_settings_writes_json(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    path = save_tui_settings(TuiSettings(theme="tau-light"), paths)

    assert path == tmp_path / ".tau" / "tui.json"
    assert load_tui_settings(paths).theme == "tau-light"


def test_tui_settings_ignores_removed_message_selection_keybindings() -> None:
    settings = tui_settings_from_json(
        {
            "keybindings": {
                "message_previous": "alt+up",
                "message_next": "alt+down",
            }
        }
    )

    assert settings == TuiSettings()


def test_tui_settings_reject_unknown_fields() -> None:
    with pytest.raises(TuiConfigError, match="Unknown TUI settings field"):
        tui_settings_from_json({"palette": {}})


def test_tui_keybindings_reject_duplicate_keys() -> None:
    with pytest.raises(TuiConfigError, match="assigned to both"):
        tui_settings_from_json(
            {
                "keybindings": {
                    "cancel": "escape",
                    "command_palette": "escape",
                }
            }
        )


def test_tui_settings_reject_unknown_theme() -> None:
    with pytest.raises(TuiConfigError, match="Unknown TUI theme"):
        tui_settings_from_json({"theme": "solarized"})


def test_tui_settings_accept_light_theme() -> None:
    settings = tui_settings_from_json({"theme": "tau-light"})

    assert settings.theme == "tau-light"
    assert settings.resolved_theme.screen_background == "#ffffff"
    assert settings.resolved_theme.syntax_theme == "ansi_light"


def test_tui_keybindings_serialize_to_json() -> None:
    settings = TuiSettings(
        keybindings=TuiKeybindings(
            command_palette="ctrl+j",
            session_picker="ctrl+y",
            queue_follow_up="f5",
            accept_completion="f2",
            thinking_cycle="f3",
            model_cycle="f6",
            toggle_thinking="f4",
            copy_message="ctrl+b",
        ),
        theme="high-contrast",
    )

    assert settings.to_json()["keybindings"]["command_palette"] == "ctrl+j"
    assert settings.to_json()["keybindings"]["session_picker"] == "ctrl+y"
    assert settings.to_json()["keybindings"]["queue_follow_up"] == "f5"
    assert settings.to_json()["keybindings"]["toggle_tool_results"] == "ctrl+o"
    assert settings.to_json()["keybindings"]["toggle_thinking"] == "f4"
    assert settings.to_json()["keybindings"]["accept_completion"] == "f2"
    assert settings.to_json()["keybindings"]["thinking_cycle"] == "f3"
    assert settings.to_json()["keybindings"]["model_cycle"] == "f6"
    assert settings.to_json()["keybindings"]["copy_message"] == "ctrl+b"
    assert settings.to_json()["theme"] == "high-contrast"


def test_get_tui_theme_returns_builtin_theme() -> None:
    assert get_tui_theme("high-contrast").prompt_border == "#00ff66"
    assert get_tui_theme("tau-light").prompt_border == "#2563eb"
    assert get_tui_theme("tau-dark").screen_background == "#000000"
