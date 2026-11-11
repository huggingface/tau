"""End-to-end Codex resume regressions with real session loading and fake transport."""

import json
from pathlib import Path

import pytest
from textual.widgets import OptionList

from pi_event_helpers import assistant_done, assistant_start
from tau_agent import AssistantMessage
from tau_agent.session import JsonlSessionStorage, ModelChangeEntry, SessionInfoEntry
from tau_ai import FakeProvider, RuntimeModel, RuntimeModelCatalog
from tau_coding import (
    CodingSession,
    CodingSessionConfig,
    FileCredentialStore,
    ModelChoice,
    OAuthCredential,
    OpenAICodexProviderConfig,
    OpenAICompatibleProviderConfig,
    ProviderConfigError,
    ProviderSettings,
    ScopedModelConfig,
    load_provider_settings,
    save_provider_settings,
)
from tau_coding import cli as cli_module
from tau_coding import session as session_module
from tau_coding.codex_model_store import save_codex_model_catalog
from tau_coding.paths import TauPaths
from tau_coding.provider_config import ProviderConfig, save_default_provider_model
from tau_coding.session_manager import SessionManager
from tau_coding.tui import app as tui_app


class CatalogProvider(FakeProvider):
    def __init__(
        self, *, include_astra: bool = True, reply: bool = False, thinking: bool = False
    ) -> None:
        super().__init__(
            [
                [
                    assistant_start(model="gpt-6-astra"),
                    assistant_done(AssistantMessage(content="hello", model="gpt-6-astra")),
                ]
                for _ in range(2)  # The first turn can also request a session title.
            ]
            if reply
            else []
        )
        self.include_astra = include_astra
        self.thinking = thinking

    async def discover_models(self) -> RuntimeModelCatalog:
        models = (RuntimeModel(id="gpt-5.6-sol"),)
        if self.include_astra:
            models += (
                RuntimeModel(
                    id="gpt-6-astra", thinking_levels=("off", "high") if self.thinking else ()
                ),
            )
        return RuntimeModelCatalog(models)

    async def aclose(self) -> None:
        pass


@pytest.mark.anyio
@pytest.mark.parametrize("selection_method", ["picker", "cycle"])
@pytest.mark.parametrize(
    "startup_inventory",
    ["cache", "discovery", "offline-cache", "offline-missing", "missing", "other-account"],
)
async def test_committed_live_codex_selection_becomes_default_on_fresh_tui_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    selection_method: str,
    startup_inventory: str,
) -> None:
    monkeypatch.delenv("TAU_OFFLINE", raising=False)
    monkeypatch.setenv("TEST_LOCAL_KEY", "test-key")
    paths = TauPaths()
    local = OpenAICompatibleProviderConfig(
        name="test-local",
        api_key_env="TEST_LOCAL_KEY",
        credential_name=None,
        models=("qwen",),
        default_model="qwen",
    )
    settings = ProviderSettings(
        default_provider=local.name,
        providers=(*load_provider_settings(paths).providers, local),
        scoped_models=(
            ScopedModelConfig(provider=local.name, model="qwen"),
            ScopedModelConfig(provider="openai-codex", model="gpt-6-astra"),
        ),
    )
    save_provider_settings(settings, paths)
    catalog_before = (paths.home / "catalog.toml").read_bytes()
    FileCredentialStore(paths.home / "credentials.json").set_oauth(
        "openai-codex",
        OAuthCredential(access="test", refresh="test", expires=4_000_000_000, account_id="test"),
    )

    live_catalog = RuntimeModelCatalog(
        (RuntimeModel(id="gpt-6-astra", thinking_levels=("off", "high")),)
    )
    save_codex_model_catalog(live_catalog, account_id="test", paths=paths)

    def create_provider(provider: ProviderConfig, **kwargs: object) -> CatalogProvider:
        # Keep the real factory's validation boundary: no unlisted runtime models.
        model = kwargs.get("model")
        assert model is None or model in provider.models
        return CatalogProvider(thinking=True)

    monkeypatch.setattr(session_module, "create_model_provider", create_provider)
    monkeypatch.setattr(tui_app, "create_model_provider", create_provider)
    manager = SessionManager(paths)
    record = manager.create_session(cwd=tmp_path, provider_name=local.name, model="qwen")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=CatalogProvider(),
            model="qwen",
            provider_name=local.name,
            provider_settings=settings,
            runtime_provider_config=local,
            runtime_model_catalogs={"openai-codex": live_catalog},
            cwd=tmp_path,
            storage=JsonlSessionStorage(record.path),
            session_id=record.id,
            session_manager=manager,
            system="Test",
            extensions_enabled=False,
        )
    )
    try:
        app = tui_app.TauTuiApp(session)
        async with app.run_test() as pilot:
            if selection_method == "cycle":
                await pilot.press("ctrl+p")
            else:
                await app._switch_model(ModelChoice("openai-codex", "gpt-6-astra"))
            assert session.model == "gpt-6-astra"
            assert load_provider_settings(paths).default_provider == local.name
            async for _ in session.prompt("commit the selected model"):
                pass
    finally:
        await session.aclose()

    saved = json.loads((paths.home / "providers.json").read_text(encoding="utf-8"))
    assert saved["default_provider"] == "openai-codex"
    assert saved["provider_preferences"]["openai-codex"]["default_model"] == "gpt-6-astra"
    assert (paths.home / "catalog.toml").read_bytes() == catalog_before
    durable = load_provider_settings(paths).get_provider("openai-codex")
    assert durable.default_model == "gpt-6-astra"
    assert "gpt-6-astra" not in durable.models
    assert "gpt-6-astra" not in durable.model_metadata

    if startup_inventory in {"discovery", "offline-missing", "missing"}:
        (paths.home / "codex-models-store.json").unlink()
    if startup_inventory in {"offline-cache", "offline-missing"}:
        monkeypatch.setenv("TAU_OFFLINE", "1")
    if startup_inventory == "other-account":
        FileCredentialStore(paths.home / "credentials.json").set_oauth(
            "openai-codex",
            OAuthCredential(
                access="other", refresh="other", expires=4_000_000_000, account_id="other"
            ),
        )
    unavailable = startup_inventory in {"offline-missing", "missing", "other-account"}
    if unavailable:
        monkeypatch.setattr(
            session_module,
            "create_model_provider",
            lambda *a, **k: CatalogProvider(include_astra=False),
        )

    class InspectApp:
        def __init__(self, session: CodingSession, **kwargs: object) -> None:
            self.session = session

        async def run_async(self) -> None:
            assert self.session.provider_name == "openai-codex"
            assert self.session.model == "gpt-6-astra"
            assert self.session.thinking_level == "high"
            assert self.session._active_provider_config() is not None
            assert "gpt-6-astra" in self.session._active_provider_config().models

    monkeypatch.setattr(tui_app, "TauTuiApp", InspectApp)

    async def restart() -> None:
        await tui_app.run_tui_app(
            model=None,
            cwd=tmp_path,
            session_manager=manager,
            extensions_enabled=False,
            trust_override="trust",
            thinking_level_override="high",
        )

    if unavailable:
        with pytest.raises(ProviderConfigError, match="gpt-6-astra"):
            await restart()
    else:
        await restart()
    assert (paths.home / "catalog.toml").read_bytes() == catalog_before


@pytest.mark.anyio
@pytest.mark.parametrize("frontend", ["print", "rpc"])
async def test_headless_startup_discovers_saved_live_codex_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frontend: str
) -> None:
    monkeypatch.delenv("TAU_OFFLINE", raising=False)
    paths = TauPaths()
    settings = load_provider_settings(paths)
    save_provider_settings(settings, paths)
    save_default_provider_model(
        provider_name="openai-codex",
        model="gpt-6-astra",
        paths=paths,
        runtime_provider=OpenAICodexProviderConfig(
            models=("gpt-6-astra",), default_model="gpt-6-astra"
        ),
    )
    FileCredentialStore(paths.home / "credentials.json").set_oauth(
        "openai-codex",
        OAuthCredential(access="test", refresh="test", expires=4_000_000_000, account_id="test"),
    )
    providers: list[CatalogProvider] = []

    def create_provider(provider: ProviderConfig, **kwargs: object) -> CatalogProvider:
        model = kwargs.get("model")
        assert model is None or model in provider.models
        runtime = CatalogProvider(reply=True, thinking=True)
        providers.append(runtime)
        return runtime

    monkeypatch.setattr(cli_module, "create_model_provider", create_provider)
    monkeypatch.setattr(session_module, "create_model_provider", create_provider)
    if frontend == "print":
        ok = await cli_module.run_openai_print_mode(
            "hello",
            None,
            tmp_path,
            extensions_enabled=False,
            trust_override="trust",
            thinking_level_override="high",
        )
        assert ok
        assert any(call[0] == "gpt-6-astra" for provider in providers for call in provider.calls)
    else:

        class InspectServer:
            def __init__(self, session: CodingSession) -> None:
                self.session = session

            async def run(self) -> None:
                try:
                    assert self.session.provider_name == "openai-codex"
                    assert self.session.model == "gpt-6-astra"
                    assert self.session.thinking_level == "high"
                finally:
                    await self.session.aclose()

        monkeypatch.setattr(cli_module, "RpcServer", InspectServer)
        await cli_module.run_openai_rpc_mode(
            None,
            tmp_path,
            extensions_enabled=False,
            trust_override="trust",
            thinking_level_override="high",
        )


@pytest.mark.anyio
async def test_initial_live_selection_replays_corrected_fallback_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TAU_OFFLINE", raising=False)
    static = OpenAICodexProviderConfig(models=("gpt-5.6-sol",), default_model="gpt-5.6-sol")
    settings = ProviderSettings(default_provider="openai-codex", providers=(static,))
    monkeypatch.setattr(session_module, "create_model_provider", lambda *a, **k: CatalogProvider())
    storage = JsonlSessionStorage(tmp_path / "new.jsonl")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=None,
            model="gpt-5.6-sol",
            provider_name="openai-codex",
            requested_provider="openai-codex",
            requested_model="gpt-6-astra",
            provider_settings=settings,
            cwd=tmp_path,
            storage=storage,
            system="Test",
            extensions_enabled=False,
            defer_authoritative_writes=True,
        )
    )
    try:
        assert session.model == "gpt-6-astra"
        assert session._state.model == "gpt-6-astra"
        await session._commit_prepared_entries()
        entries = await storage.read_all()
        selections = [entry.model for entry in entries if isinstance(entry, ModelChangeEntry)]
        assert selections == ["gpt-6-astra"]
    finally:
        await session.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("flow", ["startup", "resume"])
@pytest.mark.parametrize("stale_index", [False, True])
async def test_codex_resume_restores_astra_through_real_frontend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flow: str, stale_index: bool
) -> None:
    monkeypatch.delenv("TAU_OFFLINE", raising=False)
    paths = TauPaths()
    manager = SessionManager(paths)
    static = OpenAICodexProviderConfig(models=("gpt-5.6-sol",), default_model="gpt-5.6-sol")
    settings = ProviderSettings(default_provider="openai-codex", providers=(static,))
    FileCredentialStore(paths.home / "credentials.json").set_oauth(
        "openai-codex",
        OAuthCredential(access="test", refresh="test", expires=4_000_000_000, account_id="test"),
    )
    record = manager.create_session(
        cwd=tmp_path,
        model="gpt-5.6-sol" if stale_index else "gpt-6-astra",
        provider_name="openai-codex",
    )
    storage = JsonlSessionStorage(record.path)
    info = SessionInfoEntry(cwd=str(tmp_path))
    previous = ModelChangeEntry(parent_id=info.id, model="gpt-5.6-sol", provider="openai-codex")
    astra = ModelChangeEntry(parent_id=previous.id, model="gpt-6-astra", provider="openai-codex")
    await storage.append_batch((info, previous, astra))

    monkeypatch.setattr(tui_app, "load_provider_settings", lambda: settings)
    monkeypatch.setattr(session_module, "load_provider_settings", lambda *a: settings)
    monkeypatch.setattr(tui_app, "create_model_provider", lambda *a, **k: CatalogProvider())
    monkeypatch.setattr(session_module, "create_model_provider", lambda *a, **k: CatalogProvider())

    real_app = tui_app.TauTuiApp

    async def inspect_picker(session: CodingSession) -> None:
        async def refresh() -> None:
            session.reload_provider_settings()

        monkeypatch.setattr(session, "refresh_model_catalogs", refresh)
        app = real_app(session)
        async with app.run_test() as pilot:
            assert session.model == "gpt-6-astra"
            app._open_model_picker()
            await pilot.pause()
            picker = app.screen
            assert isinstance(picker, tui_app.ModelPickerScreen)
            index = picker.query_one("#model-picker-list", OptionList).highlighted
            assert index is not None
            assert picker.visible_choices[index].model == "gpt-6-astra"

    if flow == "startup":

        class InspectApp:
            def __init__(self, session: CodingSession, **kwargs: object) -> None:
                self.session = session

            async def run_async(self) -> None:
                assert self.session.provider_name == "openai-codex"
                assert self.session.model == "gpt-6-astra"
                await inspect_picker(self.session)

        monkeypatch.setattr(tui_app, "TauTuiApp", InspectApp)
        await tui_app.run_tui_app(
            model=None,
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
            extensions_enabled=False,
            trust_override="trust",
        )
    else:
        source = manager.create_session(
            cwd=tmp_path, model="gpt-5.6-sol", provider_name="openai-codex"
        )
        monkeypatch.setattr(
            session_module,
            "create_model_provider",
            lambda *a, **k: CatalogProvider(include_astra=False),
        )
        session = await CodingSession.load(
            CodingSessionConfig(
                provider=CatalogProvider(),
                model="gpt-5.6-sol",
                provider_name="openai-codex",
                provider_settings=settings,
                runtime_provider_config=static,
                cwd=tmp_path,
                storage=JsonlSessionStorage(source.path),
                session_id=source.id,
                session_manager=manager,
                system="Test",
                extensions_enabled=False,
            )
        )
        monkeypatch.setattr(
            session_module, "create_model_provider", lambda *a, **k: CatalogProvider()
        )
        try:
            await session.resume(record.id)
            assert session.provider_name == "openai-codex"
            assert session.model == "gpt-6-astra"
            await inspect_picker(session)
        finally:
            await session.aclose()
