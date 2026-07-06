"""Focused tests for the experimental component (widget-hosting) seam.

These drive the generic seam on the real ``TauTuiApp`` via the host bridge,
reusing the fakes/helpers that already back the broader TUI suite in
``test_tui_app`` (pytest's prepend import mode puts the tests directory on
``sys.path``, so a sibling test module is importable). They close the gaps the
seam's code review flagged: slot placement/ordering, main-view re-open, host
exceptions staying un-swallowed, and coexistence with the legacy
transcript-source view.
"""

import pytest
from textual.containers import Container
from textual.widgets import Static

from tau_coding.tui.app import PromptInput, TauTuiApp
from tau_coding.tui.widgets import TranscriptView
from test_tui_app import (  # noqa: E402 - sibling test module (see docstring)
    FakeSession,
    _component_bridge,
    _strip_source,
    _StripRuntime,
)


@pytest.mark.anyio
async def test_component_above_prompt_slot_mounts_into_above_slot() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        bridge.set_slot_widget(
            "top",
            lambda theme: Static("above", id="ext-above"),
            placement="above_prompt",
        )
        await pilot.pause()

        above = app.query_one("#above-prompt-slot", Container)
        assert above.query("#ext-above")
        # It must not have leaked into the other placement's slot.
        assert not app.query_one("#below-prompt-slot", Container).query("#ext-above")
        assert "top" in app._extension_slot_widgets


@pytest.mark.anyio
async def test_component_multiple_slot_widgets_mount_in_call_order() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        bridge.set_slot_widget(
            "a", lambda theme: Static("a", id="ext-a"), placement="below_prompt"
        )
        bridge.set_slot_widget(
            "b", lambda theme: Static("b", id="ext-b"), placement="below_prompt"
        )
        await pilot.pause()

        slot = app.query_one("#below-prompt-slot", Container)
        assert [child.id for child in slot.children] == ["ext-a", "ext-b"]
        assert list(app._extension_slot_widgets) == ["a", "b"]


@pytest.mark.anyio
async def test_component_second_main_view_replaces_first() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        first = bridge.open_main_view(
            lambda handle, theme: Static("one", id="ext-view-one")
        )
        await pilot.pause()
        second = bridge.open_main_view(
            lambda handle, theme: Static("two", id="ext-view-two")
        )
        await pilot.pause()

        # Opening a second view closes the first handle and leaves exactly one
        # child mounted in the main slot.
        assert not first.is_open
        assert second.is_open
        assert app._extension_main_view is second
        slot = app.query_one("#main-slot", Container)
        assert len(slot.children) == 1
        assert slot.query("#ext-view-two")
        assert not slot.query("#ext-view-one")


@pytest.mark.anyio
async def test_component_host_exception_is_not_swallowed() -> None:
    app = TauTuiApp(FakeSession())

    with pytest.raises(RuntimeError, match="core-bug"):
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            bridge = _component_bridge(app)
            bridge.set_slot_widget("k", lambda theme: Static("x", id="ext-host"))
            await pilot.pause()
            # An extension widget is tracked, so the quarantine path is live.
            assert app._tracked_extension_widgets()

            # A core/host exception whose traceback touches no extension widget
            # must reach Textual's default handler rather than being quarantined
            # and swallowed. run_test re-raises the stored exception on exit.
            try:
                raise RuntimeError("core-bug")
            except RuntimeError as exc:
                app._handle_exception(exc)
            assert app._exception is not None  # reached super()._handle_exception
            assert "core-bug" in str(app._exception)


@pytest.mark.anyio
async def test_component_main_view_coexists_with_legacy_agent_view() -> None:
    runtime = _StripRuntime([_strip_source()])
    session = FakeSession()
    session.extension_runtime = runtime  # type: ignore[attr-defined]
    app = TauTuiApp(session)  # type: ignore[arg-type]

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert runtime.changed_callback is not None
        runtime.changed_callback()
        await pilot.pause()

        # Enter the legacy transcript-source view: #transcript hidden, the
        # #agent-transcript-pane shown.
        await pilot.press("left")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert app._active_source_id == "agent-1"
        assert app.query_one("#agent-transcript-pane", TranscriptView).display
        assert not app.query_one("#transcript", TranscriptView).display

        # Opening a component main view must hide the pane that is actually up
        # (the legacy one), not blindly #transcript. Exactly one of the three
        # panes stays displayed.
        bridge = _component_bridge(app)
        handle = bridge.open_main_view(
            lambda h, theme: Static("main", id="ext-coexist-view")
        )
        await pilot.pause()

        displayed = [
            pane_id
            for pane_id in ("#transcript", "#agent-transcript-pane", "#main-slot")
            if app.query_one(pane_id).display
        ]
        assert displayed == ["#main-slot"]

        # Closing restores the legacy pane that was displaced, not #transcript.
        handle.close()
        await pilot.pause()
        assert app.query_one("#agent-transcript-pane", TranscriptView).display
        assert not app.query_one("#transcript", TranscriptView).display
        assert not app.query_one("#main-slot", Container).display
        assert app.query_one("#prompt", PromptInput).has_focus
