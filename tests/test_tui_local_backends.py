"""Textual lifecycle tests for the provider-neutral local-backend host."""

import asyncio

import pytest
from textual.app import App
from textual.widgets import Input, Label, Select, Static

from tau_coding.extensions import (
    DynamicProvider,
    DynamicProviderRegistry,
    LocalBackend,
    LocalBackendRegistry,
    LocalBackendStatus,
    LocalConfigField,
    LocalConfigureResult,
    LocalConfigureSpec,
    LocalModel,
    NoAuth,
    OpenAICompatibleTransport,
    ProviderModel,
)
from tau_coding.tui.config import TAU_DARK_THEME
from tau_coding.tui.local_backends import (
    LocalBackendPickerScreen,
    LocalBackendScreen,
    LocalConfigureScreen,
    LocalConfirmScreen,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Host(App[None]):
    def compose(self):
        yield Static("host")


def _registry(
    *,
    refresh=None,
    reset=None,
    recommended: bool = False,
) -> LocalBackendRegistry:
    providers = DynamicProviderRegistry(generation_id="generation")
    providers.register(
        "source",
        DynamicProvider(
            id="provider",
            display_name="Provider",
            models=(ProviderModel("model"),),
            default_model="model",
            transport=OpenAICompatibleTransport(
                base_url="http://example.test/v1",
                auth=NoAuth(),
            ),
        ),
    )
    registry = LocalBackendRegistry(providers, generation_id="generation")

    async def status(context):
        del context
        return LocalBackendStatus(
            state="ready",
            models=(LocalModel("model"),),
            selected_model="model",
            actions=("refresh", "use", "reset"),
        )

    registry.register(
        "source",
        LocalBackend(
            id="backend",
            provider_id="provider",
            display_name="Backend",
            configure_spec=LocalConfigureSpec(),
            configure=lambda values, context: LocalConfigureResult(committed=True),
            status=status,
            refresh=refresh or status,
            reset=reset,
            recommended=recommended,
        ),
    )
    return registry


async def test_single_backend_is_preselected_but_requires_explicit_confirmation() -> None:
    registry = _registry(recommended=True)
    selected: list[str | None] = []
    app = _Host()

    async with app.run_test() as pilot:
        app.push_screen(
            LocalBackendPickerScreen(registry, theme=TAU_DARK_THEME),
            callback=selected.append,
        )
        await pilot.pause()

        picker = app.screen
        assert picker.selected == "backend"
        label = picker.query_one("#local-backend-list").children[0].query_one(Label)
        assert "Recommended" in label.render().plain
        assert picker.query_one("#local-backend-confirm")
        await pilot.click("#local-backend-confirm")
        await pilot.pause()

    assert selected == ["backend"]
    await registry.aclose()


async def test_configuration_screen_renders_text_secret_and_choice_fields() -> None:
    spec = LocalConfigureSpec(
        (
            LocalConfigField("endpoint", "Endpoint", "text"),
            LocalConfigField("token", "Token", "secret"),
            LocalConfigField("profile", "Profile", "choice", choices=("fast", "safe")),
        )
    )
    app = _Host()

    async with app.run_test() as pilot:
        app.push_screen(LocalConfigureScreen(spec, theme=TAU_DARK_THEME))
        await pilot.pause()

        assert len(tuple(app.screen.query(Input))) == 2
        assert app.screen.query_one("#local-config-input-1", Input).password is True
        assert app.screen.query_one("#local-config-input-2", Select)


async def test_unmount_cancels_backend_work_without_late_updates() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def refresh(context):
        del context
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    registry = _registry(refresh=refresh)
    screen = LocalBackendScreen(registry, "backend", theme=TAU_DARK_THEME)
    screen._worker = asyncio.create_task(screen._run_operation("refresh", None, None))

    await entered.wait()
    screen.on_unmount()
    await screen._worker

    assert cancelled.is_set()
    assert screen._closing is True
    await registry.aclose()


async def test_reset_and_use_are_rechecked_after_the_host_becomes_idle() -> None:
    registry = _registry(reset=lambda context: LocalBackendStatus(state="ready"))
    idle = False
    used: list[tuple[str, str]] = []
    app = _Host()

    async def use(provider_id: str, model_id: str) -> None:
        used.append((provider_id, model_id))

    async with app.run_test() as pilot:
        screen = LocalBackendScreen(
            registry,
            "backend",
            theme=TAU_DARK_THEME,
            on_use=use,
            is_idle=lambda: idle,
        )
        app.push_screen(screen)
        await pilot.pause()

        screen._confirm_reset()
        assert not isinstance(app.screen, LocalConfirmScreen)

        screen.status = LocalBackendStatus(
            state="ready",
            models=(LocalModel("model"),),
            selected_model="model",
            actions=("use",),
        )
        screen._use_selected()
        await pilot.pause()
        assert used == []

        idle = True
        screen._confirm_reset()
        await pilot.pause()
        assert isinstance(app.screen, LocalConfirmScreen)
        await pilot.click("#local-confirm-no")
        await pilot.pause()

        screen._use_selected()
        await pilot.pause()
        assert used == [("provider", "model")]

    await registry.aclose()
