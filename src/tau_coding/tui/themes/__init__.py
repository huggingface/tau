"""JSON-defined TUI themes for Tau.

Themes are data, not code. The built-in themes ship as JSON files next to this
module and load through the same parser as user themes, which live in
``~/.tau/themes/*.json`` and ``<project>/.tau/themes/*.json``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from functools import cache
from importlib.resources import files
from json import JSONDecodeError, loads
from pathlib import Path
from typing import Literal, get_args

from rich.color import Color, ColorParseError
from rich.errors import StyleSyntaxError
from rich.style import Style
from textual.color import Color as TextualColor
from textual.color import ColorParseError as TextualColorParseError

from tau_coding.resources import ResourceDiagnostic


class TuiThemeError(ValueError):
    """Raised when a TUI theme definition is invalid."""


@dataclass(frozen=True, slots=True)
class TuiRoleStyle:
    """Colors for one transcript role block."""

    border: str
    body: str


@dataclass(frozen=True, slots=True)
class TuiTheme:
    """Resolved visual theme for Tau's built-in Textual frontend."""

    name: str
    dark: bool
    screen_background: str
    screen_text: str
    chrome_background: str
    chrome_text: str
    muted_text: str
    sidebar_background: str
    border: str
    transcript_background: str
    prompt_background: str
    prompt_text: str
    prompt_border: str
    autocomplete_background: str
    accent: str
    success: str
    error: str
    tool_success_text: str
    tool_error_text: str
    highlight_background: str
    highlight_text: str
    markdown_heading: str
    markdown_table_header: str
    markdown_table_border: str
    markdown_inline_code: str
    markdown_code_block_background: str
    markdown_link: str
    markdown_bullet: str
    completion_selected: str
    completion_selected_description: str
    completion_description: str
    syntax_theme: str
    role_styles: dict[str, TuiRoleStyle]


type TuiThemeName = str

THEME_COLOR_FIELDS: tuple[str, ...] = tuple(
    theme_field.name
    for theme_field in fields(TuiTheme)
    if theme_field.name not in {"name", "dark", "syntax_theme", "role_styles"}
)

# Single source of truth for transcript roles: the theme schema requires a
# style for each, and tui.state aliases this as ChatItemRole.
TranscriptRole = Literal[
    "user",
    "assistant",
    "tool",
    "error",
    "status",
    "thinking",
    "skill",
    "custom",
    "branch_summary",
    "compaction_summary",
]
TRANSCRIPT_ROLES: tuple[str, ...] = get_args(TranscriptRole)

_TOP_LEVEL_FIELDS = {"$schema", "name", "dark", "vars", "syntax_theme", "colors", "roles"}

# Fields that feed Textual CSS variables and must be a single color, unlike
# the completion fields and role bodies, which are full Rich style strings.
_RICH_STYLE_FIELDS = {
    "completion_selected",
    "completion_selected_description",
    "completion_description",
}

# Single-color fields only ever rendered through Rich. Every other color field
# reaches Textual too (CSS variables, Theme slots, or widget styles), and Rich
# accepts colors Textual rejects (bright_red, grey50, color(1), default), so
# those fields must parse under both libraries. New fields default to the
# strict dual check: a field missing from this set rejects a theme with a
# diagnostic instead of crashing the TUI when the theme is applied.
_RICH_ONLY_COLOR_FIELDS = {
    "tool_success_text",
    "tool_error_text",
}

# Var names that would corrupt Rich style strings during token substitution.
_RICH_STYLE_KEYWORDS = frozenset(
    {
        "on",
        "not",
        "none",
        "default",
        "b",
        "bold",
        "d",
        "dim",
        "i",
        "italic",
        "u",
        "underline",
        "uu",
        "underline2",
        "s",
        "strike",
        "r",
        "reverse",
        "blink",
        "blink2",
        "conceal",
        "o",
        "overline",
        "frame",
        "encircle",
        "link",
    }
)


def parse_tui_theme_json(data: object) -> TuiTheme:
    """Parse a theme from JSON-compatible data, reporting all problems at once."""
    if not isinstance(data, dict):
        raise TuiThemeError("Theme must be a JSON object")

    problems: list[str] = []
    for key in sorted(set(data) - _TOP_LEVEL_FIELDS):
        problems.append(f"unknown field: {key}")

    name = _parse_name(data.get("name"), problems)
    variables = _parse_vars(data.get("vars", {}), problems)
    colors = _parse_colors(data.get("colors"), variables, problems)
    role_styles = _parse_roles(data.get("roles"), variables, problems)
    dark = _parse_dark(data.get("dark"), colors, problems)
    syntax_theme = _parse_syntax_theme(data.get("syntax_theme"), dark=dark, problems=problems)

    if problems:
        label = f"theme {name!r}" if name else "theme"
        raise TuiThemeError(f"Invalid {label}: " + "; ".join(problems))
    return TuiTheme(
        name=name,
        dark=dark,
        syntax_theme=syntax_theme,
        role_styles=role_styles,
        **colors,
    )


def _parse_name(value: object, problems: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        problems.append("name must be a non-empty string")
        return ""
    name = value.strip()
    if "/" in name:
        problems.append("name must not contain '/'")
    return name


def _parse_vars(value: object, problems: list[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        problems.append("vars must be an object")
        return {}
    variables: dict[str, str] = {}
    for var_name, var_value in value.items():
        name_allowed = isinstance(var_name, str) and var_name
        if not name_allowed or var_name.lower() in _RICH_STYLE_KEYWORDS:
            problems.append(f"vars name is not allowed: {var_name!r}")
            continue
        if (
            not isinstance(var_value, str)
            or len(var_value.split()) != 1
            or _color_problem(var_value) is not None
        ):
            problems.append(f"vars value must be a single color: {var_name}")
            continue
        variables[var_name] = var_value
    return variables


def _substitute_vars(value: str, variables: dict[str, str]) -> str:
    if not variables:
        return value
    return " ".join(variables.get(token, token) for token in value.split())


def _parse_colors(
    value: object,
    variables: dict[str, str],
    problems: list[str],
) -> dict[str, str]:
    if not isinstance(value, dict):
        problems.append("colors must be an object")
        return {}
    missing = [field_name for field_name in THEME_COLOR_FIELDS if field_name not in value]
    if missing:
        problems.append("colors missing: " + ", ".join(missing))
    unknown = sorted(set(value) - set(THEME_COLOR_FIELDS))
    if unknown:
        problems.append("colors unknown: " + ", ".join(unknown))
    colors: dict[str, str] = {}
    for field_name in THEME_COLOR_FIELDS:
        if field_name not in value:
            continue
        raw = value[field_name]
        if not isinstance(raw, str) or not raw.strip():
            problems.append(f"colors.{field_name} must be a non-empty string")
            continue
        resolved = _substitute_vars(raw.strip(), variables)
        if field_name in _RICH_STYLE_FIELDS:
            error = _style_problem(resolved)
        elif field_name in _RICH_ONLY_COLOR_FIELDS:
            error = _color_problem(resolved)
        else:
            error = _color_problem(resolved) or _textual_color_problem(resolved)
        if error is not None:
            problems.append(f"colors.{field_name} {error}")
            continue
        colors[field_name] = resolved
    return colors


def _parse_roles(
    value: object,
    variables: dict[str, str],
    problems: list[str],
) -> dict[str, TuiRoleStyle]:
    if not isinstance(value, dict):
        problems.append("roles must be an object")
        return {}
    missing = [role for role in TRANSCRIPT_ROLES if role not in value]
    if missing:
        problems.append("roles missing: " + ", ".join(missing))
    unknown = sorted(set(value) - set(TRANSCRIPT_ROLES))
    if unknown:
        problems.append("roles unknown: " + ", ".join(unknown))
    role_styles: dict[str, TuiRoleStyle] = {}
    for role in TRANSCRIPT_ROLES:
        if role not in value:
            continue
        raw = value[role]
        if not isinstance(raw, dict) or set(raw) != {"border", "body"}:
            problems.append(f"roles.{role} must be an object with 'border' and 'body'")
            continue
        border, body = raw["border"], raw["body"]
        if not isinstance(border, str) or not isinstance(body, str):
            problems.append(f"roles.{role} border and body must be strings")
            continue
        resolved_border = _substitute_vars(border.strip(), variables)
        resolved_body = _substitute_vars(body.strip(), variables)
        # Borders feed Textual's styles.border_left as well as Rich tables.
        border_error = _color_problem(resolved_border) or _textual_color_problem(resolved_border)
        if border_error is not None:
            problems.append(f"roles.{role}.border {border_error}")
        # Body colors also feed Textual's styles.color/background in the
        # transcript, so both style components must satisfy Textual too.
        body_error = _style_problem(resolved_body) or _textual_style_colors_problem(resolved_body)
        if body_error is not None:
            problems.append(f"roles.{role}.body {body_error}")
        if border_error is None and body_error is None:
            role_styles[role] = TuiRoleStyle(border=resolved_border, body=resolved_body)
    return role_styles


def _color_problem(value: str) -> str | None:
    try:
        Color.parse(value)
    except ColorParseError:
        return f"is not a valid color: {value!r}"
    return None


def _textual_color_problem(value: str) -> str | None:
    try:
        TextualColor.parse(value)
    except TextualColorParseError:
        return f"is not a color Textual accepts: {value!r}"
    return None


def _textual_style_colors_problem(value: str) -> str | None:
    style = Style.parse(value)
    for color in (style.color, style.bgcolor):
        if color is not None and color.name is not None:
            error = _textual_color_problem(color.name)
            if error is not None:
                return error
    return None


def _style_problem(value: str) -> str | None:
    try:
        Style.parse(value)
    except (StyleSyntaxError, ColorParseError):
        return f"is not a valid style: {value!r}"
    return None


def _parse_dark(value: object, colors: dict[str, str], problems: list[str]) -> bool:
    if isinstance(value, bool):
        return value
    if value is not None:
        problems.append("dark must be a boolean")
    return _is_dark_background(colors.get("screen_background", ""))


def _is_dark_background(value: str) -> bool:
    tokens = value.split()
    if not tokens:
        return True
    try:
        color = Color.parse(tokens[0])
    except ColorParseError:
        return True
    red, green, blue = color.get_truecolor()
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
    return luminance < 0.5


def _parse_syntax_theme(value: object, *, dark: bool, problems: list[str]) -> str:
    if value is None:
        return "ansi_dark" if dark else "ansi_light"
    if not isinstance(value, str) or value not in _known_syntax_themes():
        problems.append(f"unknown syntax_theme: {value!r}")
        return "ansi_dark"
    return value


@cache
def _known_syntax_themes() -> frozenset[str]:
    from pygments.styles import get_all_styles

    return frozenset(get_all_styles()) | {"ansi_dark", "ansi_light"}


def _load_builtin_theme(filename: str) -> TuiTheme:
    payload = (files(__package__) / filename).read_text(encoding="utf-8")
    return parse_tui_theme_json(loads(payload))


TAU_DARK_THEME = _load_builtin_theme("tau-dark.json")
TAU_LIGHT_THEME = _load_builtin_theme("tau-light.json")
HIGH_CONTRAST_THEME = _load_builtin_theme("high-contrast.json")

_BUILTIN_THEMES: dict[str, TuiTheme] = {
    theme.name: theme for theme in (TAU_DARK_THEME, TAU_LIGHT_THEME, HIGH_CONTRAST_THEME)
}
BUILTIN_TUI_THEME_NAMES: tuple[TuiThemeName, ...] = tuple(_BUILTIN_THEMES)

_custom_themes: dict[str, TuiTheme] = {}


def set_custom_tui_themes(themes: Mapping[str, TuiTheme]) -> None:
    """Replace the registered custom themes."""
    _custom_themes.clear()
    _custom_themes.update(themes)


def available_tui_theme_names() -> tuple[TuiThemeName, ...]:
    """Return built-in theme names followed by sorted custom theme names."""
    return (*BUILTIN_TUI_THEME_NAMES, *sorted(_custom_themes))


def get_tui_theme(name: TuiThemeName = "tau-dark") -> TuiTheme:
    """Return a built-in or registered custom theme by name."""
    if name in _BUILTIN_THEMES:
        return _BUILTIN_THEMES[name]
    return _custom_themes[name]


def load_custom_tui_themes(
    theme_dirs: Sequence[Path],
) -> tuple[dict[str, TuiTheme], list[ResourceDiagnostic]]:
    """Load custom themes from directories given in increasing precedence order.

    Invalid files are skipped with a diagnostic; on duplicate names the theme
    from the higher-precedence directory (or earlier file within one directory)
    wins and the loser is reported as a collision.
    """
    themes: dict[str, TuiTheme] = {}
    diagnostics: list[ResourceDiagnostic] = []
    for directory in reversed(list(theme_dirs)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                data = loads(path.read_text(encoding="utf-8"))
            except (OSError, JSONDecodeError, UnicodeDecodeError) as error:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="invalid-theme",
                        message=f"could not parse theme JSON: {error}",
                        path=path,
                    )
                )
                continue
            try:
                theme = parse_tui_theme_json(data)
            except TuiThemeError as error:
                diagnostics.append(
                    ResourceDiagnostic(kind="invalid-theme", message=str(error), path=path)
                )
                continue
            if theme.name in _BUILTIN_THEMES:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="collision",
                        message=f"theme {theme.name!r} shadows a built-in theme and was ignored",
                        path=path,
                        name=theme.name,
                    )
                )
                continue
            if theme.name in themes:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="collision",
                        message=f"theme {theme.name!r} is already defined with higher precedence",
                        path=path,
                        name=theme.name,
                    )
                )
                continue
            themes[theme.name] = theme
    return themes, diagnostics
