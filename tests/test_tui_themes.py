from collections.abc import Iterator
from json import dumps
from pathlib import Path
from typing import Any

import pytest

from tau_coding.resources import TauResourcePaths
from tau_coding.tui.config import TuiSettings
from tau_coding.tui.themes import (
    BUILTIN_TUI_THEME_NAMES,
    TAU_DARK_THEME,
    TAU_LIGHT_THEME,
    THEME_COLOR_FIELDS,
    TRANSCRIPT_ROLES,
    TuiThemeError,
    available_tui_theme_names,
    get_tui_theme,
    load_custom_tui_themes,
    parse_tui_theme_json,
    set_custom_tui_themes,
)


@pytest.fixture(autouse=True)
def _reset_custom_themes() -> Iterator[None]:
    yield
    set_custom_tui_themes({})


def _theme_data(name: str = "midnight", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": name,
        "colors": {field_name: "#101010" for field_name in THEME_COLOR_FIELDS},
        "roles": {role: {"border": "#101010", "body": "#e0e0e0"} for role in TRANSCRIPT_ROLES},
    }
    data.update(overrides)
    return data


def test_parse_theme_resolves_vars_inside_style_strings() -> None:
    data = _theme_data(vars={"base": "#1e1e2e", "teal": "#94e2d5"})
    data["colors"]["screen_background"] = "base"
    data["colors"]["completion_selected"] = "bold base on teal"
    data["roles"]["user"] = {"border": "teal", "body": "#cdd6f4 on base"}

    theme = parse_tui_theme_json(data)

    assert theme.name == "midnight"
    assert theme.screen_background == "#1e1e2e"
    assert theme.completion_selected == "bold #1e1e2e on #94e2d5"
    assert theme.role_styles["user"].border == "#94e2d5"
    assert theme.role_styles["user"].body == "#cdd6f4 on #1e1e2e"


def test_parse_theme_defaults_dark_and_syntax_theme_from_background() -> None:
    dark_data = _theme_data()
    dark_data["colors"]["screen_background"] = "#1e1e2e"
    light_data = _theme_data(name="daylight")
    light_data["colors"]["screen_background"] = "#eff1f5"

    dark_theme = parse_tui_theme_json(dark_data)
    light_theme = parse_tui_theme_json(light_data)

    assert dark_theme.dark is True
    assert dark_theme.syntax_theme == "ansi_dark"
    assert light_theme.dark is False
    assert light_theme.syntax_theme == "ansi_light"


def test_parse_theme_honors_explicit_dark_and_syntax_theme() -> None:
    data = _theme_data(dark=False, syntax_theme="ansi_light")
    data["colors"]["screen_background"] = "#000000"

    theme = parse_tui_theme_json(data)

    assert theme.dark is False
    assert theme.syntax_theme == "ansi_light"


def test_parse_theme_rejects_unparseable_colors_and_styles() -> None:
    data = _theme_data()
    data["colors"]["screen_background"] = "not-a-color"
    data["colors"]["accent"] = "bold #ffffff on #000000"
    data["colors"]["completion_selected"] = "bold nope on #000000"
    data["roles"]["user"] = {"border": "#12345", "body": "shiny on #000000"}

    with pytest.raises(TuiThemeError) as exc_info:
        parse_tui_theme_json(data)

    message = str(exc_info.value)
    assert "colors.screen_background" in message
    assert "colors.accent" in message
    assert "colors.completion_selected" in message
    assert "roles.user.border" in message
    assert "roles.user.body" in message


@pytest.mark.parametrize("color", ["bright_red", "color(123)", "grey50", "default"])
def test_parse_theme_rejects_rich_only_colors_in_textual_fields(color: str) -> None:
    # Rich's parser accepts these, but Textual's does not; they would crash
    # the TUI when the theme's CSS variables are applied.
    data = _theme_data()
    data["colors"]["accent"] = color

    with pytest.raises(TuiThemeError, match="colors.accent"):
        parse_tui_theme_json(data)


@pytest.mark.parametrize("color", ["tomato", "ansi_red", "#ff000080"])
def test_parse_theme_rejects_textual_only_colors_in_shared_fields(color: str) -> None:
    # Shared fields also render through Rich, so colors must satisfy both
    # parsers; Textual-only syntax is rejected just like Rich-only syntax.
    data = _theme_data()
    data["colors"]["accent"] = color

    with pytest.raises(TuiThemeError, match="colors.accent"):
        parse_tui_theme_json(data)


def test_parse_theme_allows_rich_only_colors_in_rich_only_fields() -> None:
    data = _theme_data()
    data["colors"]["tool_success_text"] = "bright_green"
    data["colors"]["tool_error_text"] = "color(160)"
    data["colors"]["completion_selected"] = "bold grey50 on #101010"

    theme = parse_tui_theme_json(data)

    assert theme.tool_success_text == "bright_green"
    assert theme.completion_selected == "bold grey50 on #101010"


@pytest.mark.parametrize("body", ["bright_white on #101010", "#e0e0e0 on grey11", "default"])
def test_parse_theme_rejects_rich_only_colors_in_role_bodies(body: str) -> None:
    # Body foreground/background colors feed Textual's styles.color and
    # styles.background, so they must parse under both libraries.
    data = _theme_data()
    data["roles"]["user"] = {"border": "#101010", "body": body}

    with pytest.raises(TuiThemeError, match="roles.user.body"):
        parse_tui_theme_json(data)


def test_parse_theme_rejects_rich_only_colors_in_role_borders() -> None:
    # Role borders feed Textual's styles.border_left as well as Rich tables.
    data = _theme_data()
    data["roles"]["user"] = {"border": "bright_red", "body": "#e0e0e0"}

    with pytest.raises(TuiThemeError, match="roles.user.border"):
        parse_tui_theme_json(data)


def test_parse_theme_rejects_rich_only_var_resolved_into_textual_field() -> None:
    data = _theme_data(vars={"base": "grey50"})
    data["colors"]["screen_background"] = "base"

    with pytest.raises(TuiThemeError, match="colors.screen_background"):
        parse_tui_theme_json(data)


def test_load_custom_themes_skips_textual_invalid_color_with_diagnostic(
    tmp_path: Path,
) -> None:
    themes_dir = tmp_path / "themes"
    crashy = _theme_data(name="crashy")
    crashy["colors"]["accent"] = "bright_red"
    _write_theme(themes_dir, "crashy.json", crashy)

    themes, diagnostics = load_custom_tui_themes([themes_dir])

    assert themes == {}
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == "invalid-theme"
    assert "colors.accent" in diagnostics[0].message


def test_parse_theme_reports_all_problems_at_once() -> None:
    data = _theme_data()
    del data["colors"]["accent"]
    del data["colors"]["border"]
    del data["roles"]["thinking"]
    data["colors"]["not_a_field"] = "#000000"

    with pytest.raises(TuiThemeError) as exc_info:
        parse_tui_theme_json(data)

    message = str(exc_info.value)
    assert "accent" in message
    assert "border" in message
    assert "thinking" in message
    assert "not_a_field" in message


def test_parse_theme_rejects_unknown_top_level_fields() -> None:
    with pytest.raises(TuiThemeError, match="palette"):
        parse_tui_theme_json(_theme_data(palette={}))


def test_parse_theme_allows_schema_field() -> None:
    data = _theme_data()
    data["$schema"] = "./theme-schema.json"

    assert parse_tui_theme_json(data).name == "midnight"


def test_parse_theme_rejects_var_names_that_collide_with_rich_keywords() -> None:
    with pytest.raises(TuiThemeError, match="on"):
        parse_tui_theme_json(_theme_data(vars={"on": "#101010"}))


def test_parse_theme_rejects_var_values_with_whitespace() -> None:
    with pytest.raises(TuiThemeError, match="base"):
        parse_tui_theme_json(_theme_data(vars={"base": "#101010 on #202020"}))


def test_parse_theme_rejects_var_values_that_are_not_colors() -> None:
    # A style keyword smuggled through a var would corrupt style strings.
    with pytest.raises(TuiThemeError, match="base"):
        parse_tui_theme_json(_theme_data(vars={"base": "bold"}))


def test_transcript_roles_are_shared_with_tui_state() -> None:
    from tau_coding.tui.state import ChatItemRole
    from tau_coding.tui.themes import TranscriptRole

    assert ChatItemRole is TranscriptRole


def test_parse_theme_rejects_invalid_names() -> None:
    with pytest.raises(TuiThemeError, match="name"):
        parse_tui_theme_json(_theme_data(name=""))
    with pytest.raises(TuiThemeError, match="name"):
        parse_tui_theme_json(_theme_data(name="light/dark"))


def test_parse_theme_rejects_unknown_syntax_theme() -> None:
    with pytest.raises(TuiThemeError, match="syntax_theme"):
        parse_tui_theme_json(_theme_data(syntax_theme="not-a-pygments-style"))


def test_parse_theme_rejects_non_object_payload() -> None:
    with pytest.raises(TuiThemeError, match="object"):
        parse_tui_theme_json(["not", "a", "theme"])


def test_builtin_themes_are_loaded_from_packaged_json() -> None:
    assert BUILTIN_TUI_THEME_NAMES == ("tau-dark", "tau-light", "high-contrast")
    assert TAU_DARK_THEME.dark is True
    assert TAU_LIGHT_THEME.dark is False
    assert get_tui_theme("tau-dark").screen_background == "#000000"
    assert get_tui_theme("high-contrast").prompt_border == "#00ff66"
    assert get_tui_theme("tau-light").syntax_theme == "ansi_light"


def test_custom_theme_registry_replaces_and_resolves() -> None:
    theme = parse_tui_theme_json(_theme_data(name="midnight"))
    other = parse_tui_theme_json(_theme_data(name="noon"))

    set_custom_tui_themes({"midnight": theme})
    set_custom_tui_themes({"noon": other})

    assert available_tui_theme_names() == (*BUILTIN_TUI_THEME_NAMES, "noon")
    assert get_tui_theme("noon") is other
    with pytest.raises(KeyError):
        get_tui_theme("midnight")


def test_resolved_theme_falls_back_to_tau_dark_for_unknown_names() -> None:
    settings = TuiSettings(theme="missing-theme")

    assert settings.resolved_theme == TAU_DARK_THEME


def test_resolved_theme_finds_registered_custom_theme() -> None:
    theme = parse_tui_theme_json(_theme_data(name="midnight"))
    set_custom_tui_themes({"midnight": theme})

    assert TuiSettings(theme="midnight").resolved_theme is theme


def _write_theme(directory: Path, filename: str, data: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(dumps(data), encoding="utf-8")
    return path


def test_load_custom_themes_discovers_json_files(tmp_path: Path) -> None:
    _write_theme(tmp_path / "themes", "midnight.json", _theme_data(name="midnight"))

    themes, diagnostics = load_custom_tui_themes([tmp_path / "themes"])

    assert list(themes) == ["midnight"]
    assert diagnostics == []


def test_load_custom_themes_prefers_higher_precedence_dirs(tmp_path: Path) -> None:
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    user_theme = _theme_data(name="midnight")
    user_theme["colors"]["accent"] = "#111111"
    project_theme = _theme_data(name="midnight")
    project_theme["colors"]["accent"] = "#222222"
    _write_theme(user_dir, "midnight.json", user_theme)
    _write_theme(project_dir, "midnight.json", project_theme)

    themes, diagnostics = load_custom_tui_themes([user_dir, project_dir])

    assert themes["midnight"].accent == "#222222"
    assert any(diagnostic.kind == "collision" for diagnostic in diagnostics)


def test_load_custom_themes_skips_invalid_files_with_diagnostics(tmp_path: Path) -> None:
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    (themes_dir / "broken.json").write_text("{not json", encoding="utf-8")
    incomplete = _theme_data(name="incomplete")
    del incomplete["colors"]["accent"]
    _write_theme(themes_dir, "incomplete.json", incomplete)
    _write_theme(themes_dir, "midnight.json", _theme_data(name="midnight"))

    themes, diagnostics = load_custom_tui_themes([themes_dir])

    assert list(themes) == ["midnight"]
    assert len(diagnostics) == 2


def test_load_custom_themes_skips_builtin_shadowing_with_diagnostic(tmp_path: Path) -> None:
    themes_dir = tmp_path / "themes"
    _write_theme(themes_dir, "tau-dark.json", _theme_data(name="tau-dark"))

    themes, diagnostics = load_custom_tui_themes([themes_dir])

    assert themes == {}
    assert any("tau-dark" in diagnostic.message for diagnostic in diagnostics)


def test_load_custom_themes_ignores_missing_dirs_and_non_json(tmp_path: Path) -> None:
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    (themes_dir / "notes.md").write_text("not a theme", encoding="utf-8")

    themes, diagnostics = load_custom_tui_themes([themes_dir, tmp_path / "missing"])

    assert themes == {}
    assert diagnostics == []


def test_tool_result_accents_derive_from_theme() -> None:
    from rich.console import Console

    from tau_coding.tui.state import ChatItem
    from tau_coding.tui.widgets import render_chat_item

    data = _theme_data(vars={"base": "#1e1e2e"})
    data["colors"]["tool_success_text"] = "#00fa9a"
    data["colors"]["tool_error_text"] = "#fa0064"
    data["roles"]["tool"] = {"border": "#101010", "body": "#e0e0e0 on base"}
    theme = parse_tui_theme_json(data)

    console = Console(record=True, width=80)
    console.print(
        render_chat_item(
            ChatItem(role="tool", text="→ read README.md", tool_result_text="✓ read\nok"),
            theme=theme,
            show_tool_results=True,
        )
    )
    console.print(
        render_chat_item(
            ChatItem(role="tool", text="$ false", tool_result_text="✗ bash\nfailed"),
            theme=theme,
            show_tool_results=True,
        )
    )
    output = console.export_text(styles=True)

    # Success/error accents use the theme tokens, and their background comes
    # from the tool body style, never a hardcoded black.
    assert "38;2;0;250;154" in output
    assert "38;2;250;0;100" in output
    assert "48;2;0;0;0" not in output
    assert "48;2;30;30;46" in output


def test_resource_paths_expose_theme_dirs(tmp_path: Path) -> None:
    paths = TauResourcePaths(root=tmp_path / ".tau", cwd=tmp_path / "project")

    assert paths.themes_dirs == (
        tmp_path / ".tau" / "themes",
        tmp_path / "project" / ".tau" / "themes",
    )
