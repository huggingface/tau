"""End-to-end Codex resume regressions with real session loading and fake transport."""

from pathlib import Path

import pytest
from textual.widgets import ListView

from tau_agent.session import JsonlSessionStorage, ModelChangeEntry, SessionInfoEntry
from tau_ai import FakeProvider, RuntimeModel, RuntimeModelCatalog
from tau_coding import (
    CodingSession,
    CodingSessionConfig,
    FileCredentialStore,
    OAuthCredential,
    OpenAICodexProviderConfig,
    ProviderSettings,
)
from tau_coding import session as session_module
from tau_coding.paths import TauPaths
from tau_coding.session_manager import SessionManager
from tau_coding.tui import app as tui_app


class CatalogProvider(FakeProvider):
    def __init__(self, *, include_astra: bool = True) -> None:
        super().__init__([])
        self.include_astra = include_astra

    async def discover_models(self) -> RuntimeModelCatalog:
        models = (RuntimeModel(id="gpt-5.6-sol"),)
        if self.include_astra:
            models += (RuntimeModel(id="gpt-6-astra"),)
        return RuntimeModelCatalog(models)

    async def aclose(self) -> None:
        pass


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
            index = picker.query_one("#model-picker-list", ListView).index
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
