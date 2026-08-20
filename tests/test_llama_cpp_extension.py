"""Deterministic Phase 5 tests for the trusted llama.cpp integration."""

from __future__ import annotations

import json
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx
import pytest

from conftest import isolate_home
from tau_agent.harness import SimpleCancellationToken
from tau_coding.credentials import FileCredentialStore
from tau_coding.extensions import ExtensionRuntime
from tau_coding.extensions.builtins.llama_cpp import service as llama_service
from tau_coding.extensions.builtins.llama_cpp.service import (
    LLAMA_CPP_API_KEY_ENV,
    LLAMA_CPP_DEFAULT_ENDPOINT,
    LLAMA_CPP_ENDPOINT_ENV,
    LlamaCppError,
    LlamaCppService,
    normalize_llama_cpp_endpoint,
)
from tau_coding.extensions.builtins.llama_cpp.state import (
    LLAMA_CPP_CREDENTIAL_PREFIX,
    LlamaCppIntegrationState,
    LlamaCppStateError,
    LlamaCppStateStore,
    LlamaCppStoredModel,
)
from tau_coding.extensions.providers import ProviderRefreshContext, ResolvedProviderAuth
from tau_coding.local_backends import LocalOperationContext
from tau_coding.paths import TauPaths
from tau_coding.provider_runtime import create_dynamic_model_provider
from tau_coding.resources import TauResourcePaths

pytestmark = pytest.mark.anyio

SERVER = "http://llama.test:8080"
MODEL = LlamaCppStoredModel(
    "qwen-local",
    display_name="Qwen local",
    context_window=32768,
    input_modalities=("text",),
)


def _context(action: str = "refresh") -> LocalOperationContext:
    return LocalOperationContext(
        signal=SimpleCancellationToken(),
        action=action,  # type: ignore[arg-type]
        generation_id="test-generation",
        backend_id="llama.cpp",
        source_id="built-in:llama.cpp",
        _is_current=lambda: True,
        _progress=lambda _: None,
    )


def _state(
    endpoint: str = SERVER,
    *,
    selected_model: str | None = "qwen-local",
    credential_ref: str | None = None,
    models: tuple[LlamaCppStoredModel, ...] = (MODEL,),
    checked_at: str | None = "2026-08-20T00:00:00Z",
) -> LlamaCppIntegrationState:
    return LlamaCppIntegrationState(
        endpoint=endpoint,
        selected_model=selected_model,
        credential_ref=credential_ref,
        models=models,
        checked_at=checked_at,
    )


def _client(
    handler: Callable[[httpx.Request], httpx.Response | Exception],
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def dispatch(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        result = handler(request)
        if isinstance(result, Exception):
            raise result
        return result

    return httpx.AsyncClient(transport=httpx.MockTransport(dispatch)), requests


def _healthy_handler(
    models: list[Mapping[str, object]] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    payload = models if models is not None else [{"id": "qwen-local", "name": "Qwen local"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": payload})
        return httpx.Response(404)

    return handler


def _service(
    tmp_path: Path,
    *,
    client: httpx.AsyncClient | None = None,
    credentials: object | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[LlamaCppService, LlamaCppStateStore, FileCredentialStore]:
    paths = TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    state_store = LlamaCppStateStore(paths=paths)
    credential_store = FileCredentialStore(paths.home / "credentials.json")
    service = LlamaCppService(
        state_store=state_store,
        credential_store=credentials or credential_store,
        environment=environment,
        client=client,
    )
    return service, state_store, credential_store


def _runtime(
    tmp_path: Path,
    *,
    client: httpx.AsyncClient | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[ExtensionRuntime, TauPaths, FileCredentialStore]:
    paths = TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    credentials = FileCredentialStore(paths.home / "credentials.json")
    runtime = ExtensionRuntime(
        paths=paths,
        credentials=credentials,
        built_in_credentials=credentials,
        environment=environment,
        built_in_http_client=client,
    )
    runtime.load(
        TauResourcePaths(
            root=paths.home,
            agents_root=paths.agents_home,
            paths=paths,
            cwd=tmp_path / "project",
        ),
        include_resource_dirs=False,
        include_user_dir=False,
    )
    return runtime, paths, credentials


@pytest.mark.parametrize(
    ("value", "root", "inference"),
    [
        ("http://LOCALHOST:8080", "http://LOCALHOST:8080", "http://LOCALHOST:8080/v1"),
        ("http://localhost:8080/", "http://localhost:8080", "http://localhost:8080/v1"),
        ("http://localhost:8080/v1", "http://localhost:8080", "http://localhost:8080/v1"),
        (
            "http://localhost:8080/api/v1/",
            "http://localhost:8080/api",
            "http://localhost:8080/api/v1",
        ),
    ],
)
def test_endpoint_normalization(value: str, root: str, inference: str) -> None:
    endpoint = normalize_llama_cpp_endpoint(value)
    assert endpoint.server_root == root
    assert endpoint.inference_base == inference


@pytest.mark.parametrize(
    "value",
    ["", "localhost:8080", "ftp://localhost", "http://user:pass@localhost", "http://localhost?a=1"],
)
def test_endpoint_normalization_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_llama_cpp_endpoint(value)


def test_endpoint_precedence_is_stored_then_environment_then_default(tmp_path: Path) -> None:
    state_store = LlamaCppStateStore(
        paths=TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    )
    state_store.save(_state(endpoint=f"{SERVER}/saved"))
    stored = LlamaCppService(
        state_store=state_store,
        environment={LLAMA_CPP_ENDPOINT_ENV: "http://env.test:9000"},
    )
    assert stored.endpoint.server_root == f"{SERVER}/saved"

    environment = LlamaCppService(
        state_store=LlamaCppStateStore(
            paths=TauPaths(home=tmp_path / "other", agents_home=tmp_path / "agents")
        ),
        environment={LLAMA_CPP_ENDPOINT_ENV: "http://env.test:9000/v1"},
    )
    assert environment.endpoint.server_root == "http://env.test:9000"

    dormant = LlamaCppService(
        state_store=LlamaCppStateStore(
            paths=TauPaths(home=tmp_path / "empty", agents_home=tmp_path / "agents")
        ),
        environment={},
    )
    assert dormant.endpoint.server_root == LLAMA_CPP_DEFAULT_ENDPOINT
    assert dormant.configured is False


@pytest.mark.anyio
async def test_auth_headers_cover_stored_environment_fallback_and_no_auth(tmp_path: Path) -> None:
    client, requests = _client(_healthy_handler())
    service, state_store, credentials = _service(
        tmp_path,
        client=client,
        environment={LLAMA_CPP_API_KEY_ENV: "environment-key"},
    )
    ref = f"{LLAMA_CPP_CREDENTIAL_PREFIX}stored"
    credentials.set(ref, "stored-key")
    state_store.save(_state(credential_ref=ref))
    service = LlamaCppService(
        state_store=state_store,
        credential_store=credentials,
        environment={LLAMA_CPP_API_KEY_ENV: "environment-key"},
        client=client,
    )
    await service.refresh(_context())
    assert requests[0].headers["authorization"] == "Bearer stored-key"

    state_store.clear()
    requests.clear()
    service = LlamaCppService(
        state_store=state_store,
        credential_store=credentials,
        environment={
            LLAMA_CPP_ENDPOINT_ENV: SERVER,
            LLAMA_CPP_API_KEY_ENV: "environment-key",
        },
        client=client,
    )
    await service.refresh(_context())
    assert requests[0].headers["authorization"] == "Bearer environment-key"

    requests.clear()
    service = LlamaCppService(
        state_store=state_store,
        client=client,
        environment={LLAMA_CPP_ENDPOINT_ENV: SERVER},
    )
    await service.refresh(_context())
    assert "authorization" not in requests[0].headers
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_discovery_reports_authentication_http_failures(
    tmp_path: Path, status_code: int
) -> None:
    client, _ = _client(lambda request: httpx.Response(status_code, text="denied"))
    service, state_store, _ = _service(tmp_path, client=client)
    state_store.save(_state())
    service = LlamaCppService(
        state_store=state_store,
        credential_store=FileCredentialStore(tmp_path / "tau" / "credentials.json"),
        client=client,
    )
    result = await service.refresh(_context())
    assert result.backend_status is not None
    assert result.backend_status.stale is True
    assert "rejected" in result.diagnostics[0].message
    await client.aclose()


@pytest.mark.anyio
async def test_discovery_timeout_and_health_loading_are_actionable(tmp_path: Path) -> None:
    def timeout(request: httpx.Request) -> Exception:
        return httpx.ReadTimeout("timed out", request=request)

    client, _ = _client(timeout)
    service, state_store, _ = _service(tmp_path, client=client)
    state_store.save(_state())
    service = LlamaCppService(
        state_store=state_store,
        credential_store=FileCredentialStore(tmp_path / "tau" / "credentials.json"),
        client=client,
    )
    with pytest.raises(LlamaCppError, match="Timed out"):
        await service.discover(ResolvedProviderAuth())
    result = await service.refresh(_context())
    assert result.backend_status is not None
    assert "Timed out" in result.backend_status.diagnostics[0].message
    await client.aclose()

    loading_client, _ = _client(lambda request: httpx.Response(503, json={"status": "loading"}))
    loading_service, _, _ = _service(tmp_path / "loading", client=loading_client)
    with pytest.raises(LlamaCppError, match="still loading"):
        await loading_service.discover(ResolvedProviderAuth())
    await loading_client.aclose()
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"data": []},
        {"data": [{"id": "same"}, {"id": "same"}]},
        {"data": [{"name": "missing"}]},
    ],
)
async def test_malformed_empty_and_multiple_model_responses(
    tmp_path: Path, payload: object
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if payload is None:
            return httpx.Response(200, text="")
        return httpx.Response(200, json=payload)

    client, _ = _client(handler)
    service, _, _ = _service(tmp_path, client=client)
    if payload == {"data": []}:
        discovery = await service.discover(ResolvedProviderAuth())
        assert discovery.models == ()
    else:
        with pytest.raises(LlamaCppError, match="malformed|duplicate|exact id"):
            await service.discover(ResolvedProviderAuth())
    await client.aclose()


@pytest.mark.anyio
async def test_malformed_model_payloads_raise_without_guessing_metadata(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "gpt-5.4-local",
                        "name": "",
                        "context_length": 32768,
                        "input_modalities": ["text", "unknown"],
                        "object": "model",
                        "owned_by": "llama",
                        "secret_metadata": "do-not-persist",
                    }
                ]
            },
        )

    client, _ = _client(handler)
    service, state_store, _ = _service(tmp_path, client=client)
    discovery = await service.discover(ResolvedProviderAuth())
    model = discovery.models[0]
    assert model.id == "gpt-5.4-local"
    assert model.context_window == 32768
    assert model.input_modalities is None
    assert model.compat == {"object": "model", "owned_by": "llama"}
    state_store.save(
        _state(
            selected_model=model.id,
            models=(LlamaCppStoredModel(model.id, context_window=32768),),
        )
    )
    assert "secret_metadata" not in state_store.path.read_text()
    await client.aclose()


@pytest.mark.anyio
async def test_dormant_cache_and_offline_refresh_are_network_free(tmp_path: Path) -> None:
    dormant, _, _ = _service(tmp_path / "dormant", environment={})
    assert dormant.configured is False
    assert dormant.provider().models == ()
    assert (await dormant.status(_context("refresh"))).state == "unconfigured"

    client, requests = _client(lambda request: httpx.Response(500))
    cached, state_store, credentials = _service(tmp_path / "cached", client=client)
    state_store.save(_state())
    cached = LlamaCppService(
        state_store=state_store,
        credential_store=credentials,
        environment={},
        client=client,
    )
    provider = cached.provider()
    snapshot = await cached.refresh_provider_models(
        ProviderRefreshContext(
            signal=SimpleCancellationToken(),
            allow_network=False,
            cached_models=provider.models,
            auth=ResolvedProviderAuth(),
        )
    )
    assert snapshot.models == provider.models
    assert requests == []
    offline = await cached.refresh(_context())
    assert offline.backend_status is not None
    assert offline.backend_status.stale is True
    assert offline.backend_status.cached is True
    await client.aclose()


@pytest.mark.anyio
async def test_refresh_marks_missing_active_model_stale_without_replacing_it(
    tmp_path: Path,
) -> None:
    client, _ = _client(_healthy_handler([{"id": "replacement", "name": "Replacement"}]))
    service, state_store, credentials = _service(tmp_path, client=client)
    state_store.save(_state())
    service = LlamaCppService(
        state_store=state_store,
        credential_store=credentials,
        environment={},
        client=client,
    )

    result = await service.refresh(_context())

    assert result.backend_status is not None
    assert result.backend_status.state == "stale"
    assert result.backend_status.selected_model is None
    assert result.backend_status.models[0].id == "replacement"
    assert any("qwen-local" in item.message for item in result.backend_status.diagnostics)
    assert service.provider().default_model is None
    await client.aclose()


@pytest.mark.anyio
async def test_gpt_and_codex_model_ids_use_local_chat_transport(tmp_path: Path) -> None:
    client, _ = _client(_healthy_handler())
    service, state_store, credentials = _service(tmp_path, client=client)
    state_store.save(
        _state(
            selected_model="gpt-5.4-local",
            models=(
                LlamaCppStoredModel("gpt-5.4-local"),
                LlamaCppStoredModel("codex-local"),
            ),
        )
    )
    service = LlamaCppService(
        state_store=state_store,
        credential_store=credentials,
        environment={},
        client=client,
    )
    provider = service.provider()
    for model in ("gpt-5.4-local", "codex-local"):
        runtime = await create_dynamic_model_provider(
            provider,
            model=model,
            credential_store=credentials,
            environment={},
        )
        assert runtime._config.api == "openai-completions"
        assert runtime._config.infer_api_from_model is False
        await runtime.aclose()
    await client.aclose()


def test_state_is_atomic_locked_private_and_recovers_interrupted_writes(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    store = LlamaCppStateStore(paths=paths)
    store.save(_state())
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700

    temporary = store.path.parent / f".{store.path.name}.interrupted.tmp"
    temporary.write_text("partial", encoding="utf-8")
    assert store.active() == _state()
    assert not temporary.exists()

    original = store.path.read_text(encoding="utf-8")
    with pytest.raises(LlamaCppStateError):
        store.path.write_text("{broken", encoding="utf-8")
        store.active()
    store.path.write_text(original, encoding="utf-8")
    assert store.active() == _state()


def test_state_schema_validation_and_legacy_recovery(tmp_path: Path) -> None:
    path = tmp_path / "llama.json"
    legacy = {
        "schema_version": 1,
        "endpoint": SERVER,
        "selected_model": "qwen-local",
        "credential_ref": None,
        "models": [MODEL.to_json()],
        "checked_at": None,
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    store = LlamaCppStateStore(path)
    assert store.active() == _state(checked_at=None)
    store.save(_state(checked_at=None))
    upgraded = json.loads(path.read_text(encoding="utf-8"))
    assert "endpoints" in upgraded

    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(LlamaCppStateError, match="schema version"):
        store.active()
    path.write_text(json.dumps({"schema_version": 1, "unknown": True}), encoding="utf-8")
    with pytest.raises(LlamaCppStateError, match="Unknown field"):
        store.active()


def test_state_replace_is_atomic_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tau_coding.extensions.builtins.llama_cpp.state as state_module

    store = LlamaCppStateStore(tmp_path / "state.json")
    store.save(_state())
    original = store.path.read_text(encoding="utf-8")

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        del source, destination
        raise OSError("disk full")

    monkeypatch.setattr(state_module.os, "replace", fail_replace)
    with pytest.raises(LlamaCppStateError, match="write"):
        store.save(_state(endpoint="http://other.test"))
    assert store.path.read_text(encoding="utf-8") == original
    assert not tuple(store.path.parent.glob(f".{store.path.name}.*.tmp"))


@pytest.mark.anyio
async def test_setup_status_refresh_use_doctor_and_reset_through_real_runtime(
    tmp_path: Path,
) -> None:
    current_models = [{"id": "qwen-local", "name": "Qwen local"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": current_models})
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            payload = json.loads(request.content)
            if payload.get("tools"):
                text = (
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
                    '"function":{"name":"tau_probe","arguments":"{}"}}]},'
                    '"finish_reason":"tool_calls"}]}'
                    "\n\ndata: [DONE]\n\n"
                )
            else:
                text = (
                    'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}'
                    "\n\ndata: [DONE]\n\n"
                )
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        return httpx.Response(404)

    client, requests = _client(handler)
    runtime, paths, credentials = _runtime(tmp_path, client=client)
    registry = runtime.local_backend_registry
    assert registry.effective("llama.cpp") is not None
    initial = await registry.status("llama.cpp")
    assert initial.backend_status is not None
    assert initial.backend_status.state == "unconfigured"

    configured = await registry.configure(
        "llama.cpp", {"endpoint": f"{SERVER}/v1", "api_key": "optional-secret"}
    )
    assert configured.committed is True
    assert configured.stale is False
    assert configured.backend_status is not None
    assert configured.backend_status.state == "ready"
    assert configured.backend_status.authentication_source == "stored credential"
    assert runtime.provider_registry.effective("llama.cpp").definition.models  # type: ignore[union-attr]
    assert credentials.names(prefix=LLAMA_CPP_CREDENTIAL_PREFIX)

    current_models[:] = [{"id": "new-local", "name": "New local"}]
    refreshed = await registry.refresh("llama.cpp")
    assert refreshed.stale is False
    assert refreshed.backend_status is not None
    assert refreshed.backend_status.models[0].id == "new-local"
    view = registry.effective("llama.cpp")
    assert view is not None and view.use_available
    assert refreshed.backend_status.state == "stale"
    assert "use" not in refreshed.backend_status.actions

    diagnosed = await registry.doctor("llama.cpp")
    assert diagnosed.backend_status is not None
    assert any(item.stage == "streaming" for item in diagnosed.diagnostics)
    assert any(item.stage == "tools" for item in diagnosed.diagnostics), diagnosed.diagnostics
    assert any(request.method == "POST" for request in requests)

    reset = await registry.reset("llama.cpp")
    assert reset.committed is True
    assert not paths.llama_cpp_state_path.exists()
    assert credentials.names(prefix=LLAMA_CPP_CREDENTIAL_PREFIX)
    assert (await registry.status("llama.cpp")).backend_status.state == "unconfigured"  # type: ignore[union-attr]
    await runtime.aclose()
    await client.aclose()


@pytest.mark.anyio
async def test_generation_credential_commit_orphan_reset_and_partial_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(_healthy_handler())
    service, state_store, credentials = _service(tmp_path, client=client)
    first = await service.configure({"endpoint": SERVER, "api_key": "first"}, _context("configure"))
    assert first.committed is True
    old_ref = state_store.active().credential_ref  # type: ignore[union-attr]
    second = await service.configure(
        {"endpoint": SERVER, "api_key": "second"}, _context("configure")
    )
    assert second.committed is True
    assert credentials.get(old_ref) is None  # type: ignore[arg-type]

    original_save = state_store.save
    monkeypatch.setattr(
        state_store,
        "save",
        lambda *args, **kwargs: (_ for _ in ()).throw(LlamaCppStateError("save failed")),
    )
    failed = await service.configure(
        {"endpoint": SERVER, "api_key": "third"}, _context("configure")
    )
    assert failed.committed is False
    assert failed.credential_orphaned is False
    assert state_store.active().credential_ref == state_store.get(SERVER).credential_ref  # type: ignore[union-attr]
    assert not any(
        credentials.get(name) == "third"
        for name in credentials.names(prefix=LLAMA_CPP_CREDENTIAL_PREFIX)
    )
    monkeypatch.setattr(state_store, "save", original_save)

    original_delete = credentials.delete

    def fail_new_credential(name: str) -> None:
        if credentials.get(name) == "fourth":
            raise OSError("credential cleanup failed")
        original_delete(name)

    monkeypatch.setattr(credentials, "delete", fail_new_credential)
    monkeypatch.setattr(
        state_store,
        "save",
        lambda *args, **kwargs: (_ for _ in ()).throw(LlamaCppStateError("save failed")),
    )
    orphaned = await service.configure(
        {"endpoint": SERVER, "api_key": "fourth"}, _context("configure")
    )
    assert orphaned.committed is False
    assert orphaned.credential_orphaned is True
    assert any(
        credentials.get(name) == "fourth"
        for name in credentials.names(prefix=LLAMA_CPP_CREDENTIAL_PREFIX)
    )
    await client.aclose()


@pytest.mark.anyio
async def test_credential_cleanup_failure_is_reported_and_reset_is_recoverable(
    tmp_path: Path,
) -> None:
    client, _ = _client(_healthy_handler())
    service, state_store, credentials = _service(tmp_path, client=client)
    configured = await service.configure(
        {"endpoint": SERVER, "api_key": "secret"}, _context("configure")
    )
    assert configured.committed
    ref = state_store.active().credential_ref  # type: ignore[union-attr]
    original_delete = credentials.delete

    def fail_old(name: str) -> None:
        if name == ref:
            raise OSError("credential file unavailable")
        original_delete(name)

    credentials.delete = fail_old  # type: ignore[method-assign]
    changed = await service.configure(
        {"endpoint": SERVER, "api_key": "new-secret"}, _context("configure")
    )
    assert changed.committed is True
    assert changed.credential_orphaned is True
    assert any(item.stage == "credentials" for item in changed.diagnostics)

    reset = await service.reset(_context("reset"))
    assert reset.committed is True
    assert credentials.get(ref) == "secret"
    credentials.delete = original_delete  # type: ignore[method-assign]
    recovered = LlamaCppService(
        state_store=state_store,
        credential_store=credentials,
        environment={},
        client=client,
    )
    assert recovered.orphaned_credentials == ()
    assert credentials.get(ref) is None
    await client.aclose()


@pytest.mark.anyio
async def test_print_mode_explicit_dynamic_startup_uses_cached_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tau_coding.cli import run_print_mode
    from tau_coding.rendering import PrintOutputMode

    paths = TauPaths(home=tmp_path / "tau", agents_home=tmp_path / "agents")
    LlamaCppStateStore(paths=paths).save(_state())
    project = tmp_path / "project"
    project.mkdir()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response | Exception:
        requests.append(request)
        if request.method == "GET":
            return httpx.ConnectError("server is down", request=request)
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":"stop"}]}\n'
                "\ndata: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    def fake_client(*, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llama_service, "create_async_client", fake_client)
    import tau_ai.openai_compatible as compatible

    monkeypatch.setattr(compatible, "create_async_client", fake_client)
    ok = await run_print_mode(
        prompt="say hello",
        model="qwen-local",
        cwd=project,
        provider=None,
        output=PrintOutputMode.text,
        resource_paths=TauResourcePaths(
            root=paths.home, agents_root=paths.agents_home, paths=paths
        ),
        provider_name="llama.cpp",
        requested_provider="llama.cpp",
        requested_model="qwen-local",
        trust_default="ask",
    )
    assert ok is True
    assert "hello" in capsys.readouterr().out
    assert [request.url.path for request in requests] == ["/v1/chat/completions"]


@pytest.mark.anyio
async def test_tui_explicit_dynamic_startup_uses_cached_state_during_downtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tau_coding import SessionManager
    from tau_coding.tui import app as tui_app

    isolate_home(monkeypatch, tmp_path)
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    LlamaCppStateStore(paths=paths).save(_state())
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(paths)
    captured: dict[str, object] = {}

    class HeadlessTui:
        def __init__(self, session: object, **kwargs: object) -> None:
            del kwargs
            captured["session"] = session

        async def run_async(self) -> None:
            return None

    def unavailable_client(*, timeout: float) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> Exception:
            raise httpx.ConnectError("server is down", request=request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(tui_app, "TauTuiApp", HeadlessTui)
    monkeypatch.setattr(llama_service, "create_async_client", unavailable_client)
    session_id = await tui_app.run_tui_app(
        model="qwen-local",
        provider_name="llama.cpp",
        cwd=project,
        session_manager=manager,
    )
    assert session_id is not None
    session = captured["session"]
    assert session.provider._config.base_url == f"{SERVER}/v1"  # type: ignore[union-attr]
    assert session.provider_name == "llama.cpp"  # type: ignore[union-attr]
    await session.aclose()  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_builtin_source_lifecycle_is_generation_local(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    old_registry = runtime.provider_registry
    assert runtime.extension_metadata[0].source_id == "built-in:llama.cpp"
    assert runtime.extension_metadata[0].hidden is True
    assert runtime.extension_names == ()
    assert runtime.local_backend_registry.effective("llama.cpp") is not None

    runtime.reset_for_reload()
    assert old_registry.effective("llama.cpp") is None
    runtime.load(
        TauResourcePaths(root=tmp_path / "tau", agents_root=tmp_path / "agents"),
        include_resource_dirs=False,
        include_user_dir=False,
    )
    assert runtime.provider_registry.effective("llama.cpp") is not None
    await runtime.aclose()


__all__ = []
