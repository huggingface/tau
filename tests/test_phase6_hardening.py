"""Phase 6 lifecycle, race, ownership, and persistence regressions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from tau_agent.harness import SimpleCancellationToken
from tau_coding.credentials import FileCredentialStore
from tau_coding.extensions import (
    DynamicProvider,
    DynamicProviderRegistry,
    ExtensionRuntime,
    LocalBackend,
    LocalBackendRegistry,
    LocalBackendStatus,
    LocalConfigureResult,
    LocalConfigureSpec,
    LocalModel,
    LocalOperationContext,
    NoAuth,
    OpenAICompatibleTransport,
    ProviderModel,
    ProviderModelSnapshot,
)
from tau_coding.extensions.builtins.llama_cpp.service import (
    LlamaCppService,
)
from tau_coding.extensions.builtins.llama_cpp.state import (
    LLAMA_CPP_CREDENTIAL_PREFIX,
    LlamaCppIntegrationState,
    LlamaCppStateError,
    LlamaCppStateStore,
    LlamaCppStoredModel,
)
from tau_coding.local_backends import (
    LOCAL_OPERATION_CANCELLATION_TIMEOUT_SECONDS,
    LocalOperationResult,
)
from tau_coding.paths import TauPaths

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _provider(provider_id: str = "second", *, refresh_models=None) -> DynamicProvider:
    return DynamicProvider(
        id=provider_id,
        display_name=provider_id,
        models=(ProviderModel("model"),),
        default_model="model",
        transport=OpenAICompatibleTransport(
            base_url="http://second.test/v1",
            auth=NoAuth(),
        ),
        refresh_models=refresh_models,
    )


def _context(action: str, backend_id: str = "second") -> LocalOperationContext:
    return LocalOperationContext(
        signal=SimpleCancellationToken(),
        action=action,  # type: ignore[arg-type]
        generation_id="generation",
        backend_id=backend_id,
        source_id="test:second",
        _is_current=lambda: True,
        _progress=lambda _: None,
    )


async def test_refresh_stress_keeps_latest_snapshot_and_closes_generation() -> None:
    calls = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def refresh(context) -> ProviderModelSnapshot:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        model = f"model-{calls}"
        return ProviderModelSnapshot((ProviderModel(model),), model)

    runtime = ExtensionRuntime()
    registry = runtime.provider_registry
    registry.register("test:second", _provider(refresh_models=refresh))
    waiters = [asyncio.create_task(registry.refresh("second")) for _ in range(20)]
    await entered.wait()
    release.set()
    results = await asyncio.gather(*waiters)

    assert calls == 1
    assert {result.status for result in results} == {"published"}
    assert registry.effective("second").definition.models[0].id == "model-1"  # type: ignore[union-attr]
    await runtime.aclose()
    assert registry.effective("second") is None


async def test_refresh_from_retired_generation_cannot_publish() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def refresh(context) -> ProviderModelSnapshot:
        entered.set()
        await release.wait()
        return ProviderModelSnapshot((ProviderModel("stale"),), "stale")

    runtime = ExtensionRuntime()
    registry = runtime.provider_registry
    registry.register("test:second", _provider())
    # Replace the callback while keeping the test provider's public shape.
    current = registry.effective("second").definition  # type: ignore[union-attr]
    registry.register(
        "test:second",
        DynamicProvider(
            id=current.id,
            display_name=current.display_name,
            models=current.models,
            default_model=current.default_model,
            transport=current.transport,
            refresh_models=refresh,
        ),
    )
    pending = asyncio.create_task(registry.refresh("second"))
    await entered.wait()
    runtime.reset_for_reload()
    release.set()
    result = await pending

    assert result.status == "cancelled"
    assert registry.effective("second") is None
    await runtime.aclose()


async def test_local_retire_bounds_cancellation_resistant_backend() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    providers.register("test:second", _provider())
    registry = LocalBackendRegistry(providers, generation_id="generation")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def operation(context: LocalOperationContext) -> LocalOperationResult:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Simulate a backend finishing network cleanup later. The
                # registry must not inject another cancellation into this path.
                continue
        return LocalOperationResult(backend_status=LocalBackendStatus(state="ready"))

    backend = LocalBackend(
        id="second",
        provider_id="second",
        display_name="Second",
        configure_spec=LocalConfigureSpec(),
        configure=lambda values, context: LocalConfigureResult(),
        status=operation,
        refresh=operation,
    )
    registry.register("test:second", backend)
    pending = asyncio.create_task(registry.refresh("second"))
    await entered.wait()

    started = asyncio.get_running_loop().time()
    close_task = asyncio.create_task(registry.aclose())
    await asyncio.sleep(LOCAL_OPERATION_CANCELLATION_TIMEOUT_SECONDS / 2)
    assert not close_task.done()
    await close_task
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 1.0
    assert pending.done() is False

    release.set()
    result = await asyncio.wait_for(pending, timeout=0.5)
    assert result.stale is True
    await providers.aclose()


async def test_local_replacement_discards_late_result_and_new_operation_wins() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    providers.register("test:second", _provider())
    registry = LocalBackendRegistry(providers, generation_id="generation")
    old_started = asyncio.Event()
    old_release = asyncio.Event()

    async def old_refresh(context: LocalOperationContext) -> LocalBackendStatus:
        old_started.set()
        await old_release.wait()
        return LocalBackendStatus(state="ready", models=(LocalModel("old"),))

    async def new_refresh(context: LocalOperationContext) -> LocalBackendStatus:
        return LocalBackendStatus(state="ready", models=(LocalModel("new"),))

    def backend(refresh):
        return LocalBackend(
            id="second",
            provider_id="second",
            display_name="Second",
            configure_spec=LocalConfigureSpec(),
            configure=lambda values, context: LocalConfigureResult(),
            status=refresh,
            refresh=refresh,
        )

    registry.register("test:second", backend(old_refresh))
    old_task = asyncio.create_task(registry.refresh("second"))
    await old_started.wait()
    registry.register("test:second", backend(new_refresh))
    old_release.set()
    assert (await old_task).stale is True
    fresh = await registry.refresh("second")
    assert fresh.backend_status is not None
    assert fresh.backend_status.models[0].id == "new"
    await registry.aclose()
    await providers.aclose()


async def test_missing_model_reference_survives_refresh_and_restart(tmp_path: Path) -> None:
    endpoint = "http://local.test:8080"
    paths = TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    state_store = LlamaCppStateStore(paths=paths)
    state_store.save(
        LlamaCppIntegrationState(
            endpoint=endpoint,
            selected_model="returning-model",
            models=(LlamaCppStoredModel("other-model"),),
        )
    )
    credentials = FileCredentialStore(paths.home / "credentials.json")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"status": "ok"}
                if request.url.path == "/health"
                else {"data": [{"id": "other-model"}]},
            )
        )
    )
    service = LlamaCppService(
        state_store=state_store,
        credential_store=credentials,
        environment={},
        client=client,
    )
    assert service.provider().default_model is None
    result = await service.refresh(_context("refresh", "llama.cpp"))
    assert result.backend_status is not None
    assert result.backend_status.stale is True
    assert state_store.active().selected_model == "returning-model"  # type: ignore[union-attr]

    restarted = LlamaCppService(
        state_store=LlamaCppStateStore(paths=paths),
        credential_store=credentials,
        environment={},
        client=client,
    )
    assert restarted.provider().default_model is None
    restarted_status = await restarted.status(_context("refresh", "llama.cpp"))
    assert restarted_status.stale is True
    assert restarted.provider().default_model is None
    await client.aclose()


@pytest.mark.parametrize("selected_model", ["", " returning-model"])
def test_state_rejects_non_exact_stale_model_references(selected_model: str) -> None:
    with pytest.raises(LlamaCppStateError, match="non-empty exact"):
        LlamaCppIntegrationState(endpoint="http://local.test", selected_model=selected_model)


async def test_secret_state_and_diagnostics_never_contain_key(tmp_path: Path) -> None:
    secret = "phase-six-secret"
    paths = TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    state_store = LlamaCppStateStore(paths=paths)
    credentials = FileCredentialStore(paths.home / "credentials.json")

    async def dispatch(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(dispatch))
    service = LlamaCppService(
        state_store=state_store,
        credential_store=credentials,
        environment={},
        client=client,
    )
    result = await service.configure(
        {"endpoint": "http://127.0.0.1:8080", "api_key": secret},
        _context("configure", "llama.cpp"),
    )
    serialized = state_store.path.read_text(encoding="utf-8")
    assert secret not in serialized
    assert secret not in repr(result)
    assert secret not in repr(service.provider())
    refs = credentials.names(prefix=LLAMA_CPP_CREDENTIAL_PREFIX)
    assert len(refs) == 1
    assert credentials.get(refs[0]) == secret
    payload = json.loads(serialized)
    assert payload["endpoints"]["http://127.0.0.1:8080"]["credential_ref"] == refs[0]
    await client.aclose()
