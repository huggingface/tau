"""Add a small, host-framed status section to Tau's TUI sidebar."""

from tau_coding.extensions import ExtensionAPI, ExtensionContext


def setup(tau: ExtensionAPI) -> None:
    """Show and update a turn counter when the active frontend has a sidebar."""
    turn_count = 0

    def show(context: ExtensionContext) -> None:
        sidebar = getattr(context.ui, "sidebar", None)
        if sidebar is not None and sidebar.supported:
            sidebar.set_section(
                "turns",
                title="extension status",
                content=[f"[green]{turn_count}[/green] completed turns"],
            )

    def on_session_start(event: object, context: ExtensionContext) -> None:
        nonlocal turn_count
        del event
        turn_count = 0
        show(context)

    def on_turn_end(event: object, context: ExtensionContext) -> None:
        nonlocal turn_count
        del event
        turn_count += 1
        show(context)

    def on_session_shutdown(event: object, context: ExtensionContext) -> None:
        del event
        sidebar = getattr(context.ui, "sidebar", None)
        if sidebar is not None:
            sidebar.remove_section("turns")

    tau.on("session_start", on_session_start)
    tau.on("turn_end", on_turn_end)
    tau.on("session_shutdown", on_session_shutdown)
