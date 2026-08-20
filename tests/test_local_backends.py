"""Deterministic provider-neutral local-backend contract tests."""

import asyncio

import pytest

from tau_coding.extensions import (
    DynamicProvider,
    DynamicProviderRegistry,
    LocalBackend,
    LocalBackendError,
    LocalBackendRegistry,
    LocalBackendStatus,
    LocalConfigField,
    LocalConfigureResult,
    LocalConfigureSpec,
    LocalDiagnostic,
    LocalModel,
    LocalOperationResult,
    LocalProgress,
    NoAuth,
    OpenAICompatibleTransport,
    ProviderModel,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _provider(provider_id: str) -> DynamicProvider:
    return DynamicProvider(
        id=provider_id,
        display_name=provider_id.title(),
        models=(ProviderModel("model"),),
        default_model="model",
        transport=OpenAICompatibleTransport(
            base_url="http://example.test/v1",
            auth=NoAuth(),
        ),
    )


def _backend(
    backend_id: str,
    provider_id: str,
    spec: LocalConfigureSpec,
    *,
    configure,
    refresh=None,
    doctor=None,
    reset=None,
    recommended: bool = False,
) -> LocalBackend:
    async def status(context):
        del context
        return LocalBackendStatus(
            state="ready",
            endpoint_display=backend_id,
            models=(LocalModel("model"),),
            selected_model="model",
            actions=("refresh", "use", "configure"),
        )

    return LocalBackend(
        id=backend_id,
        provider_id=provider_id,
        display_name=backend_id.title(),
        configure_spec=spec,
        configure=configure,
        status=status,
        refresh=refresh or status,
        doctor=doctor,
        reset=reset,
        recommended=recommended,
    )


async def test_two_backends_have_different_structured_configuration_fields() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("source-a", _provider("provider-a"))
    providers.register("source-b", _provider("provider-b"))
    received: list[tuple[str, object]] = []

    async def configure_a(values, context):
        received.append(("a", values))
        assert context.action == "configure"
        assert values["endpoint"] == "http://a.test"
        assert values["token"] == "secret-a"
        assert values["profile"] == "fast"
        return LocalConfigureResult(committed=True)

    async def configure_b(values, context):
        received.append(("b", values))
        assert context.action == "configure"
        assert values["directory"] == "/models"
        assert values["password"] == "secret-b"
        return LocalConfigureResult(committed=True)

    backend_a = _backend(
        "a",
        "provider-a",
        LocalConfigureSpec(
            (
                LocalConfigField("endpoint", "Endpoint", "text", required=True),
                LocalConfigField("token", "Token", "secret"),
                LocalConfigField("profile", "Profile", "choice", choices=("fast", "safe")),
            )
        ),
        configure=configure_a,
        recommended=True,
    )
    backend_b = _backend(
        "b",
        "provider-b",
        LocalConfigureSpec(
            (
                LocalConfigField("directory", "Directory", "text", required=True),
                LocalConfigField("password", "Password", "secret"),
            )
        ),
        configure=configure_b,
    )
    registry.register("source-a", backend_a)
    registry.register("source-b", backend_b)

    views = registry.effective_backends()
    assert [view.backend.id for view in views] == ["a", "b"]
    assert views[0].recommended is True
    assert views[1].recommended is False

    configured_a = await registry.configure(
        "a",
        {"endpoint": "http://a.test", "token": "secret-a", "profile": "fast"},
    )
    configured_b = await registry.configure(
        "b",
        {"directory": "/models", "password": "secret-b"},
    )
    assert configured_a.committed is True
    assert configured_b.committed is True
    assert received[0][0] == "a"
    assert received[1][0] == "b"
    assert "secret-a" not in repr(received[0][1])
    assert "secret-b" not in repr(received[1][1])


async def test_configuration_validation_is_transactional_and_secret_safe() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("source", _provider("provider"))
    committed: list[object] = []

    async def configure(values, context):
        del context
        committed.append(values)
        return LocalConfigureResult(committed=True)

    backend = _backend(
        "backend",
        "provider",
        LocalConfigureSpec(
            (
                LocalConfigField("url", "URL", "text", required=True),
                LocalConfigField("key", "Key", "secret", required=True),
                LocalConfigField("mode", "Mode", "choice", choices=("one", "two")),
            )
        ),
        configure=configure,
    )
    registry.register("source", backend)

    invalid = await registry.configure("backend", {"url": "", "key": "secret", "mode": "bad"})
    assert invalid.committed is False
    assert set(invalid.field_errors) == {"url", "mode"}
    assert committed == []
    assert "secret" not in repr(invalid)

    valid = await registry.configure(
        "backend", {"url": "http://example", "key": "secret", "mode": "one"}
    )
    assert valid.committed is True
    assert len(committed) == 1


async def test_failed_write_reports_orphan_without_partial_host_commit() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("source", _provider("provider"))
    state = {"value": "old"}

    async def configure(values, context):
        del values, context
        # The backend owns its cross-store write protocol. It reports that the
        # new credential may need cleanup, but does not claim a commit.
        return LocalConfigureResult(
            message="safe state write failed",
            credential_orphaned=True,
        )

    backend = _backend(
        "backend",
        "provider",
        LocalConfigureSpec((LocalConfigField("value", "Value", "text"),)),
        configure=configure,
    )
    registry.register("source", backend)

    result = await registry.configure("backend", {"value": "new"})
    assert result.committed is False
    assert result.credential_orphaned is True
    assert state["value"] == "old"


async def test_cancelled_operation_does_not_publish_result() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("source", _provider("provider"))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def refresh(context):
        entered.set()
        while not context.cancelled:
            await asyncio.sleep(0)
        release.set()
        return LocalBackendStatus(
            state="ready",
            models=(LocalModel("stale"),),
            selected_model="stale",
        )

    backend = _backend(
        "backend",
        "provider",
        LocalConfigureSpec(),
        configure=lambda values, context: LocalConfigureResult(),
        refresh=refresh,
    )
    registry.register("source", backend)
    pending = asyncio.create_task(registry.refresh("backend"))
    await entered.wait()
    assert registry.cancel("backend", "refresh") is True
    result = await pending
    assert result.cancelled is True
    assert result.backend_status is None
    assert release.is_set() is False
    await registry.aclose()


async def test_replaced_source_is_stale_and_shadowed_source_cannot_reset_effective_layer() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("first", _provider("provider"))
    providers.register("second", _provider("provider"))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def refresh(context):
        entered.set()
        await release.wait()
        return LocalBackendStatus(state="ready", models=(LocalModel("old"),))

    async def configure(values, context):
        del values, context
        return LocalConfigureResult(committed=True)

    first = _backend(
        "shared",
        "provider",
        LocalConfigureSpec(),
        configure=configure,
        refresh=refresh,
        reset=refresh,
    )
    second = _backend(
        "shared",
        "provider",
        LocalConfigureSpec(),
        configure=configure,
    )
    # The second provider source shadows the first provider layer, so the
    # first backend remains inspectable but cannot be the effective use/reset.
    registry.register("first", first)
    pending = asyncio.create_task(registry.refresh("shared"))
    await entered.wait()
    registry.register("first", first)
    release.set()
    stale = await pending
    assert stale.stale is True or stale.cancelled is True
    registry.register("second", second)
    all_views = registry.all_views("shared")
    assert len(all_views) == 2
    assert registry.effective("shared").token.source_id == "second"  # type: ignore[union-attr]
    assert all_views[0].use_available is False
    assert registry.unregister("shared", "first") is True
    assert registry.effective("shared").token.source_id == "second"  # type: ignore[union-attr]
    await registry.aclose()


async def test_missing_provider_pair_is_rejected() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    backend = _backend(
        "backend",
        "missing",
        LocalConfigureSpec(),
        configure=lambda values, context: LocalConfigureResult(),
    )
    with pytest.raises(LocalBackendError, match="paired"):
        registry.register("source", backend)


async def test_optional_secret_may_be_omitted_without_invalidating_submission() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("source", _provider("provider"))
    received: list[object] = []

    async def configure(values, context):
        del context
        received.append(values)
        return LocalConfigureResult(committed=True)

    registry.register(
        "source",
        _backend(
            "backend",
            "provider",
            LocalConfigureSpec((LocalConfigField("optional-key", "Key", "secret"),)),
            configure=configure,
        ),
    )

    result = await registry.configure("backend", {})
    assert result.committed is True
    assert received[0].secret_keys == frozenset()  # type: ignore[union-attr]
    await registry.aclose()


async def test_progress_and_transaction_results_redact_submitted_secrets() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("source", _provider("provider"))

    async def configure(values, context):
        context.report_progress(LocalProgress("upload secret-token"))
        return LocalConfigureResult(
            committed=True,
            message="saved secret-token",
            diagnostics=(LocalDiagnostic("diagnostic secret-token", "error"),),
            field_errors={"key": "invalid secret-token"},
        )

    registry.register(
        "source",
        _backend(
            "backend",
            "provider",
            LocalConfigureSpec((LocalConfigField("key", "Key", "secret"),)),
            configure=configure,
        ),
    )

    result = await registry.configure("backend", {"key": "secret-token"})
    assert result.committed is False
    assert result.message == "saved [redacted]"
    assert "secret-token" not in repr(result)
    assert all("secret-token" not in item.message for item in result.progress)
    await registry.aclose()


async def test_registry_rejects_mismatched_provider_generation() -> None:
    providers = DynamicProviderRegistry(generation_id="provider-generation")
    with pytest.raises(LocalBackendError, match="share a generation"):
        LocalBackendRegistry(providers, generation_id="other-generation")


async def test_shadowed_source_cannot_reset_or_mutate_backend_state() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("first", _provider("provider"))
    providers.register("second", _provider("provider"))
    reset_called = False

    async def reset(context):
        nonlocal reset_called
        del context
        reset_called = True
        return LocalOperationResult(committed=True)

    registry.register(
        "first",
        _backend(
            "backend",
            "provider",
            LocalConfigureSpec(),
            configure=lambda values, context: LocalConfigureResult(),
            reset=reset,
        ),
    )
    result = await registry.reset("backend")
    assert result.committed is False
    assert result.diagnostics[0].severity == "warning"
    assert reset_called is False
    await registry.aclose()


async def test_optional_capabilities_return_structured_unavailable_results() -> None:
    providers = DynamicProviderRegistry(generation_id="generation")
    registry = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("source", _provider("provider"))
    backend = _backend(
        "backend",
        "provider",
        LocalConfigureSpec(),
        configure=lambda values, context: LocalConfigureResult(),
    )
    registry.register("source", backend)
    result = await registry.doctor("backend")
    assert result.diagnostics == (LocalDiagnostic("This action is unavailable.", "warning"),)
    assert registry.effective("backend").backend.doctor is None  # type: ignore[union-attr]
    await registry.aclose()
