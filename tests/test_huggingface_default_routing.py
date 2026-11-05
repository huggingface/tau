"""Session routing defaults do not depend on advertised inference providers."""

from dataclasses import replace
from pathlib import Path

import pytest

from tau_agent.messages import AssistantMessage, AssistantMessageDiagnostic
from tau_agent.provider_events import AssistantErrorEvent
from tau_agent.session import JsonlSessionStorage
from tau_ai import FakeProvider
from tau_coding import (
    CodingSession,
    CodingSessionConfig,
    SessionManager,
    TauPaths,
    TauResourcePaths,
)
from tau_coding import session as session_module
from tau_coding.provider_config import OpenAICompatibleProviderConfig, ProviderSettings


class _ClosableFakeProvider(FakeProvider):
    async def aclose(self) -> None:
        pass


def _config(tmp_path: Path) -> CodingSessionConfig:
    provider = OpenAICompatibleProviderConfig(
        name="huggingface",
        models=("org/model-a", "org/model-b", "org/model-c"),
        default_model="org/model-a",
        default_inference_provider="openrouter",
        inference_providers={"org/model-b": "deepinfra"},
    )
    paths = TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    return CodingSessionConfig(
        provider=None,
        model="org/model-a",
        provider_name="huggingface",
        requested_provider="huggingface",
        system="You are Tau.",
        storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
        cwd=tmp_path,
        provider_settings=ProviderSettings(providers=(provider,), default_provider="huggingface"),
        resource_paths=TauResourcePaths(root=paths.home, paths=paths),
        extensions_enabled=False,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("route", "mode", "expected_route", "expected_mode"),
    [
        (None, None, "openrouter", "fixed"),
        (None, "automatic", None, "automatic"),
        ("baseten", "fixed", "baseten", "fixed"),
        ("baseten", "automatic", "baseten", "automatic"),
    ],
)
async def test_staged_session_default_respects_explicit_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str | None,
    mode: str | None,
    expected_route: str | None,
    expected_mode: str,
) -> None:
    created: list[str | None] = []

    def create_provider(config: object, **kwargs: object) -> _ClosableFakeProvider:
        created.append(kwargs.get("inference_provider"))  # type: ignore[arg-type]
        return _ClosableFakeProvider([])

    monkeypatch.setattr(session_module, "create_model_provider", create_provider)
    config = replace(_config(tmp_path), inference_provider=route, inference_provider_mode=mode)
    session = await CodingSession.load(config)
    try:
        assert session.inference_provider == expected_route
        assert session.inference_provider_mode == expected_mode
        assert created == [expected_route]
    finally:
        await session.aclose()


@pytest.mark.anyio
async def test_model_switch_uses_override_then_default_and_reset_survives_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str | None] = []

    def create_provider(config: object, **kwargs: object) -> _ClosableFakeProvider:
        created.append(kwargs.get("inference_provider"))  # type: ignore[arg-type]
        return _ClosableFakeProvider([])

    monkeypatch.setattr(session_module, "create_model_provider", create_provider)
    session = await CodingSession.load(_config(tmp_path))
    try:
        session.set_model("org/model-b")
        assert session.inference_provider == "deepinfra"
        session.set_model("org/model-c")
        assert session.inference_provider == "openrouter"
        assert session.inference_provider_mode == "fixed"
        session.set_inference_provider(None)
        await session.reload()
        assert session.inference_provider is None
        assert session.inference_provider_mode == "automatic"
        assert created[:4] == ["openrouter", "deepinfra", "openrouter", None]
        assert created[-1] is None
    finally:
        await session.aclose()


@pytest.mark.anyio
async def test_new_session_reapplies_default_after_explicit_automatic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def create_provider(config: object, **kwargs: object) -> _ClosableFakeProvider:
        return _ClosableFakeProvider([])

    monkeypatch.setattr(session_module, "create_model_provider", create_provider)
    config = _config(tmp_path)
    manager = SessionManager(TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents"))
    record = manager.prepare_session(cwd=tmp_path, model=config.model, provider_name="huggingface")
    session = await CodingSession.load(
        replace(
            config,
            session_manager=manager,
            session_id=record.id,
            storage=JsonlSessionStorage(record.path),
        )
    )
    try:
        session.set_inference_provider(None)
        assert session.inference_provider_mode == "automatic"
        await session.new_session()
        assert session.inference_provider == "openrouter"
        assert session.inference_provider_mode == "fixed"
    finally:
        await session.aclose()


@pytest.mark.anyio
async def test_unsupported_default_route_reports_error_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str | None] = []

    def create_provider(config: object, **kwargs: object) -> _ClosableFakeProvider:
        created.append(kwargs.get("inference_provider"))  # type: ignore[arg-type]
        return _ClosableFakeProvider(
            [
                [
                    AssistantErrorEvent(
                        reason="error",
                        error=AssistantMessage(
                            stop_reason="error",
                            error_message="Model is not supported by openrouter",
                            diagnostics=[
                                AssistantMessageDiagnostic(
                                    type="provider_error", details={"status_code": 400}
                                )
                            ],
                        ),
                    )
                ]
            ]
        )

    monkeypatch.setattr(session_module, "create_model_provider", create_provider)
    session = await CodingSession.load(_config(tmp_path))
    try:
        events = [event async for event in session.prompt("Hello")]
        assert session.inference_provider == "openrouter"
        assert session.inference_provider_mode == "fixed"
        assert created == ["openrouter"]
        errors = [
            event.message
            for event in events
            if event.type == "message_end" and isinstance(event.message, AssistantMessage)
        ]
        assert errors[-1].error_message == "Model is not supported by openrouter"
        assert not any(event.type == "auto_retry_start" for event in events)
    finally:
        await session.aclose()
