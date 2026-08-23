"""Focused tests for the component (widget-hosting) seam.

These drive the generic seam on the real ``TauTuiApp`` via the host bridge,
reusing the fakes/helpers that already back the broader TUI suite in
``test_tui_app`` (pytest's prepend import mode puts the tests directory on
``sys.path``, so a sibling test module is importable). They close the gaps the
seam's code review flagged: slot placement/ordering, main-view re-open, host
exceptions staying un-swallowed, and main-view open/close restoring the main
transcript.
"""

from pathlib import Path

import pytest
from textual.containers import Container
from textual.widgets import Static

from conftest import isolate_home
from tau_coding.extensions import ExtensionRuntime
from tau_coding.tui.app import PromptInput, TauTuiApp
from tau_coding.tui.config import TuiSettings
from tau_coding.tui.widgets import TranscriptView
from test_tui_app import (  # noqa: E402 - sibling test module (see docstring)
    FakeSession,
    _component_bridge,
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

        bridge.set_slot_widget("a", lambda theme: Static("a", id="ext-a"), placement="below_prompt")
        bridge.set_slot_widget("b", lambda theme: Static("b", id="ext-b"), placement="below_prompt")
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

        first = bridge.open_main_view(lambda handle, theme: Static("one", id="ext-view-one"))
        await pilot.pause()
        second = bridge.open_main_view(lambda handle, theme: Static("two", id="ext-view-two"))
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
async def test_component_slot_replace_same_id_same_tick_no_duplicate() -> None:
    """Replacing a slot widget with a same-id widget in one tick must sequence.

    The old widget's ``remove()`` is deferred; mounting the replacement before it
    drains would leave two widgets sharing an id (``DuplicateIds``). The swap
    must await the removal, so exactly one widget survives with no failure.
    """
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        bridge.set_slot_widget(
            "k", lambda theme: Static("one", id="dup-slot"), placement="below_prompt"
        )
        await pilot.pause()
        # Same tick: unmount then re-register the SAME id (the reload analog).
        bridge.set_slot_widget("k", None, placement="below_prompt")
        bridge.set_slot_widget(
            "k", lambda theme: Static("two", id="dup-slot"), placement="below_prompt"
        )
        await pilot.pause()
        await pilot.pause()

        slot = app.query_one("#below-prompt-slot", Container)
        assert len(slot.query("#dup-slot")) == 1
        assert app._extension_component_failures_reported == set()
        assert app._extension_slot_mounted["k"] is app._extension_slot_widgets["k"]


@pytest.mark.anyio
async def test_component_slot_rapid_a_b_c_last_wins() -> None:
    """Three same-id slot swaps in one tick collapse to the last, no duplicates."""
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        made: dict[str, Static] = {}

        def factory(label: str):  # noqa: ANN202
            def build(theme):  # noqa: ANN001, ANN202
                widget = made[label] = Static(label, id="abc-slot")
                return widget

            return build

        bridge.set_slot_widget("k", factory("pre"), placement="below_prompt")
        await pilot.pause()
        bridge.set_slot_widget("k", factory("A"), placement="below_prompt")
        bridge.set_slot_widget("k", factory("B"), placement="below_prompt")
        bridge.set_slot_widget("k", factory("C"), placement="below_prompt")
        await pilot.pause()
        await pilot.pause()

        slot = app.query_one("#below-prompt-slot", Container)
        surviving = slot.query("#abc-slot")
        assert len(surviving) == 1
        assert surviving.first() is made["C"]
        assert app._extension_component_failures_reported == set()


@pytest.mark.anyio
async def test_component_second_main_view_same_id_no_duplicate() -> None:
    """Opening a second same-id main view while one is open must not collide."""
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        bridge.open_main_view(lambda handle, theme: Static("one", id="dup-view"))
        await pilot.pause()
        # A second view sharing the viewer id, opened before the first drains.
        second = bridge.open_main_view(lambda handle, theme: Static("two", id="dup-view"))
        await pilot.pause()
        await pilot.pause()

        slot = app.query_one("#main-slot", Container)
        assert len(slot.query("#dup-view")) == 1
        assert app._extension_main_view is second
        assert app._extension_main_view_mounted is second.widget
        assert app._extension_component_failures_reported == set()


@pytest.mark.anyio
async def test_component_main_view_rapid_a_b_c_last_wins() -> None:
    """Three same-id main-view opens in one tick mount exactly the last one."""
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        bridge.open_main_view(lambda handle, theme: Static("pre", id="abc-view"))
        await pilot.pause()
        a = bridge.open_main_view(lambda handle, theme: Static("A", id="abc-view"))
        b = bridge.open_main_view(lambda handle, theme: Static("B", id="abc-view"))
        c = bridge.open_main_view(lambda handle, theme: Static("C", id="abc-view"))
        await pilot.pause()
        await pilot.pause()

        slot = app.query_one("#main-slot", Container)
        surviving = slot.query("#abc-view")
        assert len(surviving) == 1
        assert surviving.first() is c.widget
        assert app._extension_main_view is c
        assert not a.is_open
        assert not b.is_open
        assert c.is_open
        assert app._extension_component_failures_reported == set()


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
async def test_component_main_view_open_close_restores_transcript() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        # Opening a main view hides #transcript and shows only #main-slot.
        handle = bridge.open_main_view(lambda h, theme: Static("main", id="ext-main-view"))
        await pilot.pause()
        displayed = [
            pane_id for pane_id in ("#transcript", "#main-slot") if app.query_one(pane_id).display
        ]
        assert displayed == ["#main-slot"]

        # Closing restores #transcript, hides the slot, and refocuses the prompt.
        handle.close()
        await pilot.pause()
        assert app.query_one("#transcript", TranscriptView).display
        assert not app.query_one("#main-slot", Container).display
        assert app.query_one("#prompt", PromptInput).has_focus


@pytest.mark.anyio
async def test_sidebar_sections_mount_update_and_remove_by_owned_key() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        bridge.set_sidebar_section("alpha", "status", title="Alpha", content=["first"])
        bridge.set_sidebar_section("beta", "status", title="Beta", content=["second"])
        await pilot.pause()

        slot = app.query_one("#sidebar-extension-sections", Container)
        assert len(slot.children) == 2
        assert [
            section.query_one(".extension-sidebar-title", Static).render().plain
            for section in slot.children
        ] == [
            "Alpha",
            "Beta",
        ]

        bridge.set_sidebar_section("alpha", "status", title="Alpha", content=["updated"])
        await pilot.pause()
        await pilot.pause()

        assert [
            section.query_one(".extension-sidebar-title", Static).render().plain
            for section in slot.children
        ] == [
            "Alpha",
            "Beta",
        ]
        alpha_body = slot.children[0].query_one(".extension-sidebar-body", Container)
        assert alpha_body.query_one(Static).render().plain == "updated"

        bridge.remove_sidebar_section("alpha", "status")
        await pilot.pause()
        await pilot.pause()
        assert len(slot.children) == 1
        assert (
            slot.children[0].query_one(".extension-sidebar-title", Static).render().plain == "Beta"
        )


@pytest.mark.anyio
async def test_sidebar_same_local_key_is_isolated_between_extensions() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)
        bridge.set_sidebar_section("one", "status", title="One", content=["1"])
        bridge.set_sidebar_section("two", "status", title="Two", content=["2"])
        await pilot.pause()

        assert list(app._extension_sidebar_widgets) == [("one", "status"), ("two", "status")]


@pytest.mark.anyio
async def test_sidebar_remove_then_readd_moves_section_to_end() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)
        bridge.set_sidebar_section("ext", "a", title="A", content=["a"])
        bridge.set_sidebar_section("ext", "b", title="B", content=["b"])
        await pilot.pause()
        bridge.remove_sidebar_section("ext", "a")
        bridge.set_sidebar_section("ext", "a", title="A", content=["a"])
        await pilot.pause()
        await pilot.pause()

        slot = app.query_one("#sidebar-extension-sections", Container)
        assert [
            section.query_one(".extension-sidebar-title", Static).render().plain
            for section in slot.children
        ] == [
            "B",
            "A",
        ]


@pytest.mark.anyio
async def test_sidebar_off_is_unsupported_and_does_not_call_factory() -> None:
    app = TauTuiApp(FakeSession(), tui_settings=TuiSettings(sidebar_position="off"))
    called = False

    def factory(theme):  # noqa: ANN001, ANN202
        nonlocal called
        called = True
        return Static("hidden")

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)
        assert bridge.supports_sidebar is False
        bridge.set_sidebar_section("ext", "status", title="Status", content=factory)
        await pilot.pause()

        assert called is False
        assert app._extension_sidebar_widgets == {}
        assert app.query_one("#sidebar").display is False


@pytest.mark.anyio
async def test_responsive_hidden_sidebar_keeps_sections_for_resize() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)
        assert bridge.supports_sidebar is True
        bridge.set_sidebar_section("ext", "status", title="Status", content=["ready"])
        await pilot.pause()
        assert app.query_one("#sidebar").display is False
        assert app.query_one("#sidebar-extension-sections", Container).children

        await pilot.resize_terminal(width=120, height=40)
        await pilot.pause()
        assert app.query_one("#sidebar").display is True
        assert app.query_one("#sidebar-extension-sections", Container).children


@pytest.mark.anyio
@pytest.mark.parametrize("position", ["left", "right"])
async def test_sidebar_sections_work_in_both_sidebar_positions(position: str) -> None:
    app = TauTuiApp(FakeSession(), tui_settings=TuiSettings(sidebar_position=position))

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)
        bridge.set_sidebar_section("ext", "status", title="Status", content=["ready"])
        await pilot.pause()

        assert app.query_one("#sidebar-extension-sections", Container).children
        assert app.has_class("-sidebar-right") is (position == "right")


@pytest.mark.anyio
async def test_sidebar_factory_rebuilds_with_live_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_home(monkeypatch, tmp_path)
    app = TauTuiApp(FakeSession())
    themes: list[str] = []

    def factory(theme):  # noqa: ANN001, ANN202
        themes.append(theme.name)
        return Static(theme.name, classes="theme-body")

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)
        bridge.set_sidebar_section("ext", "theme", title="Theme", content=factory)
        await pilot.pause()
        app._set_tui_theme("tau-light")
        await pilot.pause()
        await pilot.pause()

        assert themes == ["tau-dark", "tau-light"]
        assert (
            app.query_one("#sidebar-extension-sections .theme-body", Static).render().plain
            == "tau-light"
        )


@pytest.mark.anyio
async def test_sidebar_factory_failure_keeps_app_running() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)

        def explode(theme):  # noqa: ANN001, ANN202
            raise RuntimeError("sidebar factory exploded")

        bridge.set_sidebar_section("ext", "bad", title="Bad", content=explode)
        await pilot.pause()

        assert app.is_running
        assert app._extension_sidebar_widgets == {}
        assert "sidebar:ext:bad" in app._extension_component_failures_reported


class _SidebarRenderCrash(Static):
    def render(self):  # noqa: ANN201
        raise RuntimeError("sidebar render exploded")


@pytest.mark.anyio
async def test_sidebar_render_failure_is_quarantined_and_diagnosed() -> None:
    session = FakeSession()
    runtime = ExtensionRuntime()
    session.extension_runtime = runtime
    app = TauTuiApp(session)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)
        bridge.set_sidebar_section(
            "broken-extension",
            "bad",
            title="Bad",
            content=lambda theme: _SidebarRenderCrash("bad"),
        )
        await pilot.pause()
        await pilot.pause()

        assert app.is_running
        assert ("broken-extension", "bad") not in app._extension_sidebar_widgets
        assert any(
            diagnostic.name == "broken-extension"
            and "sidebar:broken-extension:bad" in diagnostic.message
            for diagnostic in runtime.diagnostics
        )


@pytest.mark.anyio
async def test_component_clear_removes_sidebar_sections() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bridge = _component_bridge(app)
        bridge.set_sidebar_section("ext", "status", title="Status", content=["ready"])
        await pilot.pause()
        assert app.query_one("#sidebar-extension-sections", Container).children

        bridge.clear_components()
        await pilot.pause()
        await pilot.pause()

        assert app._extension_sidebar_contributions == {}
        assert app._extension_sidebar_widgets == {}
        assert app._extension_sidebar_mounted == {}
        assert not app.query_one("#sidebar-extension-sections", Container).children
