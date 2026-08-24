"""Textual lifecycle tests for the provider-neutral local-backend host."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console
from textual.app import App
from textual.widgets import Input, Label, ListView, ProgressBar, Select, Static

from tau_coding.extensions import (
    DynamicProvider,
    DynamicProviderRegistry,
    LocalArtifactOption,
    LocalBackend,
    LocalBackendRegistry,
    LocalBackendStatus,
    LocalConfigField,
    LocalConfigureResult,
    LocalConfigureSpec,
    LocalConfirmationChoice,
    LocalConfirmationRequest,
    LocalModel,
    LocalOperationResult,
    LocalProgress,
    LocalSearchResult,
    NoAuth,
    OpenAICompatibleTransport,
    ProviderModel,
)
from tau_coding.tui.config import TAU_DARK_THEME
from tau_coding.tui.local_backends import (
    LocalBackendPickerScreen,
    LocalBackendScreen,
    LocalChoiceConfirmScreen,
    LocalConfigureScreen,
    LocalConfirmScreen,
    LocalSearchResultsScreen,
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
    load_model=None,
    unload_model=None,
    download_model=None,
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
            load_model=load_model,
            unload_model=unload_model,
            download_model=download_model,
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
        assert (
            "Enter selects"
            in picker.query_one("#local-backend-picker-footer", Static).render().plain
        )
        await pilot.press("enter")
        await pilot.pause()

    assert selected == ["backend"]
    await registry.aclose()


async def test_backend_open_auto_refreshes_and_renders_clickable_models() -> None:
    refreshed = asyncio.Event()

    async def refresh(context):
        del context
        refreshed.set()
        return LocalBackendStatus(
            state="ready",
            endpoint_display="http://127.0.0.1:8080/v1",
            models=(LocalModel("downloaded", "Downloaded model", "unloaded"),),
            actions=("refresh", "load_model"),
        )

    loaded: list[str] = []

    async def load_model(model_id, context):
        del context
        loaded.append(model_id)
        return LocalOperationResult(
            backend_status=LocalBackendStatus(
                state="ready",
                models=(LocalModel(model_id, "Downloaded model", "loaded"),),
                selected_model=model_id,
                actions=("refresh", "load_model", "use"),
            ),
            committed=True,
        )

    registry = _registry(refresh=refresh, load_model=load_model)
    app = _Host()

    async with app.run_test() as pilot:
        app.push_screen(LocalBackendScreen(registry, "backend", theme=TAU_DARK_THEME))
        await refreshed.wait()
        await pilot.pause()

        screen = app.screen
        status = screen.query_one("#local-backend-status", Static).render().plain
        assert "http://127.0.0.1:8080/v1" in status
        model_list = screen.query_one("#local-model-list", ListView)
        action_menu = screen.query_one("#local-action-menu", ListView)
        assert model_list is not action_menu
        assert "Refresh" in action_menu.children[0].query_one(Label).render().plain
        label = model_list.children[0].query_one(Label).render().plain
        assert "Downloaded model" in label
        assert "available to load" in label

        model_list.action_select_cursor()
        await pilot.pause()
        assert loaded == ["downloaded"]
        assert "loaded" in model_list.children[0].query_one(Label).render().plain

    await registry.aclose()


async def test_download_progress_renders_fraction_and_remaining_detail() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def refresh(context):
        del context
        return LocalBackendStatus(
            state="ready",
            actions=("refresh", "download_model"),
        )

    async def download_model(model_id, context):
        assert model_id == "owner/repo:Q4_K_M"
        context.report_progress(
            LocalProgress(
                "Downloading owner/repo:Q4_K_M… 4.0 GiB remaining",
                fraction=0.25,
            )
        )
        # A catalog poll can only report the state, not byte progress. It must
        # not replace the newer determinate SSE update.
        context.report_progress(LocalProgress("Downloading owner/repo:Q4_K_M…"))
        started.set()
        await release.wait()
        return LocalOperationResult(message="Download complete.", committed=True)

    registry = _registry(refresh=refresh, download_model=download_model)
    app = _Host()

    async with app.run_test() as pilot:
        screen = LocalBackendScreen(registry, "backend", theme=TAU_DARK_THEME)
        app.push_screen(screen)
        await pilot.pause()
        screen._start_operation(
            "download_model",
            model_id="owner/repo:Q4_K_M",
            confirmation="download",
        )
        await started.wait()
        await pilot.pause()

        progress = screen.query_one("#local-backend-progress", Static).render().plain
        progress_bar = screen.query_one("#local-backend-progress-bar", ProgressBar)
        assert "4.0 GiB remaining" in progress
        assert progress_bar.styles.display == "block"
        assert progress_bar.progress == 0.25
        assert (
            progress_bar.size.width == screen.query_one("#local-backend-screen").content_size.width
        )
        bar = progress_bar.query_one("#bar")
        assert bar.size.width == progress_bar.content_size.width
        console = Console(width=bar.size.width, record=True, file=StringIO())
        console.print(bar.render())
        rendered_bar = console.export_text().strip()
        assert "█" in rendered_bar
        assert "─" in rendered_bar

        release.set()
        await screen._worker
        assert progress_bar.styles.display == "none"

    await registry.aclose()


async def test_clicking_loaded_model_uses_exact_model_id() -> None:
    async def refresh(context):
        del context
        return LocalBackendStatus(
            state="ready",
            models=(
                LocalModel("first", state="loaded"),
                LocalModel("second", state="sleeping"),
            ),
            actions=("refresh",),
        )

    used: list[tuple[str, str]] = []
    registry = _registry(refresh=refresh)
    app = _Host()

    async def use(provider_id: str, model_id: str) -> None:
        used.append((provider_id, model_id))

    async with app.run_test() as pilot:
        app.push_screen(
            LocalBackendScreen(
                registry,
                "backend",
                theme=TAU_DARK_THEME,
                on_use=use,
            )
        )
        await pilot.pause()
        model_list = app.screen.query_one("#local-model-list", ListView)
        model_list.index = 1
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, LocalChoiceConfirmScreen)
        assert app.screen.query_one("#local-choice-list", ListView).index == 0
        await pilot.press("enter")
        await pilot.pause()

    assert used == [("provider", "second")]
    await registry.aclose()


async def test_confirmation_preselects_cancel_without_recommending_it() -> None:
    request = LocalConfirmationRequest(
        "Download a large model?",
        (
            LocalConfirmationChoice("download", "Start download"),
            LocalConfirmationChoice("cancel", "Cancel"),
        ),
    )
    app = _Host()

    async with app.run_test() as pilot:
        app.push_screen(LocalChoiceConfirmScreen(request, theme=TAU_DARK_THEME))
        await pilot.pause()

        choices = app.screen.query_one("#local-choice-list", ListView)
        assert choices.index == 1
        assert all(
            "recommended" not in item.query_one(Label).render().plain for item in choices.children
        )


async def test_search_results_preselect_recommended_download_variant() -> None:
    selected: list[str | None] = []
    app = _Host()
    results = (
        LocalSearchResult(
            "owner/repo",
            "owner/repo",
            options=(
                LocalArtifactOption("owner/repo:Q8_0", "Q8_0", 8_000_000_000),
                LocalArtifactOption(
                    "owner/repo:Q4_K_M",
                    "Q4_K_M",
                    4_000_000_000,
                    recommended=True,
                ),
            ),
        ),
    )

    async with app.run_test() as pilot:
        app.push_screen(
            LocalSearchResultsScreen(results, theme=TAU_DARK_THEME),
            callback=selected.append,
        )
        await pilot.pause()

        model_list = app.screen.query_one("#local-search-results-list", ListView)
        assert model_list.index == 1
        assert "recommended" in model_list.children[1].query_one(Label).render().plain
        model_list.action_select_cursor()
        await pilot.pause()

    assert selected == ["owner/repo:Q4_K_M"]


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


async def test_closing_download_modal_detaches_and_reopen_offers_explicit_cancel() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def refresh(context):
        del context
        return LocalBackendStatus(state="ready", actions=("refresh", "download_model"))

    async def download_model(model_id, context):
        del model_id
        context.report_progress(
            LocalProgress("Downloading owner/repo:Q4_K_M… 3.0 GiB remaining", fraction=0.25)
        )
        # A later state-only poll must not replace byte progress when reattaching.
        context.report_progress(LocalProgress("Downloading owner/repo:Q4_K_M…"))
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    registry = _registry(refresh=refresh, download_model=download_model)
    app = _Host()

    async with app.run_test() as pilot:
        screen = LocalBackendScreen(registry, "backend", theme=TAU_DARK_THEME)
        app.push_screen(screen)
        await pilot.pause()
        screen._start_operation(
            "download_model",
            model_id="owner/repo:Q4_K_M",
            confirmation="download",
        )
        await entered.wait()
        worker = screen._worker
        assert worker is not None

        screen.action_cancel()
        await pilot.pause()

        assert cancelled.is_set() is False
        assert worker.done() is False
        assert registry.operation_running("backend", "download_model") is True

        reopened = LocalBackendScreen(registry, "backend", theme=TAU_DARK_THEME)
        app.push_screen(reopened)
        await pilot.pause()
        labels = [
            item.query_one(Label).render().plain
            for item in reopened.query_one("#local-action-menu", ListView).children
        ]
        assert "Cancel active download…" in labels
        server_status = reopened.query_one("#local-backend-status", Static).render().plain
        assert "State: ready" in server_status
        assert "Looking for a local server" not in server_status
        progress = reopened.query_one("#local-backend-progress", Static).render().plain
        progress_bar = reopened.query_one("#local-backend-progress-bar", ProgressBar)
        assert "3.0 GiB remaining" in progress
        assert progress_bar.styles.display == "block"
        assert progress_bar.progress == 0.25
        assert progress_bar.query_one("#bar").size.width == progress_bar.content_size.width

        reopened._cancel_download("cancel_download")
        await worker
        assert cancelled.is_set()
        assert registry.operation_running("backend", "download_model") is False

    await registry.aclose()


async def test_detached_download_completion_immediately_shows_model_available() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = False

    def status() -> LocalBackendStatus:
        return LocalBackendStatus(
            state="ready",
            models=(
                LocalModel(
                    "owner/repo:Q4_K_M",
                    state="unloaded" if completed else "downloading",
                ),
            ),
            actions=("refresh", "download_model", "load_model"),
        )

    async def refresh(context):
        del context
        return status()

    async def download_model(model_id, context):
        nonlocal completed
        del model_id
        context.report_progress(LocalProgress("Downloading owner/repo:Q4_K_M…", fraction=0.75))
        entered.set()
        await release.wait()
        completed = True
        return LocalOperationResult(
            backend_status=status(),
            message="Server-side download completed.",
            committed=True,
        )

    registry = _registry(refresh=refresh, download_model=download_model)
    app = _Host()

    async with app.run_test() as pilot:
        screen = LocalBackendScreen(registry, "backend", theme=TAU_DARK_THEME)
        app.push_screen(screen)
        await pilot.pause()
        screen._start_operation(
            "download_model",
            model_id="owner/repo:Q4_K_M",
            confirmation="download",
        )
        await entered.wait()
        worker = screen._worker
        assert worker is not None
        screen.action_cancel()
        await pilot.pause()

        reopened = LocalBackendScreen(registry, "backend", theme=TAU_DARK_THEME)
        app.push_screen(reopened)
        await pilot.pause()
        assert (
            "downloading"
            in reopened.query_one("#local-model-list", ListView)
            .children[0]
            .query_one(Label)
            .render()
            .plain
        )

        release.set()
        await worker
        for _ in range(20):
            await asyncio.sleep(0.05)
            await pilot.pause()
            current_label = (
                reopened.query_one("#local-model-list", ListView)
                .children[0]
                .query_one(Label)
                .render()
                .plain
            )
            if "available to load" in current_label:
                break

        reopened._render_observed_download_progress(
            LocalProgress("Downloading owner/repo:Q4_K_M…", fraction=0.75)
        )
        label = (
            reopened.query_one("#local-model-list", ListView)
            .children[0]
            .query_one(Label)
            .render()
            .plain
        )
        assert "available to load" in label
        assert "downloading" not in label
        assert reopened.query_one("#local-backend-progress", Static).render().plain == ""
        assert (
            reopened.query_one("#local-backend-progress-bar", ProgressBar).styles.display == "none"
        )

    await registry.aclose()


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
        assert app.screen.query_one("#local-confirm-list", ListView).index == 1
        await pilot.press("enter")
        await pilot.pause()

        screen._use_selected()
        await pilot.pause()
        assert used == [("provider", "model")]

    await registry.aclose()
