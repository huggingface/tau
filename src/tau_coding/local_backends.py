"""Provider-neutral local-backend contracts and generation-local registry.

Backends describe protocol work and structured data.  They never construct UI
widgets or receive a frontend object.  The registry owns source/generation
identity, cancellation, and stale-result containment; the host owns rendering
and session/model selection.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol

from tau_agent.harness import SimpleCancellationToken
from tau_coding.extensions.provider_registry import (
    DynamicProviderRegistry,
    ProviderLayerToken,
)

LocalFieldKind = Literal["text", "secret", "choice"]
LocalConnectionState = Literal[
    "unconfigured",
    "connecting",
    "loading",
    "ready",
    "unavailable",
    "stale",
    "error",
    "cancelled",
]
LocalAction = Literal[
    "configure",
    "refresh",
    "use",
    "doctor",
    "reset",
    "load_model",
    "unload_model",
    "download_model",
]
LocalDiagnosticSeverity = Literal["info", "warning", "error"]


class LocalBackendError(ValueError):
    """Raised when a backend contract or registration is invalid."""


class LocalOperationError(RuntimeError):
    """Raised for host-owned local operation failures."""


@dataclass(frozen=True, slots=True)
class LocalConfigField:
    """One host-rendered configuration field.

    ``secret`` values are accepted only through :class:`LocalConfigValues` and
    are deliberately excluded from reprs and diagnostics by the host.
    """

    key: str
    label: str
    kind: LocalFieldKind
    required: bool = False
    placeholder: str | None = None
    choices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.key, "Configuration field key")
        _non_empty(self.label, "Configuration field label")
        if self.kind not in {"text", "secret", "choice"}:
            raise LocalBackendError("Configuration field kind is unsupported")
        if not isinstance(self.required, bool):
            raise LocalBackendError("Configuration field required flag must be boolean")
        if self.placeholder is not None and not isinstance(self.placeholder, str):
            raise LocalBackendError("Configuration field placeholder must be a string or None")
        if not isinstance(self.choices, tuple) or any(
            not isinstance(choice, str) or not choice for choice in self.choices
        ):
            raise LocalBackendError("Configuration choices must be non-empty strings")
        if self.kind == "choice" and not self.choices:
            raise LocalBackendError("Choice configuration fields must define choices")
        if self.key != self.key.strip():
            raise LocalBackendError("Configuration field key must not have surrounding whitespace")
        if self.kind != "choice" and self.choices:
            raise LocalBackendError("Only choice configuration fields may define choices")
        if len(set(self.choices)) != len(self.choices):
            raise LocalBackendError("Configuration choices must be unique")


@dataclass(frozen=True, slots=True)
class LocalConfigureSpec:
    """Complete configuration schema rendered by the host."""

    fields: tuple[LocalConfigField, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fields, tuple) or any(
            not isinstance(field, LocalConfigField) for field in self.fields
        ):
            raise LocalBackendError("Configuration fields must be LocalConfigField values")
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise LocalBackendError("Configuration field keys must be unique")


ReadConfigureSpec = LocalConfigureSpec | Callable[[], LocalConfigureSpec]


@dataclass(frozen=True, slots=True)
class LocalConfigValues(Mapping[str, str]):
    """Ephemeral submitted configuration values.

    The mapping remains usable by backend code, but its values never appear in
    reprs.  Backends should not retain it after their transactional configure
    callback returns.
    """

    _values: Mapping[str, str] = field(repr=False)
    secret_keys: frozenset[str] = field(default_factory=frozenset, repr=False)

    def __post_init__(self) -> None:
        copied: dict[str, str] = {}
        for key, value in self._values.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise LocalBackendError("Configuration values must map strings to strings")
            copied[key] = value
        if not self.secret_keys.issubset(copied):
            raise LocalBackendError("Secret configuration keys must be submitted values")
        object.__setattr__(self, "_values", MappingProxyType(copied))
        object.__setattr__(self, "secret_keys", frozenset(self.secret_keys))

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"LocalConfigValues(keys={tuple(self._values)!r})"


@dataclass(frozen=True, slots=True)
class LocalModel:
    """Safe model information returned by a local backend."""

    id: str
    display_name: str | None = None
    state: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.id, "Local model id")
        if self.display_name is not None:
            _non_empty(self.display_name, "Local model display name")
        if self.state is not None:
            _non_empty(self.state, "Local model state")


@dataclass(frozen=True, slots=True)
class LocalDiagnostic:
    """Secret-free diagnostic suitable for host rendering."""

    message: str
    severity: LocalDiagnosticSeverity = "info"
    stage: str | None = None

    def __post_init__(self) -> None:
        _non_empty(self.message, "Diagnostic message")
        if self.severity not in {"info", "warning", "error"}:
            raise LocalBackendError("Diagnostic severity is unsupported")
        if self.stage is not None:
            _non_empty(self.stage, "Diagnostic stage")


@dataclass(frozen=True, slots=True)
class LocalProgress:
    """Bounded progress update emitted by a backend operation."""

    message: str
    fraction: float | None = None
    done: bool = False

    def __post_init__(self) -> None:
        _non_empty(self.message, "Progress message")
        if self.fraction is not None and not 0 <= self.fraction <= 1:
            raise LocalBackendError("Progress fraction must be between 0 and 1")
        if not isinstance(self.done, bool):
            raise LocalBackendError("Progress done flag must be boolean")


@dataclass(frozen=True, slots=True)
class LocalBackendStatus:
    """Structured status snapshot; no backend-specific protocol vocabulary."""

    state: LocalConnectionState
    endpoint_display: str | None = None
    authentication_source: Literal["none", "environment", "stored credential"] = "none"
    models: tuple[LocalModel, ...] = ()
    selected_model: str | None = None
    actions: tuple[LocalAction, ...] = ()
    diagnostics: tuple[LocalDiagnostic, ...] = ()
    cached: bool = False
    stale: bool = False

    def __post_init__(self) -> None:
        if self.state not in {
            "unconfigured",
            "connecting",
            "loading",
            "ready",
            "unavailable",
            "stale",
            "error",
            "cancelled",
        }:
            raise LocalBackendError("Local connection state is unsupported")
        if self.endpoint_display is not None:
            _non_empty(self.endpoint_display, "Endpoint display value")
        if self.authentication_source not in {"none", "environment", "stored credential"}:
            raise LocalBackendError("Authentication source is unsupported")
        if not isinstance(self.models, tuple) or any(
            not isinstance(model, LocalModel) for model in self.models
        ):
            raise LocalBackendError("Local models must be LocalModel values")
        model_ids = [model.id for model in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise LocalBackendError("Local model ids must be unique")
        if self.selected_model is not None and self.selected_model not in model_ids:
            raise LocalBackendError("Selected model must belong to the status snapshot")
        if not isinstance(self.actions, tuple) or any(
            action
            not in {
                "configure",
                "refresh",
                "use",
                "doctor",
                "reset",
                "load_model",
                "unload_model",
                "download_model",
            }
            for action in self.actions
        ):
            raise LocalBackendError("Local actions are unsupported")
        if len(set(self.actions)) != len(self.actions):
            raise LocalBackendError("Local actions must be unique")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LocalDiagnostic) for item in self.diagnostics
        ):
            raise LocalBackendError("Local diagnostics must be structured values")


@dataclass(frozen=True, slots=True)
class LocalOperationContext:
    """Inputs owned by one backend operation."""

    signal: SimpleCancellationToken
    action: LocalAction
    generation_id: str
    backend_id: str
    source_id: str
    _is_current: Callable[[], bool] = field(repr=False)
    _progress: Callable[[LocalProgress], None] = field(repr=False)

    @property
    def cancelled(self) -> bool:
        return self.signal.is_cancelled()

    @property
    def stale(self) -> bool:
        return not self._is_current()

    def report_progress(self, progress: LocalProgress) -> None:
        """Publish one structured progress event to the host."""
        if not self.cancelled and not self.stale:
            self._progress(progress)


@dataclass(frozen=True, slots=True)
class LocalOperationResult:
    """Common host-renderable operation result."""

    backend_status: LocalBackendStatus | None = None
    message: str | None = None
    diagnostics: tuple[LocalDiagnostic, ...] = ()
    progress: tuple[LocalProgress, ...] = ()
    cancelled: bool = False
    stale: bool = False
    committed: bool = False
    field_errors: Mapping[str, str] = field(default_factory=dict)
    credential_orphaned: bool = False

    def __post_init__(self) -> None:
        if self.message is not None and not isinstance(self.message, str):
            raise LocalBackendError("Operation message must be a string or None")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LocalDiagnostic) for item in self.diagnostics
        ):
            raise LocalBackendError("Operation diagnostics must be structured values")
        if not isinstance(self.progress, tuple) or any(
            not isinstance(item, LocalProgress) for item in self.progress
        ):
            raise LocalBackendError("Operation progress must be structured values")
        if not isinstance(self.field_errors, Mapping):
            raise LocalBackendError("Configuration field errors must be a mapping")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.field_errors.items()
        ):
            raise LocalBackendError("Configuration field errors must contain strings")


@dataclass(frozen=True, slots=True)
class LocalConfigureResult:
    """Backend result for a transactional configuration attempt."""

    committed: bool = False
    backend_status: LocalBackendStatus | None = None
    message: str | None = None
    field_errors: Mapping[str, str] = field(default_factory=dict)
    diagnostics: tuple[LocalDiagnostic, ...] = ()
    credential_orphaned: bool = False


@dataclass(frozen=True, slots=True)
class LocalBackendLayerToken:
    backend_id: str
    source_id: str
    generation_id: str
    layer_id: str
    provider_layer: ProviderLayerToken


@dataclass(frozen=True, slots=True)
class LocalBackendView:
    """A backend plus effective-source information for host rendering."""

    backend: LocalBackend
    token: LocalBackendLayerToken
    recommended: bool = False
    provider_layer_effective: bool = True

    @property
    def use_available(self) -> bool:
        return self.provider_layer_effective


class ProgressCallback(Protocol):
    def __call__(self, progress: LocalProgress) -> None: ...


ConfigureLocalBackend = Callable[
    [LocalConfigValues, LocalOperationContext],
    LocalConfigureResult
    | LocalOperationResult
    | Awaitable[LocalConfigureResult | LocalOperationResult],
]
ReadLocalBackendStatus = Callable[
    [LocalOperationContext],
    LocalBackendStatus
    | LocalOperationResult
    | Awaitable[LocalBackendStatus | LocalOperationResult],
]
RefreshLocalBackend = ReadLocalBackendStatus
DoctorLocalBackend = ReadLocalBackendStatus
ResetLocalBackend = ReadLocalBackendStatus
ManageModel = Callable[
    [str, LocalOperationContext],
    LocalOperationResult | Awaitable[LocalOperationResult],
]
DownloadModel = ManageModel


@dataclass(frozen=True, slots=True)
class LocalBackend:
    """Provider-neutral local backend capability declaration."""

    id: str
    provider_id: str
    display_name: str
    configure_spec: ReadConfigureSpec
    configure: ConfigureLocalBackend
    status: ReadLocalBackendStatus
    refresh: RefreshLocalBackend
    doctor: DoctorLocalBackend | None = None
    reset: ResetLocalBackend | None = None
    load_model: ManageModel | None = None
    unload_model: ManageModel | None = None
    download_model: DownloadModel | None = None
    recommended: bool = False

    def __post_init__(self) -> None:
        _identifier(self.id, "Local backend id")
        _identifier(self.provider_id, "Local backend provider id")
        _non_empty(self.display_name, "Local backend display name")
        if not callable(self.configure) or not callable(self.status) or not callable(self.refresh):
            raise LocalBackendError("Local backend configure, status, and refresh must be callable")
        if not callable(self.configure_spec) and not isinstance(
            self.configure_spec, LocalConfigureSpec
        ):
            raise LocalBackendError("Local backend configure_spec is unsupported")
        if not isinstance(self.recommended, bool):
            raise LocalBackendError("Local backend recommended flag must be boolean")
        for callback in (
            self.doctor,
            self.reset,
            self.load_model,
            self.unload_model,
            self.download_model,
        ):
            if callback is not None and not callable(callback):
                raise LocalBackendError("Local backend capability must be callable")

    def read_configure_spec(self) -> LocalConfigureSpec:
        value = self.configure_spec() if callable(self.configure_spec) else self.configure_spec
        if not isinstance(value, LocalConfigureSpec):
            raise LocalBackendError("Local backend configure_spec returned an unsupported value")
        return value


@dataclass(eq=False, slots=True)
class _Operation:
    signal: SimpleCancellationToken
    task: asyncio.Task[LocalOperationResult]
    progress: list[LocalProgress]


class LocalBackendRegistry:
    """Generation-local, source-bound local backend registry."""

    def __init__(
        self,
        provider_registry: DynamicProviderRegistry,
        *,
        generation_id: str | None = None,
        recommended_backend_id: str | None = None,
    ) -> None:
        self._providers = provider_registry
        self._generation_id = generation_id or provider_registry.generation_id
        if self._generation_id != provider_registry.generation_id:
            raise LocalBackendError(
                "Local backend registry and provider registry must share a generation"
            )
        self._recommended_backend_id = recommended_backend_id
        self._layers: dict[str, list[tuple[LocalBackendLayerToken, LocalBackend, int]]] = {}
        self._sequence = 0
        self._operations: dict[tuple[LocalBackendLayerToken, LocalAction], _Operation] = {}
        self._retired = False

    @property
    def generation_id(self) -> str:
        return self._generation_id

    def register(self, source_id: str, backend: LocalBackend) -> LocalBackendLayerToken:
        """Register a backend only against its exact source provider layer."""
        self._assert_active()
        source = _source_id(source_id)
        provider_layer = self._providers.layer_token(backend.provider_id, source)
        if provider_layer is None:
            raise LocalBackendError(
                f"Local backend {backend.id} must be paired with provider {backend.provider_id} "
                "registered by the same source"
            )
        self._sequence += 1
        token = LocalBackendLayerToken(
            backend_id=backend.id,
            source_id=source,
            generation_id=self._generation_id,
            layer_id=f"{self._generation_id}:{self._sequence}",
            provider_layer=provider_layer,
        )
        current = self._layers.get(backend.id, [])
        replaced = [item for item in current if item[0].source_id == source]
        self._layers[backend.id] = [item for item in current if item[0].source_id != source] + [
            (token, backend, self._sequence)
        ]
        for old_token, _, _ in replaced:
            self._cancel_token(old_token)
        return token

    def unregister(self, backend_id: str, source_id: str) -> bool:
        """Remove one exact source layer and preserve preceding layers."""
        source = _source_id(source_id)
        current = self._layers.get(backend_id, [])
        removed = [item for item in current if item[0].source_id == source]
        if not removed:
            return False
        remaining = [item for item in current if item[0].source_id != source]
        if remaining:
            self._layers[backend_id] = remaining
        else:
            self._layers.pop(backend_id, None)
        for token, _, _ in removed:
            self._cancel_token(token)
        return True

    def unregister_source(self, source_id: str) -> None:
        source = _source_id(source_id)
        for backend_id in tuple(self._layers):
            self.unregister(backend_id, source)

    def layers(self, backend_id: str) -> tuple[LocalBackendLayerToken, ...]:
        return tuple(token for token, _, _ in self._layers.get(backend_id, ()))

    def effective(self, backend_id: str) -> LocalBackendView | None:
        current = self._layers.get(backend_id)
        if not current:
            return None
        token, backend, _ = current[-1]
        provider = self._providers.layer_token(backend.provider_id, token.source_id)
        effective_provider = self._providers.effective(backend.provider_id)
        provider_effective = (
            provider == token.provider_layer
            and effective_provider is not None
            and effective_provider.layer_token == provider
        )
        return LocalBackendView(
            backend=backend,
            token=token,
            recommended=backend.recommended or backend.id == self._recommended_backend_id,
            provider_layer_effective=provider_effective,
        )

    def all_views(self, backend_id: str) -> tuple[LocalBackendView, ...]:
        """Return every source layer, including shadowed backend layers."""
        views: list[LocalBackendView] = []
        for token, backend, _ in self._layers.get(backend_id, ()):
            provider = self._providers.layer_token(backend.provider_id, token.source_id)
            effective_provider = self._providers.effective(backend.provider_id)
            views.append(
                LocalBackendView(
                    backend=backend,
                    token=token,
                    recommended=backend.recommended or backend.id == self._recommended_backend_id,
                    provider_layer_effective=(
                        provider == token.provider_layer
                        and effective_provider is not None
                        and effective_provider.layer_token == provider
                    ),
                )
            )
        return tuple(views)

    def effective_backends(self) -> tuple[LocalBackendView, ...]:
        """Return one view per backend in deterministic registration order."""
        views = [self.effective(backend_id) for backend_id in self._layers]
        return tuple(view for view in views if view is not None)

    def cancel(self, backend_id: str, action: LocalAction | None = None) -> bool:
        cancelled = False
        for (token, operation), state in tuple(self._operations.items()):
            if token.backend_id == backend_id and (action is None or operation == action):
                state.signal.cancel()
                if not state.task.done():
                    state.task.cancel()
                cancelled = True
        return cancelled

    async def configure(
        self,
        backend_id: str,
        values: Mapping[str, str],
        *,
        progress: ProgressCallback | None = None,
    ) -> LocalOperationResult:
        view = self._require_view(backend_id)
        spec = view.backend.read_configure_spec()
        config_values, errors = _validate_values(spec, values)
        if errors:
            return LocalOperationResult(field_errors=errors)
        secret_values = tuple(
            config_values[key] for key in config_values.secret_keys if config_values[key]
        )
        return await self._run(
            view,
            "configure",
            lambda context: _invoke(view.backend.configure, config_values, context),
            progress=progress,
            secrets=secret_values,
        )

    async def status(
        self, backend_id: str, *, progress: ProgressCallback | None = None
    ) -> LocalOperationResult:
        view = self._require_view(backend_id)
        return await self._run(
            view,
            "refresh",
            lambda context: _invoke(view.backend.status, context),
            progress=progress,
        )

    async def refresh(
        self, backend_id: str, *, progress: ProgressCallback | None = None
    ) -> LocalOperationResult:
        view = self._require_view(backend_id)
        return await self._run(
            view,
            "refresh",
            lambda context: _invoke(view.backend.refresh, context),
            progress=progress,
        )

    async def doctor(
        self, backend_id: str, *, progress: ProgressCallback | None = None
    ) -> LocalOperationResult:
        view = self._require_view(backend_id)
        doctor = view.backend.doctor
        if doctor is None:
            return LocalOperationResult(
                diagnostics=(LocalDiagnostic("This action is unavailable.", "warning"),)
            )
        return await self._run(
            view,
            "doctor",
            lambda context: _invoke(doctor, context),
            progress=progress,
        )

    async def reset(
        self, backend_id: str, *, progress: ProgressCallback | None = None
    ) -> LocalOperationResult:
        view = self._require_view(backend_id)
        reset = view.backend.reset
        if reset is None:
            return LocalOperationResult(
                diagnostics=(LocalDiagnostic("This action is unavailable.", "warning"),)
            )
        if not view.use_available:
            return LocalOperationResult(
                diagnostics=(
                    LocalDiagnostic(
                        "Reset is unavailable while this backend is shadowed by another source.",
                        "warning",
                    ),
                )
            )
        return await self._run(
            view,
            "reset",
            lambda context: _invoke(reset, context),
            progress=progress,
        )

    async def manage_model(
        self,
        backend_id: str,
        action: Literal["load_model", "unload_model", "download_model"],
        model_id: str,
        *,
        progress: ProgressCallback | None = None,
    ) -> LocalOperationResult:
        view = self._require_view(backend_id)
        callback = getattr(view.backend, action)
        if callback is None:
            return LocalOperationResult(
                diagnostics=(LocalDiagnostic("This action is unavailable.", "warning"),)
            )
        if not view.use_available:
            return LocalOperationResult(
                diagnostics=(
                    LocalDiagnostic(
                        "This action is unavailable while this backend is shadowed by "
                        "another source.",
                        "warning",
                    ),
                )
            )
        return await self._run(
            view,
            action,
            lambda context: _invoke(callback, model_id, context),
            progress=progress,
        )

    def retire(self) -> None:
        if self._retired:
            return
        self._retired = True
        self._layers.clear()
        for token, _ in tuple(self._operations):
            self._cancel_token(token)

    async def aclose(self) -> None:
        self.retire()
        if self._operations:
            await asyncio.gather(
                *(operation.task for operation in tuple(self._operations.values())),
                return_exceptions=True,
            )

    async def _run(
        self,
        view: LocalBackendView,
        action: LocalAction,
        callback: Callable[[LocalOperationContext], object],
        *,
        progress: ProgressCallback | None,
        secrets: Sequence[str] = (),
    ) -> LocalOperationResult:
        token = view.token
        key = (token, action)
        prior = self._operations.get(key)
        if prior is not None:
            self._cancel_token(token, action)
        signal = SimpleCancellationToken()
        progress_events: list[LocalProgress] = []
        context = LocalOperationContext(
            signal=signal,
            action=action,
            generation_id=self._generation_id,
            backend_id=token.backend_id,
            source_id=token.source_id,
            _is_current=lambda: self._token_is_current(token),
            _progress=lambda item: _record_progress(
                _redact_progress(item, secrets), progress_events, progress
            ),
        )
        task = asyncio.create_task(
            self._invoke_operation(context, callback, progress_events, secrets),
            name=f"tau-local-backend:{token.source_id}:{token.backend_id}:{action}",
        )
        operation = _Operation(signal=signal, task=task, progress=progress_events)
        self._operations[key] = operation
        try:
            result = await task
        finally:
            if self._operations.get(key) is operation:
                self._operations.pop(key, None)
        if not self._token_is_current(token):
            return LocalOperationResult(
                progress=tuple(progress_events),
                cancelled=result.cancelled,
                stale=True,
            )
        return result

    async def _invoke_operation(
        self,
        context: LocalOperationContext,
        callback: Callable[[LocalOperationContext], object],
        progress_events: list[LocalProgress],
        secrets: Sequence[str],
    ) -> LocalOperationResult:
        try:
            value = callback(context)
            value = await value if inspect.isawaitable(value) else value
        except asyncio.CancelledError:
            context.signal.cancel()
            return LocalOperationResult(progress=tuple(progress_events), cancelled=True)
        except Exception:
            return LocalOperationResult(
                diagnostics=(LocalDiagnostic("The backend operation failed. Try again.", "error"),),
                progress=tuple(progress_events),
            )
        if context.cancelled:
            return LocalOperationResult(progress=tuple(progress_events), cancelled=True)
        if isinstance(value, LocalConfigureResult):
            return _safe_operation_result(
                LocalOperationResult(
                    backend_status=value.backend_status,
                    message=value.message,
                    diagnostics=value.diagnostics,
                    progress=tuple(progress_events),
                    committed=value.committed,
                    field_errors=value.field_errors,
                    credential_orphaned=value.credential_orphaned,
                ),
                secrets,
            )
        if isinstance(value, LocalBackendStatus):
            return _safe_operation_result(
                LocalOperationResult(
                    backend_status=value,
                    progress=tuple(progress_events),
                ),
                secrets,
            )
        if isinstance(value, LocalOperationResult):
            return _safe_operation_result(
                LocalOperationResult(
                    backend_status=value.backend_status,
                    message=value.message,
                    diagnostics=value.diagnostics,
                    progress=tuple((*progress_events, *value.progress)),
                    cancelled=value.cancelled,
                    stale=value.stale,
                    committed=value.committed,
                    field_errors=value.field_errors,
                    credential_orphaned=value.credential_orphaned,
                ),
                secrets,
            )
        return LocalOperationResult(
            diagnostics=(LocalDiagnostic("The backend returned an invalid result.", "error"),),
            progress=tuple(progress_events),
        )

    def _require_view(self, backend_id: str) -> LocalBackendView:
        if self._retired:
            raise LocalOperationError("Local backend registry generation is retired")
        view = self.effective(backend_id)
        if view is None:
            raise LocalOperationError(f"Unknown local backend: {backend_id}")
        return view

    def _token_is_current(self, token: LocalBackendLayerToken) -> bool:
        if self._retired or token.generation_id != self._generation_id:
            return False
        return any(item[0] == token for item in self._layers.get(token.backend_id, ()))

    def _cancel_token(
        self,
        token: LocalBackendLayerToken,
        action: LocalAction | None = None,
    ) -> bool:
        cancelled = False
        for (operation_token, operation_action), operation in tuple(self._operations.items()):
            if operation_token == token and (action is None or operation_action == action):
                operation.signal.cancel()
                if not operation.task.done():
                    operation.task.cancel()
                cancelled = True
        return cancelled

    def _assert_active(self) -> None:
        if self._retired:
            raise LocalOperationError("Local backend registry generation is retired")


def _validate_values(
    spec: LocalConfigureSpec,
    values: Mapping[str, str],
) -> tuple[LocalConfigValues, Mapping[str, str]]:
    submitted = dict(values)
    fields = {field.key: field for field in spec.fields}
    errors: dict[str, str] = {}
    for key in submitted:
        if key not in fields:
            errors[key] = "Unknown configuration field."
    for key, config_field in fields.items():
        value = submitted.get(key, "")
        if config_field.required and not value.strip():
            errors[key] = "This field is required."
        if config_field.kind == "choice" and value and value not in config_field.choices:
            errors[key] = "Choose one of the listed values."
    secret_keys = frozenset(
        field.key for field in spec.fields if field.kind == "secret" and field.key in submitted
    )
    return LocalConfigValues(submitted, secret_keys=secret_keys), MappingProxyType(errors)


def _record_progress(
    item: LocalProgress,
    events: list[LocalProgress],
    callback: ProgressCallback | None,
) -> None:
    events.append(item)
    if callback is not None:
        # A closing or replaced host must not turn backend work into a failed
        # transaction merely because progress rendering disappeared.
        with suppress(Exception):
            callback(item)


async def _invoke(callback: Callable[..., object], *args: object) -> object:
    """Invoke documented callbacks, accepting a no-context test double too."""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        value = callback(*args)
    else:
        positional = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        ]
        required = [
            parameter for parameter in positional if parameter.default is inspect.Parameter.empty
        ]
        if len(required) == 0:
            value = callback()
        elif len(positional) < len(args):
            value = callback(*args[: len(positional)])
        else:
            value = callback(*args)
    return await value if inspect.isawaitable(value) else value


def _redact(value: str | None, secrets: Sequence[str]) -> str | None:
    if value is None:
        return None
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[redacted]")
    return result


def _redact_progress(item: LocalProgress, secrets: Sequence[str]) -> LocalProgress:
    return LocalProgress(
        _redact(item.message, secrets) or "[redacted]",
        item.fraction,
        item.done,
    )


def _safe_operation_result(
    result: LocalOperationResult,
    secrets: Sequence[str],
) -> LocalOperationResult:
    """Keep backend-authored values out of host messages and diagnostics."""
    status = result.backend_status
    if status is not None:
        status = LocalBackendStatus(
            state=status.state,
            endpoint_display=_redact(status.endpoint_display, secrets),
            authentication_source=status.authentication_source,
            models=tuple(
                LocalModel(
                    id=_redact(model.id, secrets) or "[redacted]",
                    display_name=_redact(model.display_name, secrets),
                    state=_redact(model.state, secrets),
                )
                for model in status.models
            ),
            selected_model=_redact(status.selected_model, secrets),
            actions=status.actions,
            diagnostics=tuple(
                LocalDiagnostic(
                    _redact(item.message, secrets) or "[redacted]",
                    item.severity,
                    item.stage,
                )
                for item in status.diagnostics
            ),
            cached=status.cached,
            stale=status.stale,
        )
    return LocalOperationResult(
        backend_status=status,
        message=_redact(result.message, secrets),
        diagnostics=tuple(
            LocalDiagnostic(
                _redact(item.message, secrets) or "[redacted]",
                item.severity,
                item.stage,
            )
            for item in result.diagnostics
        ),
        progress=tuple(
            LocalProgress(
                _redact(item.message, secrets) or "[redacted]",
                item.fraction,
                item.done,
            )
            for item in result.progress
        ),
        cancelled=result.cancelled,
        stale=result.stale,
        committed=result.committed and not result.field_errors,
        field_errors={
            key: _redact(message, secrets) or "[redacted]"
            for key, message in result.field_errors.items()
        },
        credential_orphaned=result.credential_orphaned,
    )


def _source_id(value: str) -> str:
    _non_empty(value, "Local backend source id")
    if value != value.strip():
        raise LocalBackendError("Local backend source id must not have surrounding whitespace")
    return value


def _non_empty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise LocalBackendError(f"{label} must be a non-empty string")


def _identifier(value: str, label: str) -> None:
    _non_empty(value, label)
    if value != value.strip():
        raise LocalBackendError(f"{label} must not have surrounding whitespace")


__all__ = [
    "ConfigureLocalBackend",
    "DoctorLocalBackend",
    "DownloadModel",
    "LocalAction",
    "LocalBackend",
    "LocalBackendError",
    "LocalBackendLayerToken",
    "LocalBackendRegistry",
    "LocalBackendStatus",
    "LocalBackendView",
    "LocalConfigField",
    "LocalConfigValues",
    "LocalConfigureResult",
    "LocalConfigureSpec",
    "LocalConnectionState",
    "LocalDiagnostic",
    "LocalFieldKind",
    "LocalModel",
    "LocalOperationContext",
    "LocalOperationError",
    "LocalOperationResult",
    "LocalProgress",
    "ManageModel",
    "ReadConfigureSpec",
    "ReadLocalBackendStatus",
    "RefreshLocalBackend",
    "ResetLocalBackend",
]
