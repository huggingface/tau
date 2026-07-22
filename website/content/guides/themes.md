---
title: Themes
description: Restyle the Tau TUI with JSON theme files, from single colors to full custom palettes.
---

Tau's TUI themes are JSON files. The built-in themes (`tau-dark`, `tau-light`,
`high-contrast`) ship as JSON inside the package and load through the same
parser as custom themes, so they double as reference examples:
[`src/tau_coding/tui/themes/`](https://github.com/huggingface/tau/tree/main/src/tau_coding/tui/themes).

## Adding a custom theme

Drop a `.json` file into one of the theme directories:

- `~/.tau/themes/` — available in every project
- `<project>/.tau/themes/` — project-local, wins over the user directory on
  name collisions

Themes are discovered at startup; restart Tau after adding or editing a file.
Invalid theme files are skipped with a startup notice — they never prevent Tau
from starting. Select a theme with `/theme <name>`, the `/theme` picker, or
Textual's command palette. The selection is persisted in `~/.tau/tui.json` —
see [Configuration]({{< relref "../reference/configuration.md#tui-settings" >}}).

## Theme format

```json
{
  "name": "my-theme",
  "dark": true,
  "syntax_theme": "ansi_dark",
  "vars": {
    "base": "#1e1e2e",
    "text": "#cdd6f4",
    "teal": "#94e2d5"
  },
  "colors": {
    "screen_background": "base",
    "screen_text": "text",
    "completion_selected": "bold base on teal",
    "…": "all color fields are required"
  },
  "roles": {
    "user": { "border": "teal", "body": "text on base" },
    "…": "all roles are required"
  }
}
```

- `name` (required) — unique; must not contain `/` and cannot shadow a
  built-in theme name.
- `dark` (optional) — whether the theme is dark. Defaults from the luminance
  of `screen_background`. Controls dark-vs-light rendering details such as
  tool-result colors.
- `syntax_theme` (optional) — Pygments style for fenced code blocks, or
  `ansi_dark` / `ansi_light`. Defaults from `dark`.
- `vars` (optional) — named colors. Any whitespace-separated token in a
  `colors` or `roles` value that matches a var name is replaced by its value,
  so compound Rich styles like `"bold base on teal"` work. Var names that
  collide with Rich style keywords (`on`, `bold`, `dim`, …) are rejected, and
  var values must be single color tokens.
- `colors` (required) — every field of the theme palette, including the
  `success` / `error` status tokens and the `tool_success_text` /
  `tool_error_text` colors for tool invocation text. The full list is
  `THEME_COLOR_FIELDS` in `src/tau_coding/tui/themes/__init__.py`; the
  built-in theme JSON files show them all in context.
  Most colors are rendered by both Rich and Textual, so stick to formats both
  accept — six-digit hex like `#94e2d5` is always safe. Library-specific
  syntax such as Rich's `bright_red` / `grey50` or Textual's `ansi_red` /
  `#ff000080` is rejected, except in the Rich-only `tool_success_text` /
  `tool_error_text` fields and the `completion_*` style strings.
- `roles` (required) — `border` and `body` styles for each transcript role:
  `user`, `assistant`, `tool`, `error`, `status`, `thinking`, `skill`,
  `custom` (extension messages), `branch_summary`, `compaction_summary`.
  `body` is a Rich style string, so
  it can carry a background (`"#cdd6f4 on #1e1e2e"`); its colors also tint
  the surrounding Textual widget, so like the palette colors they must use
  formats both libraries accept. The `border` is the
  colored bar beside each transcript block; completed tool calls override the
  `tool` border with `success` or `error`.

Validation reports every problem in a file at once, so a new theme can be
fixed in one pass.
