from pathlib import Path
from types import SimpleNamespace

import pytest

from tau_ai import FakeProvider
from tau_coding import CodingSessionConfig, SessionManager, TauPaths, cli
from tau_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderSelection,
    ProviderSettings,
    load_provider_settings,
)
from tau_coding.tui.app import _startup_inference_provider, _startup_inference_provider_mode


@pytest.mark.parametrize("resume", [False, True])
def test_tui_and_print_record_use_default_but_preserve_resumed_automatic(
    tmp_path: Path, resume: bool
) -> None:
    paths = TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    settings = load_provider_settings(paths)
    provider = settings.get_provider("huggingface")
    manager = SessionManager(paths)
    old = manager.create_session(
        cwd=tmp_path,
        model=provider.default_model,
        provider_name="huggingface",
        inference_provider=None,
        inference_provider_mode="automatic",
    )
    record = cli._print_session_record(
        manager,
        resume_session_id=old.id if resume else None,
        cwd=tmp_path,
        settings=settings,
        provider_name="huggingface",
        model=provider.default_model,
        session_id=None,
    )
    expected = None if resume else "openrouter"
    assert record.inference_provider == expected
    assert record.inference_provider_mode == ("automatic" if resume else "fixed")
    selection = ProviderSelection(provider=provider, model=provider.default_model)
    assert _startup_inference_provider(selection, old if resume else None) == expected
    assert _startup_inference_provider_mode(selection, old if resume else None) == (
        "automatic" if resume else "fixed"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("frontend", ["print", "rpc"])
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("default_route", ["openrouter", None])
async def test_frontend_runtime_default_and_resumed_automatic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frontend: str,
    resume: bool,
    default_route: str | None,
) -> None:
    paths = TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    provider_config = OpenAICompatibleProviderConfig(
        name="huggingface",
        models=("org/model",),
        default_model="org/model",
        default_inference_provider=default_route,
    )
    settings = ProviderSettings(providers=(provider_config,), default_provider="huggingface")
    manager = SessionManager(paths)
    record = manager.create_session(
        cwd=tmp_path,
        model="org/model",
        provider_name="huggingface",
        inference_provider_mode="automatic",
    )
    created: list[str | None] = []
    captured: list[tuple[str | None, str | None]] = []

    class ClosableFakeProvider(FakeProvider):
        async def aclose(self) -> None:
            pass

    def create_provider(config: object, **kwargs: object) -> ClosableFakeProvider:
        created.append(kwargs.get("inference_provider"))  # type: ignore[arg-type]
        return ClosableFakeProvider([])

    async def run_print(**kwargs: object) -> bool:
        captured.append((kwargs["inference_provider"], kwargs["inference_provider_mode"]))
        return True

    async def load_session(config: CodingSessionConfig) -> object:
        captured.append((config.inference_provider, config.inference_provider_mode))
        return SimpleNamespace(extension_runtime=SimpleNamespace(set_ui_bridge=lambda ui: None))

    async def run_rpc() -> None:
        pass

    monkeypatch.setattr(cli, "load_provider_settings", lambda: settings)
    monkeypatch.setattr(cli, "SessionManager", lambda: manager)
    monkeypatch.setattr(cli, "create_model_provider", create_provider)
    monkeypatch.setattr(cli, "run_print_mode", run_print)
    monkeypatch.setattr(cli.CodingSession, "load", load_session)
    monkeypatch.setattr(cli, "RpcServer", lambda session: SimpleNamespace(run=run_rpc))
    if frontend == "print":
        await cli.run_openai_print_mode(
            "Hello", None, tmp_path, resume_session_id=record.id if resume else None
        )
    else:
        await cli.run_openai_rpc_mode(
            None, tmp_path, resume_session_id=record.id if resume else None
        )
    expected = None if resume else default_route
    assert created == [expected]
    assert captured == [(expected, "fixed" if expected is not None else "automatic")]
