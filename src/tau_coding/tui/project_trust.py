"""Accessible Textual adapter for Tau-owned project-trust requests."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from tau_coding.project_trust import ProjectTrustRequest, TrustChoice

_LABELS: tuple[tuple[TrustChoice, str], ...] = (
    ("trust-exact", "Trust this folder"),
    ("trust-parent", "Trust parent folder"),
    ("trust-run", "Trust for this run only"),
    ("decline-exact", "Do not trust this folder"),
    ("decline-run", "Do not trust for this run only"),
)


class ProjectTrustScreen(ModalScreen[TrustChoice | None]):
    """Keyboard-accessible modal rendering one policy-owned request."""

    BINDINGS = [Binding("escape", "cancel", "Decline for this run")]
    DEFAULT_CSS = """
    ProjectTrustScreen { align: center middle; }
    #project-trust-dialog {
        width: 76; max-height: 90%; padding: 1 2;
        border: round $accent; background: $panel;
    }
    #project-trust-title { text-style: bold; margin-bottom: 1; }
    #project-trust-copy { margin-bottom: 1; }
    #project-trust-dialog Button { width: 100%; margin-top: 1; }
    """

    def __init__(self, request: ProjectTrustRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        categories = ", ".join(
            f"{category} ({self.request.resources.counts[category]})"
            for category in self.request.resources.categories
        )
        parent = self.request.cwd.value.parent
        with Vertical(id="project-trust-dialog"):
            yield Label("Project inputs require a decision", id="project-trust-title")
            yield Static(
                f"Folder: {self.request.cwd.value}\n"
                f"Protected inputs: {categories}\n\n"
                "This controls project inputs; it is not a sandbox.",
                id="project-trust-copy",
            )
            for choice, label in _LABELS:
                displayed = f"{label} ({parent})" if choice == "trust-parent" else label
                yield Button(displayed, id=f"trust-{choice}", name=choice)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice = event.button.name
        if choice is not None:
            self.dismiss(choice)  # type: ignore[arg-type]

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ProjectTrustApp(App[TrustChoice | None]):
    def __init__(self, request: ProjectTrustRequest) -> None:
        super().__init__()
        self.request = request

    def on_mount(self) -> None:
        self.push_screen(ProjectTrustScreen(self.request), self.exit)


async def prompt_project_trust(request: ProjectTrustRequest) -> TrustChoice | None:
    """Run the frontend adapter and return the Tau policy choice."""
    return await _ProjectTrustApp(request).run_async()
