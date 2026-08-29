"""Tests for dynamic extension provider contracts and registry lifecycle."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx2
import pytest

from tau_agent import UserMessage
from tau_ai import OpenAICompatibleProvider
from tau_coding import TauResourcePaths
from tau_coding.diagnostics import AgentCallDiagnosticContext, AgentCallDiagnosticLogger
from tau_coding.extensions import (
    DynamicProvider,
    DynamicProviderError,
    DynamicProviderRegistry,
    ExtensionRuntime,
    NoAuth,
    OpenAICompatibleTransport,
    OptionalApiKey,
    ProviderAuthError,
    ProviderModel,
    ProviderModelSnapshot,
    RequiredApiKey,
    ResolvedProviderAuth,
    resolve_provider_auth,
)
from tau_coding.extensions.provider_registry import (
    _SUPERVISED_DISCOVERY_TASKS,
    MAX_PROVIDER_REFRESH_DIAGNOSTICS,
    PROVIDER_DISCOVERY_CANCELLATION_TIMEOUT_SECONDS,
)
from tau_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderConfigError,
    ProviderModelMetadata,
)
from tau_coding.provider_runtime import create_dynamic_model_provider

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class MemoryCredentials:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}
        self.reads: list[str] = []

    def get(self, name: str) -> str | None:
        self.reads.append(name)
        return self.values.get(name)


class ExplodingCredentials:
    def get(self, name: str) -> str | None:
        raise AssertionError(f"must not read {name}")


class FixedAuth:
    def __init__(self, resolved: ResolvedProviderAuth) -> None:
        self._resolved = resolved

    async def resolve(self, context) -> ResolvedProviderAuth:
        del context
        return self._resolved


def provider(
    provider_id: str = "local",
    *,
    models: tuple[ProviderModel, ...] = (ProviderModel("model"),),
    default_model: str | None = "model",
    refresh_models=None,
    auth=None,
) -> DynamicProvider:
    return DynamicProvider(
        id=provider_id,
        display_name=provider_id.title(),
        models=models,
        default_model=default_model,
        transport=OpenAICompatibleTransport(
            base_url="http://127.0.0.1:8080/v1",
            auth=auth or NoAuth(),
        ),
        refresh_models=refresh_models,
    )


def _provider_extension_body(model_id: str, *, fail_setup: bool = False) -> str:
    failure = "\n    raise RuntimeError('setup exploded')" if fail_setup else ""
    return f"""
from tau_coding.extensions import (
    DynamicProvider,
    NoAuth,
    OpenAICompatibleTransport,
    ProviderModel,
)


def setup(tau):
    tau.register_provider(DynamicProvider(
        id="local",
        display_name="Local",
        models=(ProviderModel("{model_id}"),),
        default_model="{model_id}",
        transport=OpenAICompatibleTransport(
            base_url="http://example.test/v1",
            auth=NoAuth(),
        ),
    )){failure}
"""


async def test_provider_contract_accepts_dormant_zero_model_provider() -> None:
    dormant = provider(models=(), default_model=None)

    assert dormant.models == ()
    assert dormant.default_model is None


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: provider(""), "Provider id"),
        (
            lambda: DynamicProvider(
                id="valid",
                display_name=" ",
                transport=OpenAICompatibleTransport(base_url="http://example.test/v1"),
            ),
            "Provider display name",
        ),
        (
            lambda: provider(models=(ProviderModel("duplicate"), ProviderModel("duplicate"))),
            "unique",
        ),
        (
            lambda: provider(models=(ProviderModel("one"),), default_model="missing"),
            "Default model",
        ),
        (
            lambda: DynamicProvider(id="invalid", display_name="Invalid"),
            "exactly one",
        ),
        (
            lambda: DynamicProvider(
                id="invalid",
                display_name="Invalid",
                transport=OpenAICompatibleTransport(base_url="http://example.test/v1"),
                runtime_factory=lambda context, model: None,  # type: ignore[arg-type,return-value]
            ),
            "exactly one",
        ),
        (lambda: ProviderModel(" model"), "surrounding whitespace"),
        (lambda: ProviderModel("model", context_window=0), "positive integer"),
        (
            lambda: ProviderModel("model", compat={"bad": object()}),  # type: ignore[dict-item]
            "JSON values",
        ),
        (
            lambda: provider(models=(ProviderModel("model", api="anthropic-messages"),)),
            "OpenAI-compatible provider models",
        ),
        (
            lambda: ProviderModel("model", headers={"Authorization": "secret"}),
            "provider auth strategy",
        ),
        (
            lambda: OpenAICompatibleTransport(
                base_url="http://example.test/v1",
                headers={"authorization": "secret"},
            ),
            "provider auth strategy",
        ),
        (
            lambda: ProviderModel("model", headers={"X-Test": "a", "x-test": "b"}),
            "duplicate header names",
        ),
    ],
)
async def test_provider_contract_rejects_invalid_definitions_atomically(factory, message) -> None:
    with pytest.raises(DynamicProviderError, match=message):
        factory()


async def test_auth_strategies_use_stored_then_environment_then_missing() -> None:
    credentials = MemoryCredentials({"local:key": "stored-secret"})
    environment = {"LOCAL_API_KEY": "environment-secret"}

    stored = await resolve_provider_auth(
        RequiredApiKey("local:key", "LOCAL_API_KEY"),
        credentials=credentials,
        environment=environment,
    )
    from_environment = await resolve_provider_auth(
        RequiredApiKey("missing", "LOCAL_API_KEY"),
        credentials=credentials,
        environment=environment,
    )
    optional = await resolve_provider_auth(
        OptionalApiKey("missing", "MISSING_API_KEY"),
        credentials=credentials,
        environment=environment,
    )

    assert stored.api_key == "stored-secret"
    assert stored.source == "stored credential"
    assert stored.omit_authorization_header is False
    assert from_environment.api_key == "environment-secret"
    assert from_environment.source == "environment variable LOCAL_API_KEY"
    assert optional.api_key is None
    assert optional.source == "none"
    assert optional.omit_authorization_header is True

    with pytest.raises(ProviderAuthError, match="Store credential `missing`"):
        await resolve_provider_auth(
            RequiredApiKey("missing", "MISSING_API_KEY"),
            credentials=credentials,
            environment=environment,
        )


async def test_resolved_auth_rejects_accidental_empty_bearer_configuration() -> None:
    with pytest.raises(ProviderAuthError, match="API key or Authorization"):
        ResolvedProviderAuth(omit_authorization_header=False)


async def test_no_auth_never_reads_secret_sources() -> None:
    resolved = await resolve_provider_auth(
        NoAuth(),
        credentials=ExplodingCredentials(),
        environment={"SECRET": "must-not-read"},
    )

    assert resolved.api_key is None
    assert resolved.omit_authorization_header is True


async def test_adversarial_custom_auth_is_absent_from_reprs_and_diagnostics() -> None:
    secret = "auth-source-super-secret"
    resolved = ResolvedProviderAuth(
        api_key=secret,
        headers={f"X-{secret}": secret},
        source=secret,
        omit_authorization_header=False,
    )

    async def refresh(context):
        assert context.auth is resolved
        raise RuntimeError(secret)

    runtime = ExtensionRuntime()
    registry = runtime.provider_registry
    registry.register(
        "extension",
        provider(refresh_models=refresh, auth=FixedAuth(resolved)),
    )

    result = await registry.refresh("local")
    projected = runtime.diagnostics

    assert result.status == "failed"
    assert secret not in repr(resolved)
    assert secret not in repr(registry.diagnostics)
    assert secret not in repr(projected)


@pytest.mark.parametrize("error_type", [RuntimeError, ProviderAuthError])
async def test_runtime_creation_sanitizes_custom_auth_exceptions_and_diagnostics(
    tmp_path: Path,
    error_type: type[BaseException],
) -> None:
    secret = "runtime-auth-super-secret"

    class ExplodingAuth:
        async def resolve(self, context):
            del context
            raise error_type(secret)

    dynamic = provider(auth=ExplodingAuth())
    with pytest.raises(
        ProviderAuthError,
        match="^Dynamic provider authentication resolution failed$",
    ) as exc_info:
        await create_dynamic_model_provider(
            dynamic,
            model="model",
            credential_store=MemoryCredentials(),  # type: ignore[arg-type]
            environment={},
        )

    error = exc_info.value
    logger = AgentCallDiagnosticLogger(tmp_path / "diagnostics.jsonl")
    logger.log_exception(
        context=AgentCallDiagnosticContext(
            provider_name="local",
            model="model",
            cwd=tmp_path,
            session_id=None,
            run_id="run-1",
        ),
        phase="runtime-creation",
        exc=error,
    )
    diagnostic = json.loads(logger.path.read_text(encoding="utf-8"))

    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in repr(diagnostic)


async def test_runtime_creation_preserves_required_auth_guidance() -> None:
    dynamic = provider(auth=RequiredApiKey("local:key", "LOCAL_API_KEY"))

    with pytest.raises(ProviderAuthError, match="Store credential `local:key`") as exc_info:
        await create_dynamic_model_provider(
            dynamic,
            model="model",
            credential_store=MemoryCredentials(),  # type: ignore[arg-type]
            environment={},
        )

    assert "set LOCAL_API_KEY" in str(exc_info.value)


async def test_runtime_creation_only_preserves_host_authored_required_guidance() -> None:
    secret = "required-reader-super-secret"

    class SecretBearingCredentials:
        def get(self, name: str) -> str | None:
            del name
            raise ProviderAuthError(secret)

    dynamic = provider(auth=RequiredApiKey("local:key", "LOCAL_API_KEY"))
    with pytest.raises(
        ProviderAuthError,
        match="^Dynamic provider authentication resolution failed$",
    ) as exc_info:
        await create_dynamic_model_provider(
            dynamic,
            model="model",
            credential_store=SecretBearingCredentials(),
            environment={},
        )

    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)


async def test_secret_values_are_absent_from_runtime_reprs() -> None:
    credentials = MemoryCredentials({"local:key": "stored-super-secret"})
    resolved = await resolve_provider_auth(
        RequiredApiKey("local:key", "LOCAL_API_KEY"),
        credentials=credentials,
        environment={},
    )
    dynamic = provider(
        models=(ProviderModel("model", headers={"X-Secret": "model-super-secret"}),),
        auth=RequiredApiKey("local:key", "LOCAL_API_KEY"),
    )
    assert dynamic.transport is not None
    dynamic = replace(
        dynamic,
        transport=replace(
            dynamic.transport,
            headers={"X-Transport-Secret": "transport-super-secret"},
        ),
    )
    runtime = await create_dynamic_model_provider(
        dynamic,
        model="model",
        credential_store=credentials,  # type: ignore[arg-type]
        environment={},
    )

    combined = "\n".join((repr(resolved), repr(dynamic), repr(runtime._config)))  # type: ignore[attr-defined]
    assert "stored-super-secret" not in combined
    assert "model-super-secret" not in combined
    assert "transport-super-secret" not in combined
    await runtime.aclose()


@pytest.mark.parametrize("async_factory", [False, True])
@pytest.mark.parametrize("close_raises", [False, True])
async def test_invalid_custom_runtime_is_closed_once_without_masking_error(
    async_factory: bool,
    close_raises: bool,
) -> None:
    class InvalidRuntime:
        stream_response = None

        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            if close_raises:
                raise RuntimeError("close-super-secret")

    invalid = InvalidRuntime()

    if async_factory:

        async def factory(context, model):
            return invalid

    else:

        def factory(context, model):
            return invalid

    dynamic = DynamicProvider(
        id="factory",
        display_name="Factory",
        models=(ProviderModel("custom"),),
        default_model="custom",
        runtime_factory=factory,  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderConfigError, match="unsupported provider") as exc_info:
        await create_dynamic_model_provider(dynamic, model="custom")

    assert "close-super-secret" not in str(exc_info.value)
    assert invalid.close_calls == 1


async def test_custom_runtime_requires_callable_close_member() -> None:
    class InvalidRuntime:
        aclose = None

        def stream_response(self, **kwargs):
            raise AssertionError("must not be called")

    dynamic = DynamicProvider(
        id="factory",
        display_name="Factory",
        models=(ProviderModel("custom"),),
        default_model="custom",
        runtime_factory=lambda context, model: InvalidRuntime(),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderConfigError, match="unsupported provider"):
        await create_dynamic_model_provider(dynamic, model="custom")


async def test_custom_runtime_factory_receives_resolved_auth_and_model() -> None:
    observed = []

    class FactoryRuntime:
        closed = False

        def stream_response(self, **kwargs):
            async def events():
                if False:
                    yield None

            return events()

        async def aclose(self) -> None:
            self.closed = True

    runtime = FactoryRuntime()

    async def factory(context, model):
        observed.append((context, model))
        return runtime

    dynamic = DynamicProvider(
        id="factory",
        display_name="Factory",
        models=(ProviderModel("custom"),),
        default_model="custom",
        runtime_factory=factory,
        runtime_auth=RequiredApiKey("factory:key", "FACTORY_API_KEY"),
    )

    created = await create_dynamic_model_provider(
        dynamic,
        model="custom",
        credential_store=MemoryCredentials({"factory:key": "factory-secret"}),
        environment={},
    )

    assert created is runtime
    assert observed[0][0].provider_id == "factory"
    assert observed[0][0].auth.api_key == "factory-secret"
    assert observed[0][1] == ProviderModel("custom")
    assert "factory-secret" not in repr(observed[0][0])


async def test_durable_baseline_is_overridden_and_restored_exactly() -> None:
    durable = OpenAICompatibleProviderConfig(
        name="local",
        base_url="https://durable.example/v1",
        api_key_env="DURABLE_API_KEY",
        credential_name="durable-key",
        models=("durable-model",),
        default_model="durable-model",
        context_windows={"durable-model": 32768},
        headers={"X-Durable": "complete"},
        compat={"feature": True},
        model_metadata={
            "durable-model": ProviderModelMetadata(
                name="Durable model",
                max_tokens=4096,
                compat={"nested": "kept"},
            )
        },
        timeout_seconds=12,
        max_retries=4,
        max_retry_delay_seconds=3,
    )
    registry = DynamicProviderRegistry((durable,), generation_id="generation-1")

    baseline = registry.effective("local")
    assert baseline is not None
    assert baseline.definition is durable
    token = registry.register("extension-a", provider())
    effective = registry.effective("local")
    assert effective is not None
    assert effective.definition != durable
    assert effective.layer_token == token

    assert registry.unregister("local", "extension-a") is True
    restored = registry.effective("local")
    assert restored is not None
    assert restored.definition is durable
    assert restored.definition == durable
    assert registry.unregister("local", "unknown") is False


async def test_latest_layer_and_same_source_replacement_are_deterministic() -> None:
    registry = DynamicProviderRegistry(generation_id="generation-1")
    first = provider(models=(ProviderModel("first"),), default_model="first")
    second = provider(models=(ProviderModel("second"),), default_model="second")
    replacement = provider(models=(ProviderModel("replacement"),), default_model="replacement")

    first_token = registry.register("source-a", first)
    second_token = registry.register("source-b", second)
    replacement_token = registry.register("source-a", replacement)

    assert [layer.token for layer in registry.layers("local")] == [
        second_token,
        replacement_token,
    ]
    assert registry.effective("local").definition is replacement  # type: ignore[union-attr]
    assert first_token != replacement_token

    # Constructing an invalid replacement fails before registration and leaves
    # the complete prior layer untouched.
    with pytest.raises(DynamicProviderError):
        registry.register(
            "source-a",
            replace(replacement, default_model="not-present"),
        )
    assert registry.effective("local").definition is replacement  # type: ignore[union-attr]

    registry.unregister("local", "source-a")
    assert registry.effective("local").definition is second  # type: ignore[union-attr]


async def test_provider_inputs_are_defensively_copied() -> None:
    headers = {"X-Header": "original"}
    compat = {"nested": {"value": "original"}}
    model = ProviderModel("model", headers=headers, compat=compat)
    headers["X-Header"] = "mutated"
    compat["nested"]["value"] = "mutated"

    assert model.headers["X-Header"] == "original"
    assert model.compat["nested"] == {"value": "original"}


async def test_nested_compat_is_frozen_at_all_registry_exposure_boundaries() -> None:
    cached: list[ProviderModel] = []

    async def refresh(context):
        cached.extend(context.cached_models)
        return ProviderModelSnapshot(
            (
                ProviderModel(
                    "fresh",
                    compat={"nested": {"items": [{"value": "fresh"}]}},
                ),
            ),
            "fresh",
        )

    initial = provider(
        models=(
            ProviderModel(
                "model",
                compat={"nested": {"items": [{"value": "initial"}]}},
            ),
        ),
        refresh_models=refresh,
    )
    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", initial)
    effective = registry.effective("local")
    layer = registry.layers("local")[0]

    assert effective is not None
    assert isinstance(effective.definition, DynamicProvider)
    _assert_nested_compat_is_frozen(effective.definition.models[0], "initial")
    _assert_nested_compat_is_frozen(layer.provider.models[0], "initial")

    result = await registry.refresh("local")

    assert result.status == "published"
    assert result.provider is not None
    _assert_nested_compat_is_frozen(cached[0], "initial")
    _assert_nested_compat_is_frozen(result.provider.models[0], "fresh")
    refreshed = registry.effective("local")
    assert refreshed is not None
    assert isinstance(refreshed.definition, DynamicProvider)
    _assert_nested_compat_is_frozen(refreshed.definition.models[0], "fresh")


def _assert_nested_compat_is_frozen(model: ProviderModel, expected: str) -> None:
    nested = cast(Mapping[str, object], model.compat["nested"])
    items = cast(tuple[object, ...], nested["items"])
    item = cast(Mapping[str, str], items[0])

    assert item["value"] == expected
    with pytest.raises(TypeError):
        cast(dict[str, object], nested)["mutated"] = True
    with pytest.raises(AttributeError):
        cast(list[object], items).append({"value": "mutated"})
    with pytest.raises(TypeError):
        cast(dict[str, str], item)["value"] = "mutated"


async def test_successful_refresh_publishes_one_complete_snapshot() -> None:
    observed_cached: list[tuple[ProviderModel, ...]] = []

    async def refresh(context):
        observed_cached.append(context.cached_models)
        return ProviderModelSnapshot(
            models=(ProviderModel("new-a"), ProviderModel("new-b")),
            default_model="new-b",
        )

    initial = provider(refresh_models=refresh)
    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", initial)

    result = await registry.refresh("local")

    assert result.status == "published"
    assert observed_cached == [initial.models]
    effective = registry.effective("local")
    assert isinstance(effective.definition, DynamicProvider)  # type: ignore[union-attr]
    assert [model.id for model in effective.definition.models] == ["new-a", "new-b"]  # type: ignore[union-attr]
    assert effective.definition.default_model == "new-b"  # type: ignore[union-attr]


@pytest.mark.parametrize("short_first", [False, True])
async def test_coalesced_refresh_applies_each_waiters_timeout(short_first: bool) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def refresh(context):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return ProviderModelSnapshot((ProviderModel("fresh"),), "fresh")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))
    first_timeout = 0.02 if short_first else 1.0
    second_timeout = 1.0 if short_first else 0.02
    first = asyncio.create_task(registry.refresh("local", timeout_seconds=first_timeout))
    await entered.wait()
    second = asyncio.create_task(registry.refresh("local", timeout_seconds=second_timeout))
    short = first if short_first else second
    long = second if short_first else first

    short_result = await asyncio.wait_for(short, timeout=0.5)

    assert short_result.status == "timed_out"
    assert long.done() is False
    assert calls == 1
    release.set()
    long_result = await asyncio.wait_for(long, timeout=0.5)
    assert long_result.status == "published"


@pytest.mark.parametrize("network_first", [False, True])
async def test_incompatible_network_policies_never_coalesce(network_first: bool) -> None:
    entered = {False: asyncio.Event(), True: asyncio.Event()}
    release = {False: asyncio.Event(), True: asyncio.Event()}
    observed: list[bool] = []

    async def refresh(context):
        policy = context.allow_network
        observed.append(policy)
        entered[policy].set()
        await release[policy].wait()
        model = "network" if policy else "cached"
        return ProviderModelSnapshot((ProviderModel(model),), model)

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))
    first = asyncio.create_task(
        registry.refresh("local", allow_network=network_first, timeout_seconds=1.0)
    )
    await entered[network_first].wait()
    second = asyncio.create_task(
        registry.refresh("local", allow_network=not network_first, timeout_seconds=1.0)
    )
    await entered[not network_first].wait()

    assert observed == [network_first, not network_first]
    release[network_first].set()
    release[not network_first].set()
    results = await asyncio.gather(first, second)
    assert {result.status for result in results} <= {"published", "stale"}


async def test_concurrent_refresh_callers_share_one_task() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def refresh(context):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return ProviderModelSnapshot((ProviderModel("fresh"),), "fresh")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))
    first = asyncio.create_task(registry.refresh("local"))
    second = asyncio.create_task(registry.refresh("local"))
    await entered.wait()
    release.set()

    first_result, second_result = await asyncio.gather(first, second)
    assert calls == 1
    assert first_result == second_result
    assert first_result.status == "published"


@pytest.mark.parametrize("mode", ["malformed", "error", "timeout"])
async def test_failed_refresh_retains_snapshot_and_records_bounded_safe_diagnostic(mode) -> None:
    secret = "refresh-super-secret"

    async def refresh(context):
        if mode == "malformed":
            return ProviderModelSnapshot((ProviderModel("duplicate"), ProviderModel("duplicate")))
        if mode == "error":
            raise RuntimeError(secret)
        await asyncio.sleep(10)
        raise AssertionError("unreachable")

    initial = provider(refresh_models=refresh)
    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", initial)

    result = await registry.refresh("local", timeout_seconds=0.01)
    effective = registry.effective("local")

    assert result.status == ("timed_out" if mode == "timeout" else "failed")
    assert effective is not None
    assert effective.definition is initial
    assert len(registry.diagnostics) == 1
    assert secret not in repr(registry.diagnostics)

    # One diagnostic per layer generation, even after another failure.
    await registry.refresh("local", timeout_seconds=0.01)
    assert len(registry.diagnostics) == 1


async def test_refresh_diagnostics_have_a_deterministic_global_bound() -> None:
    async def refresh(context):
        raise RuntimeError("safe categorical diagnostic only")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    first_token = None
    for index in range(MAX_PROVIDER_REFRESH_DIAGNOSTICS + 1):
        token = registry.register(f"source-{index}", provider(refresh_models=refresh))
        if first_token is None:
            first_token = token
        result = await registry.refresh("local")
        assert result.status == "failed"

    assert len(registry.diagnostics) == MAX_PROVIDER_REFRESH_DIAGNOSTICS
    assert all(diagnostic.token != first_token for diagnostic in registry.diagnostics)


async def test_cancelling_one_waiter_does_not_cancel_shared_refresh() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def refresh(context):
        entered.set()
        await release.wait()
        return ProviderModelSnapshot((ProviderModel("fresh"),), "fresh")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))
    cancelled_waiter = asyncio.create_task(registry.refresh("local"))
    surviving_waiter = asyncio.create_task(registry.refresh("local"))
    await entered.wait()

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    release.set()

    result = await surviving_waiter
    assert result.status == "published"
    assert registry.diagnostics == ()


async def test_cancelled_refresh_does_not_publish() -> None:
    entered = asyncio.Event()

    async def refresh(context):
        entered.set()
        while not context.signal.is_cancelled():
            await asyncio.sleep(0)
        return ProviderModelSnapshot((ProviderModel("stale"),), "stale")

    initial = provider(refresh_models=refresh)
    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", initial)
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()

    assert registry.cancel_refresh("local", "source") is True
    result = await pending

    assert result.status == "cancelled"
    assert registry.effective("local").definition is initial  # type: ignore[union-attr]


async def test_timeout_then_immediate_retry_starts_fresh_and_publishes() -> None:
    calls = 0

    async def refresh(context):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(10)
        return ProviderModelSnapshot((ProviderModel("fresh"),), "fresh")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))

    timed_out = await registry.refresh("local", timeout_seconds=0.01)
    retried = await registry.refresh("local", timeout_seconds=1.0)

    assert timed_out.status == "timed_out"
    assert retried.status == "published"
    assert calls == 2
    assert registry.effective("local").definition.models[0].id == "fresh"  # type: ignore[union-attr]


async def test_cancel_then_immediate_retry_starts_fresh_and_publishes() -> None:
    entered = asyncio.Event()
    calls = 0

    async def refresh(context):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.sleep(10)
        return ProviderModelSnapshot((ProviderModel("fresh"),), "fresh")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))
    cancelled = asyncio.create_task(registry.refresh("local"))
    await entered.wait()

    assert registry.cancel_refresh("local", "source") is True
    retried = await registry.refresh("local", timeout_seconds=1.0)

    assert (await cancelled).status == "cancelled"
    assert retried.status == "published"
    assert calls == 2
    assert registry.effective("local").definition.models[0].id == "fresh"  # type: ignore[union-attr]


async def test_reregistration_prevents_old_refresh_publication() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def refresh(context):
        entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
        return ProviderModelSnapshot((ProviderModel("stale"),), "stale")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()
    replacement = provider(models=(ProviderModel("replacement"),), default_model="replacement")

    registry.register("source", replacement)
    result = await pending

    assert result.status == "cancelled"
    assert cancelled.is_set()
    assert registry.effective("local").definition is replacement  # type: ignore[union-attr]


async def test_cancellation_resistant_old_callback_cannot_publish_stale_work() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def refresh(context):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return ProviderModelSnapshot((ProviderModel("stale"),), "stale")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()
    replacement = provider(models=(ProviderModel("replacement"),), default_model="replacement")

    registry.register("source", replacement)
    result = await pending
    release.set()
    await asyncio.sleep(0)

    assert result.status == "cancelled"
    assert registry.effective("local").definition is replacement  # type: ignore[union-attr]


async def test_unregister_cancels_refresh_and_restores_preceding_layer() -> None:
    entered = asyncio.Event()

    async def refresh(context):
        entered.set()
        await asyncio.sleep(10)
        return ProviderModelSnapshot((ProviderModel("stale"),), "stale")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    preceding = provider(models=(ProviderModel("preceding"),), default_model="preceding")
    registry.register("source-a", preceding)
    registry.register("source-b", provider(refresh_models=refresh))
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()

    registry.unregister("local", "source-b")
    result = await pending

    assert result.status == "cancelled"
    assert registry.effective("local").definition is preceding  # type: ignore[union-attr]


async def test_retire_cancels_tasks_removes_layers_and_rejects_registration() -> None:
    entered = asyncio.Event()

    async def refresh(context):
        entered.set()
        await asyncio.sleep(10)
        return ProviderModelSnapshot((ProviderModel("stale"),), "stale")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()

    registry.retire()
    result = await pending

    assert result.status == "cancelled"
    assert registry.effective("local") is None
    with pytest.raises(RuntimeError, match="retired"):
        registry.register("source", provider())


async def test_close_waits_without_recancelling_long_finally_cleanup() -> None:
    entered = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_completed = asyncio.Event()
    cleanup_cancelled = False
    release_cleanup = asyncio.Event()

    async def refresh(context):
        nonlocal cleanup_cancelled
        entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            cleanup_started.set()
            try:
                await release_cleanup.wait()
            except asyncio.CancelledError:
                cleanup_cancelled = True
                raise
            cleanup_completed.set()

    runtime = ExtensionRuntime()
    registry = runtime.provider_registry
    registry.register("source", provider(refresh_models=refresh))
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()

    runtime.retire()
    await cleanup_started.wait()
    close = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0.15)

    assert PROVIDER_DISCOVERY_CANCELLATION_TIMEOUT_SECONDS > 0.1
    assert close.done() is False
    assert cleanup_cancelled is False
    assert cleanup_completed.is_set() is False
    release_cleanup.set()
    close_result = await asyncio.wait_for(close, timeout=0.5)
    assert close_result.drained is True
    assert close_result.contained_discovery_tasks == 0
    assert cleanup_completed.is_set()
    assert (await pending).status == "cancelled"
    assert not _live_discovery_tasks()


async def test_close_reports_blocked_finally_as_contained_without_recancelling() -> None:
    entered = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_cancelled = False

    async def refresh(context):
        nonlocal cleanup_cancelled
        entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            cleanup_started.set()
            try:
                await release_cleanup.wait()
            except asyncio.CancelledError:
                cleanup_cancelled = True
                raise

    runtime = ExtensionRuntime()
    registry = runtime.provider_registry
    registry.register("source", provider(refresh_models=refresh))
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()

    close_result = await asyncio.wait_for(runtime.aclose(), timeout=0.75)

    assert cleanup_started.is_set()
    assert cleanup_cancelled is False
    assert close_result.drained is False
    assert close_result.contained_discovery_tasks == 1
    discovery_task = next(iter(registry._discovery_tasks))  # type: ignore[attr-defined]
    assert discovery_task in _SUPERVISED_DISCOVERY_TASKS

    release_cleanup.set()
    await asyncio.wait_for(
        asyncio.gather(discovery_task, return_exceptions=True),
        timeout=0.5,
    )
    await asyncio.sleep(0)
    final_close_result = await runtime.aclose()
    assert final_close_result.drained is True
    assert discovery_task not in _SUPERVISED_DISCOVERY_TASKS
    assert (await pending).status == "cancelled"


async def test_replaced_cancellation_hostile_discovery_cannot_publish_when_it_finishes() -> None:
    entered = asyncio.Event()
    ignored_cancellation = asyncio.Event()
    release = asyncio.Event()

    async def refresh(context):
        while not release.is_set():
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                ignored_cancellation.set()
        return ProviderModelSnapshot((ProviderModel("stale"),), "stale")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("source", provider(refresh_models=refresh))
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()
    replacement = provider(models=(ProviderModel("replacement"),), default_model="replacement")

    registry.register("source", replacement)
    assert (await asyncio.wait_for(pending, timeout=0.5)).status == "cancelled"
    assert ignored_cancellation.is_set()
    assert len(registry._discovery_tasks) == 1  # type: ignore[attr-defined]

    release.set()
    await asyncio.wait_for(next(iter(registry._discovery_tasks)), timeout=0.5)  # type: ignore[attr-defined]
    await asyncio.sleep(0)
    assert registry.effective("local").definition is replacement  # type: ignore[union-attr]
    assert not registry._discovery_tasks  # type: ignore[attr-defined]


async def test_cancellation_hostile_discovery_is_boundedly_contained_and_owned() -> None:
    entered = asyncio.Event()
    suppressed = asyncio.Event()
    release = asyncio.Event()
    cancellation_count = 0

    async def refresh(context):
        nonlocal cancellation_count
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellation_count += 1
                suppressed.set()
        return ProviderModelSnapshot((ProviderModel("stale"),), "stale")

    runtime = ExtensionRuntime()
    registry = runtime.provider_registry
    registry.register("source", provider(refresh_models=refresh))
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()

    close_result = await asyncio.wait_for(runtime.aclose(), timeout=0.75)

    assert close_result.drained is False
    assert close_result.contained_discovery_tasks == 1
    assert suppressed.is_set()
    assert cancellation_count == 1
    assert (await pending).status == "cancelled"
    assert len(registry._discovery_tasks) == 1  # type: ignore[attr-defined]
    assert len(_live_discovery_tasks()) == 1
    discovery_task = next(iter(registry._discovery_tasks))  # type: ignore[attr-defined]
    assert discovery_task in _SUPERVISED_DISCOVERY_TASKS

    release.set()
    await asyncio.wait_for(discovery_task, timeout=0.5)
    await asyncio.sleep(0)
    final_close_result = await runtime.aclose()
    assert final_close_result.drained is True
    assert discovery_task not in _SUPERVISED_DISCOVERY_TASKS
    assert not registry._discovery_tasks  # type: ignore[attr-defined]
    assert registry.effective("local") is None


def _live_discovery_tasks() -> list[asyncio.Task[object]]:
    return [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name().startswith("tau-provider-discovery:")
    ]


async def test_reset_retains_outgoing_registry_for_later_async_drain() -> None:
    entered = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def refresh(context):
        entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            cleanup_started.set()
            await release_cleanup.wait()

    runtime = ExtensionRuntime()
    old_registry = runtime.provider_registry
    old_registry.register("source", provider(refresh_models=refresh))
    pending = asyncio.create_task(old_registry.refresh("local"))
    await entered.wait()

    runtime.reset_for_reload()
    await cleanup_started.wait()
    close = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0.15)

    assert close.done() is False
    release_cleanup.set()
    close_result = await close
    assert close_result.drained is True
    assert (await pending).status == "cancelled"
    assert not old_registry._discovery_tasks  # type: ignore[attr-defined]


async def test_runtime_close_drains_owned_refresh_and_is_idempotent() -> None:
    entered = asyncio.Event()

    async def refresh(context):
        entered.set()
        await asyncio.sleep(10)
        return ProviderModelSnapshot((ProviderModel("stale"),), "stale")

    runtime = ExtensionRuntime()
    registry = runtime.provider_registry
    registry.register("source", provider(refresh_models=refresh))
    pending = asyncio.create_task(registry.refresh("local"))
    await entered.wait()

    await runtime.aclose()
    result = await pending
    await runtime.aclose()

    assert result.status == "cancelled"
    assert runtime.active is False
    assert registry.effective("local") is None


async def test_setup_failure_removes_provider_layers(tmp_path: Path) -> None:
    paths = TauResourcePaths(
        root=tmp_path / "tau",
        cwd=tmp_path / "project",
        agents_root=tmp_path / "agents",
    )
    extension = tmp_path / "broken_provider.py"
    extension.write_text(
        """
from tau_coding.extensions import DynamicProvider, OpenAICompatibleTransport


def setup(tau):
    tau.register_provider(DynamicProvider(
        id="temporary",
        display_name="Temporary",
        transport=OpenAICompatibleTransport(base_url="http://example.test/v1"),
    ))
    raise RuntimeError("setup exploded")
""",
        encoding="utf-8",
    )
    runtime = ExtensionRuntime()

    runtime.load(paths, extra_paths=(extension,), include_resource_dirs=False)

    assert runtime.provider_registry.effective("temporary") is None
    assert any("setup failed" in diagnostic.message for diagnostic in runtime.diagnostics)


async def test_same_name_extension_paths_shadow_and_restore_exact_provider_layer(
    tmp_path: Path,
) -> None:
    paths = TauResourcePaths(
        root=tmp_path / "tau",
        cwd=tmp_path / "project",
        agents_root=tmp_path / "agents",
    )
    first_path = tmp_path / "first" / "shared.py"
    second_path = tmp_path / "second" / "shared.py"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_text(_provider_extension_body("first"), encoding="utf-8")
    second_path.write_text(_provider_extension_body("second"), encoding="utf-8")
    runtime = ExtensionRuntime()

    runtime.load(paths, extra_paths=(first_path,), include_resource_dirs=False)
    first_effective = runtime.provider_registry.effective("local")
    assert first_effective is not None
    first_definition = first_effective.definition

    runtime.load(paths, extra_paths=(second_path,), include_resource_dirs=False)
    layers = runtime.provider_registry.layers("local")

    assert [layer.provider.models[0].id for layer in layers] == ["first", "second"]
    assert layers[0].token.source_id == f"extension:{first_path.resolve().as_uri()}"
    assert layers[1].token.source_id == f"extension:{second_path.resolve().as_uri()}"
    assert layers[0].token.source_id != layers[1].token.source_id
    assert runtime.provider_registry.effective("local").definition is layers[1].provider  # type: ignore[union-attr]

    runtime.provider_registry.unregister_source(layers[1].token.source_id)

    assert runtime.provider_registry.effective("local").definition is first_definition  # type: ignore[union-attr]


async def test_same_name_failed_setup_preserves_first_exact_provider_layer(
    tmp_path: Path,
) -> None:
    paths = TauResourcePaths(
        root=tmp_path / "tau",
        cwd=tmp_path / "project",
        agents_root=tmp_path / "agents",
    )
    first_path = tmp_path / "first" / "shared.py"
    second_path = tmp_path / "second" / "shared.py"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_text(_provider_extension_body("first"), encoding="utf-8")
    second_path.write_text(
        _provider_extension_body("second", fail_setup=True),
        encoding="utf-8",
    )
    runtime = ExtensionRuntime()

    runtime.load(paths, extra_paths=(first_path,), include_resource_dirs=False)
    first_effective = runtime.provider_registry.effective("local")
    assert first_effective is not None
    first_definition = first_effective.definition

    runtime.load(paths, extra_paths=(second_path,), include_resource_dirs=False)

    assert runtime.extension_names == ("shared",)
    assert runtime.provider_registry.effective("local").definition is first_definition  # type: ignore[union-attr]
    assert len(runtime.provider_registry.layers("local")) == 1
    assert any("setup failed" in diagnostic.message for diagnostic in runtime.diagnostics)


async def test_reloading_same_exact_extension_source_keeps_first_registration(
    tmp_path: Path,
) -> None:
    paths = TauResourcePaths(
        root=tmp_path / "tau",
        cwd=tmp_path / "project",
        agents_root=tmp_path / "agents",
    )
    extension_path = tmp_path / "shared.py"
    extension_path.write_text(_provider_extension_body("first"), encoding="utf-8")
    runtime = ExtensionRuntime()

    runtime.load(paths, extra_paths=(extension_path,), include_resource_dirs=False)
    first_effective = runtime.provider_registry.effective("local")
    assert first_effective is not None
    first_definition = first_effective.definition

    extension_path.write_text(
        _provider_extension_body("second", fail_setup=True),
        encoding="utf-8",
    )
    runtime.load(paths, extra_paths=(extension_path,), include_resource_dirs=False)

    assert runtime.extension_names == ("shared",)
    assert runtime.provider_registry.effective("local").definition is first_definition  # type: ignore[union-attr]
    assert len(runtime.provider_registry.layers("local")) == 1
    assert any(
        "duplicate extension source ignored" in diagnostic.message
        for diagnostic in runtime.diagnostics
    )


async def test_extension_api_registers_provider_and_reload_retires_generation(
    tmp_path: Path,
) -> None:
    paths = TauResourcePaths(
        root=tmp_path / "tau",
        cwd=tmp_path / "project",
        agents_root=tmp_path / "agents",
    )
    extension = tmp_path / "dynamic_provider.py"
    extension.write_text(
        """
from tau_coding.extensions import DynamicProvider, OpenAICompatibleTransport


def setup(tau):
    tau.register_provider(DynamicProvider(
        id="dynamic",
        display_name="Dynamic",
        transport=OpenAICompatibleTransport(base_url="http://example.test/v1"),
    ))
""",
        encoding="utf-8",
    )
    runtime = ExtensionRuntime()
    runtime.load(paths, extra_paths=(extension,), include_resource_dirs=False)
    old_registry = runtime.provider_registry
    old_generation = old_registry.generation_id

    assert old_registry.effective("dynamic") is not None
    runtime.reset_for_reload()

    assert old_registry.effective("dynamic") is None
    assert runtime.provider_registry.generation_id != old_generation
    assert runtime.provider_registry.effective("dynamic") is None


async def test_frozen_compat_is_copied_to_mutable_json_at_runtime_boundary() -> None:
    model = ProviderModel(
        "model",
        compat={"nested": {"items": [{"value": "original"}]}},
    )
    dynamic = provider(models=(model,))

    runtime = await create_dynamic_model_provider(
        dynamic,
        model="model",
        credential_store=MemoryCredentials(),  # type: ignore[arg-type]
        environment={},
    )
    runtime_compat = runtime._config.compat  # type: ignore[attr-defined]
    nested = cast(dict[str, object], runtime_compat["nested"])
    items = cast(list[dict[str, str]], nested["items"])
    items[0]["value"] = "runtime-only"

    assert items[0]["value"] == "runtime-only"
    _assert_nested_compat_is_frozen(model, "original")
    await runtime.aclose()


async def test_dynamic_provider_operations_never_call_durable_write_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_write(*args, **kwargs):
        raise AssertionError(f"durable provider write attempted: {args!r} {kwargs!r}")

    monkeypatch.setattr("tau_coding.provider_config._write_provider_settings", reject_write)
    monkeypatch.setattr("tau_coding.provider_config.save_user_catalog_entries", reject_write)

    async def refresh(context):
        return ProviderModelSnapshot((ProviderModel("fresh"),), "fresh")

    registry = DynamicProviderRegistry(generation_id="generation-1")
    registry.register("extension", provider(refresh_models=refresh))
    result = await registry.refresh("local")
    assert result.status == "published"
    assert result.provider is not None

    runtime = await create_dynamic_model_provider(
        result.provider,
        model="fresh",
        credential_store=MemoryCredentials(),  # type: ignore[arg-type]
        environment={},
    )
    await runtime.aclose()
    registry.unregister_source("extension")


async def test_dynamic_transport_merges_headers_and_supports_custom_auth() -> None:
    auth = FixedAuth(
        ResolvedProviderAuth(
            headers={"Authorization": "Token runtime-secret", "X-Trace": "auth"},
            source="custom auth",
        )
    )
    dynamic = DynamicProvider(
        id="local",
        display_name="Local",
        models=(ProviderModel("model", headers={"x-trace": "model"}),),
        default_model="model",
        transport=OpenAICompatibleTransport(
            base_url="http://127.0.0.1:8080/v1",
            auth=auth,
            headers={"X-TRACE": "transport"},
        ),
    )
    runtime = await create_dynamic_model_provider(
        dynamic,
        model="model",
        credential_store=MemoryCredentials(),  # type: ignore[arg-type]
        environment={},
    )
    observed: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        return httpx2.Response(
            200,
            text='data: {"choices":[{"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    runtime._client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))  # type: ignore[attr-defined]
    runtime._owns_client = True  # type: ignore[attr-defined]
    _ = [
        event
        async for event in runtime.stream_response(
            model="model",
            system="system",
            messages=[UserMessage(content="hello")],
            tools=[],
        )
    ]

    assert observed[0].headers["authorization"] == "Token runtime-secret"
    assert observed[0].headers["x-trace"] == "auth"
    assert observed[0].headers.get_list("x-trace") == ["auth"]
    await runtime.aclose()


async def test_dynamic_openai_transport_rejects_key_with_omitted_authorization() -> None:
    dynamic = provider(
        auth=FixedAuth(
            ResolvedProviderAuth(
                api_key="runtime-secret",
                source="custom auth",
                omit_authorization_header=True,
            )
        )
    )

    with pytest.raises(ProviderConfigError, match="requesting Authorization omission"):
        await create_dynamic_model_provider(
            dynamic,
            model="model",
            credential_store=MemoryCredentials(),  # type: ignore[arg-type]
            environment={},
        )


@pytest.mark.parametrize("with_key", [False, True])
async def test_dynamic_openai_transport_emits_conditional_authorization(with_key: bool) -> None:
    key = "runtime-super-secret" if with_key else None
    credentials = MemoryCredentials({"local:key": key} if key else {})
    dynamic = provider(auth=OptionalApiKey("local:key", "LOCAL_API_KEY"))
    runtime = await create_dynamic_model_provider(
        dynamic,
        model="model",
        credential_store=credentials,  # type: ignore[arg-type]
        environment={},
    )
    assert isinstance(runtime, OpenAICompatibleProvider)
    observed: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        return httpx2.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            'data: {"choices":[{"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    runtime._client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    runtime._owns_client = True
    events = [
        event
        async for event in runtime.stream_response(
            model="model",
            system="system",
            messages=[UserMessage(content="hello")],
            tools=[],
        )
    ]

    assert events
    assert len(observed) == 1
    assert observed[0].headers.get("authorization") == (f"Bearer {key}" if with_key else None)
    await runtime.aclose()


@pytest.mark.parametrize("model_id", ["gpt-5.4-local", "my-codex-local"])
async def test_dynamic_transport_does_not_infer_endpoint_from_model_name(model_id: str) -> None:
    dynamic = provider(models=(ProviderModel(model_id),), default_model=model_id)
    runtime = await create_dynamic_model_provider(
        dynamic,
        model=model_id,
        credential_store=MemoryCredentials(),  # type: ignore[arg-type]
        environment={},
    )
    assert isinstance(runtime, OpenAICompatibleProvider)
    urls: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        urls.append(str(request.url))
        return httpx2.Response(
            200,
            text='data: {"choices":[{"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    runtime._client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    runtime._owns_client = True
    _ = [
        event
        async for event in runtime.stream_response(
            model=model_id,
            system="system",
            messages=[UserMessage(content="hello")],
            tools=[],
        )
    ]

    assert urls == ["http://127.0.0.1:8080/v1/chat/completions"]
    await runtime.aclose()
