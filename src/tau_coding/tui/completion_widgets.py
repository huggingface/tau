"""Rendering for prompt-completion suggestions.

The completion state is shared by the TUI input and the app layout, while its
Rich rendering is intentionally independent of either widget lifecycle.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from tau_coding.tui.autocomplete import CompletionState
from tau_coding.tui.config import TAU_DARK_THEME, TuiTheme


def render_completion_suggestions(
    state: CompletionState,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
) -> RenderableType:
    """Render prompt completion suggestions in aligned command/description columns."""
    table = Table.grid(expand=True)
    table.add_column(no_wrap=True)
    table.add_column(ratio=1)

    previous_category: str | None = None
    for index, item in enumerate(state.items):
        if item.category != previous_category:
            if index:
                table.add_row(Text(""), Text(""))
            if item.category:
                table.add_row(Text(item.category, style=theme.completion_description), Text(""))
            previous_category = item.category

        selected = index == state.selected_index
        prefix = "› " if selected else "  "
        style = theme.completion_selected if selected else theme.prompt_text
        description_style = (
            theme.completion_selected_description if selected else theme.completion_description
        )
        command = Text(prefix, style=style)
        command.append(item.display, style=style)
        command.append("  ", style=style)
        table.add_row(command, Text(item.description or "", style=description_style))
    return table
