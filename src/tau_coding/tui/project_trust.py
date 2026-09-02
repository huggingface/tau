"""Accessible Textual adapter for Tau-owned project-trust requests."""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from tau_coding.project_trust import ProjectTrustRequest, TrustChoice
from tau_coding.tui.themes import TAU_DARK_THEME, textual_theme_for_tui_theme

_LABELS: tuple[tuple[TrustChoice, str], ...] = (
    ("trust-exact", "Trust this folder"),
    ("trust-parent", "Trust parent folder"),
    ("trust-run", "Trust for this run only"),
    ("decline-exact", "Do not trust this folder"),
    ("decline-run", "Do not trust for this run only"),
)


class ProjectTrustScreen(ModalScreen[TrustChoice | None]):
    """Tau-style modal picker rendering one policy-owned request."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
    ]
    DEFAULT_CSS = """
    ProjectTrustScreen {
        align: center middle;
        background: #000000 70%;
    }
    #project-trust-dialog {
        width: 76;
        max-width: 92%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        background: #000000;
        color: #d8dee9;
        border: tall #141922;
    }
    #project-trust-title {
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }
    #project-trust-path-label,
    #project-trust-summary-label {
        height: 1;
        color: #667085;
    }
    #project-trust-path,
    #project-trust-summary,
    #project-trust-boundary {
        height: auto;
        margin-bottom: 1;
    }
    #project-trust-boundary {
        color: #667085;
    }
    #project-trust-list {
        height: auto;
        max-height: 7;
        background: #000000;
        color: #d8dee9;
        border: tall #141922;
    }
    #project-trust-list ListItem.-highlight,
    #project-trust-list ListItem.-highlight Label {
        background: #a7f3f0;
        color: #061a1a;
    }
    #project-trust-help {
        height: 1;
        margin-top: 1;
        color: #667085;
    }
    """

    def __init__(
        self,
        request: ProjectTrustRequest,
        *,
        cancel_action: str = "cancels",
    ) -> None:
        super().__init__()
        self.request = request
        self.cancel_action = cancel_action

    def compose(self) -> ComposeResult:
        categories = ", ".join(
            f"{category} ({self.request.resources.counts[category]})"
            for category in self.request.resources.categories
        )
        parent = self.request.cwd.value.parent
        choices = []
        for choice, label in _LABELS:
            displayed = f"{label} ({parent})" if choice == "trust-parent" else label
            choices.append(
                ListItem(
                    Label(displayed, markup=False),
                    id=f"trust-{choice}",
                    name=choice,
                    classes="project-trust-choice",
                )
            )
        with Vertical(id="project-trust-dialog"):
            yield Static("Project inputs require a decision", id="project-trust-title")
            yield Static("Folder", id="project-trust-path-label")
            yield Static(str(self.request.cwd.value), id="project-trust-path", markup=False)
            yield Static("Protected inputs", id="project-trust-summary-label")
            yield Static(categories, id="project-trust-summary", markup=False)
            yield Static(
                "This controls project inputs; it is not a sandbox.",
                id="project-trust-boundary",
            )
            yield ListView(*choices, id="project-trust-list")
            yield Static(
                f"↑/↓ choose · Enter selects · Escape {self.cancel_action}",
                id="project-trust-help",
            )

    def on_mount(self) -> None:
        """Focus the first safe, explicit action for keyboard users."""
        choices = self.query_one("#project-trust-list", ListView)
        choices.index = 0
        choices.focus()

    def on_key(self, event: Key) -> None:
        """Keep navigation local when hosted by Tau's globally bound app."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        choice = event.item.name
        if choice is not None:
            self.dismiss(choice)  # type: ignore[arg-type]

    def action_cursor_up(self) -> None:
        self.query_one("#project-trust-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#project-trust-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        self.query_one("#project-trust-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ProjectTrustApp(App[TrustChoice | None]):
    def __init__(self, request: ProjectTrustRequest) -> None:
        super().__init__()
        tau_dark = textual_theme_for_tui_theme(TAU_DARK_THEME.name)
        self.register_theme(tau_dark)
        self.theme = tau_dark.name
        self.request = request

    def on_mount(self) -> None:
        self.push_screen(
            ProjectTrustScreen(self.request, cancel_action="exits Tau"),
            self.exit,
        )


async def prompt_project_trust(request: ProjectTrustRequest) -> TrustChoice | None:
    """Run the frontend adapter and return the Tau policy choice."""
    return await _ProjectTrustApp(request).run_async()
