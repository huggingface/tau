"""llama.cpp protocol adapter used by the built-in extension."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal, cast
from urllib.parse import urlsplit, urlunsplit

import httpx

from tau_agent.harness import SimpleCancellationToken
from tau_agent.messages import TextContent, UserMessage
from tau_agent.provider import CancellationToken
from tau_agent.provider_events import (
    AssistantErrorEvent,
    AssistantStartEvent,
    ToolCallEndEvent,
)
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken
from tau_agent.types import JSONValue
from tau_ai.env import OpenAICompatibleConfig
from tau_ai.http import create_async_client
from tau_ai.openai_compatible import OpenAICompatibleProvider
from tau_coding.credentials import CredentialStore, FileCredentialStore, credentials_path
from tau_coding.extensions.providers import (
    DynamicProvider,
    OpenAICompatibleTransport,
    ProviderAuthContext,
    ProviderAuthError,
    ProviderModel,
    ProviderModelSnapshot,
    ProviderRefreshContext,
    ResolvedProviderAuth,
)
from tau_coding.local_backends import (
    LocalAction,
    LocalBackend,
    LocalBackendStatus,
    LocalConfigField,
    LocalConfigureResult,
    LocalConfigureSpec,
    LocalDiagnostic,
    LocalModel,
    LocalOperationContext,
    LocalOperationResult,
)
from tau_coding.paths import TauPaths

from .state import (
    LLAMA_CPP_CREDENTIAL_PREFIX,
    LlamaCppIntegrationState,
    LlamaCppStateError,
    LlamaCppStateStore,
    LlamaCppStoredModel,
)

LLAMA_CPP_PROVIDER_ID = "llama.cpp"
LLAMA_CPP_DISPLAY_NAME = "llama.cpp"
LLAMA_CPP_BACKEND_ID = "llama.cpp"
LLAMA_CPP_DEFAULT_ENDPOINT = "http://127.0.0.1:8080"
LLAMA_CPP_ENDPOINT_ENV = "LLAMA_BASE_URL"
LLAMA_CPP_API_KEY_ENV = "LLAMA_API_KEY"
DEFAULT_LLAMA_CPP_TIMEOUT_SECONDS = 5.0


class LlamaCppError(RuntimeError):
    """Raised for a safe, user-actionable llama.cpp integration failure."""


class LlamaCppEndpointError(ValueError):
    """Raised when an endpoint is not a safe HTTP base URL."""


@dataclass(frozen=True, slots=True)
class LlamaCppEndpoint:
    """The server root and OpenAI-compatible inference base for one endpoint."""

    server_root: str
    inference_base: str


@dataclass(frozen=True, slots=True)
class LlamaCppDiscovery:
    """Defensive result from the standard health and model endpoints."""

    endpoint: LlamaCppEndpoint
    models: tuple[ProviderModel, ...]
    health: str = "ok"


@dataclass(frozen=True, slots=True)
class _LlamaCppAuth:
    """Generation-referenced optional auth; secrets never enter the provider."""

    credential_ref: str | None = None
    env_var: str = LLAMA_CPP_API_KEY_ENV

    async def resolve(self, context: ProviderAuthContext) -> ResolvedProviderAuth:
        if self.credential_ref:
            stored = context.credentials.get(self.credential_ref)
            if stored:
                return ResolvedProviderAuth(
                    api_key=stored,
                    source="stored credential",
                    omit_authorization_header=False,
                )
        environment_value = context.environment.get(self.env_var)
        if environment_value:
            return ResolvedProviderAuth(
                api_key=environment_value,
                source=f"environment variable {self.env_var}",
                omit_authorization_header=False,
            )
        return ResolvedProviderAuth()


class _HttpFailure(LlamaCppError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class LlamaCppService:
    """Own endpoint state, discovery, diagnostics, and provider construction."""

    def __init__(
        self,
        *,
        state_store: LlamaCppStateStore | None = None,
        credential_store: CredentialStore | None = None,
        environment: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_LLAMA_CPP_TIMEOUT_SECONDS,
        register_provider: Callable[[DynamicProvider], None] | None = None,
        update_provider: Callable[[DynamicProvider], bool] | None = None,
        register_backend: Callable[[LocalBackend], None] | None = None,
    ) -> None:
        self.state_store = state_store or LlamaCppStateStore()
        self.credential_store: CredentialStore = (
            credential_store
            if credential_store is not None
            else FileCredentialStore(credentials_path(TauPaths()))
        )
        self.environment = dict(environment or {})
        if environment is None:
            import os

            self.environment = dict(os.environ)
        self.client = client
        self.timeout_seconds = timeout_seconds
        self._register_provider = register_provider
        self._update_provider = update_provider
        self._register_backend = register_backend
        self._last_discovery: LlamaCppDiscovery | None = None
        self._last_error: LlamaCppError | None = None
        self._state_error: str | None = None
        self._orphaned_credentials: tuple[str, ...] = ()
        self._stale_selected_model: str | None = None
        try:
            active = self.state_store.active()
        except LlamaCppStateError as exc:
            active = None
            self._state_error = str(exc)
        self._active_state = active
        self._endpoint_error: str | None = None
        endpoint = self._resolve_effective_endpoint(active)
        self._endpoint = endpoint or normalize_llama_cpp_endpoint(LLAMA_CPP_DEFAULT_ENDPOINT)
        self._cleanup_orphans()

    @property
    def configured(self) -> bool:
        return self._active_state is not None or bool(self.environment.get(LLAMA_CPP_ENDPOINT_ENV))

    @property
    def endpoint(self) -> LlamaCppEndpoint:
        return self._endpoint

    @property
    def endpoint_error(self) -> str | None:
        return self._endpoint_error

    @property
    def last_discovery(self) -> LlamaCppDiscovery | None:
        return self._last_discovery

    @property
    def state_error(self) -> str | None:
        return self._state_error

    @property
    def orphaned_credentials(self) -> tuple[str, ...]:
        return self._orphaned_credentials

    def provider(self) -> DynamicProvider:
        active = self._active_state
        models = (
            tuple(_provider_model(model) for model in active.models)
            if active is not None and active.endpoint == self.endpoint.server_root
            else ()
        )
        default = (
            None if self._stale_selected_model is not None else _selected_model(active, models)
        )
        return DynamicProvider(
            id=LLAMA_CPP_PROVIDER_ID,
            display_name=LLAMA_CPP_DISPLAY_NAME,
            models=models,
            default_model=default,
            transport=OpenAICompatibleTransport(
                base_url=self.endpoint.inference_base,
                auth=_LlamaCppAuth(active.credential_ref if active else None),
                timeout_seconds=self.timeout_seconds,
                max_retries=2,
                max_retry_delay_seconds=1.0,
                client=self.client,
            ),
            refresh_models=self.refresh_provider_models,
        )

    async def refresh_provider_models(
        self,
        context: ProviderRefreshContext,
    ) -> ProviderModelSnapshot:
        """Discovery callback used by the generic provider registry."""
        if not context.allow_network:
            return ProviderModelSnapshot(
                models=tuple(context.cached_models),
                default_model=_cached_default(context.cached_models),
            )
        discovery = await self.discover(context.auth, signal=context.signal)
        self._last_discovery = discovery
        self._last_error = None
        self._save_discovery(discovery)
        return ProviderModelSnapshot(
            models=discovery.models,
            default_model=_selected_model(self._active_state, discovery.models),
        )

    async def discover(
        self,
        auth: ResolvedProviderAuth,
        *,
        signal: CancellationToken | None = None,
    ) -> LlamaCppDiscovery:
        """Probe only the already-selected endpoint and parse standard models."""
        headers = _auth_headers(auth)
        owned_client = self.client is None
        client = self.client or create_async_client(timeout=self.timeout_seconds)
        try:
            health = await self._get(client, self.endpoint.server_root + "/health", headers, signal)
            health_state = _health_state(health)
            if health_state == "loading":
                raise LlamaCppError(
                    "llama.cpp is still loading a model. Wait for it to become ready and retry."
                )
            if health_state == "unavailable":
                raise LlamaCppError("llama.cpp reported that its server is unavailable.")
            response = await self._get(
                client, self.endpoint.inference_base + "/models", headers, signal
            )
            payload = _json_object(response, "/v1/models")
            models = _parse_models(payload)
            return LlamaCppDiscovery(self.endpoint, models, health=health_state)
        except asyncio.CancelledError:
            raise
        except LlamaCppError as exc:
            self._last_error = exc
            raise
        except httpx.TimeoutException as exc:
            error = LlamaCppError(
                f"Timed out connecting to llama.cpp at {self.endpoint.server_root}. "
                "Check the server and retry."
            )
            self._last_error = error
            raise error from exc
        except httpx.HTTPError as exc:
            error = LlamaCppError(
                f"Could not connect to llama.cpp at {self.endpoint.server_root}. "
                "Start llama-server and retry."
            )
            self._last_error = error
            raise error from exc
        finally:
            if owned_client:
                await client.aclose()

    async def configure(
        self,
        values: Mapping[str, str],
        context: LocalOperationContext,
    ) -> LocalConfigureResult:
        """Commit endpoint and optional credential as one safe-state transaction."""
        del context
        try:
            endpoint = normalize_llama_cpp_endpoint(values.get("endpoint", ""))
        except LlamaCppEndpointError as exc:
            return LocalConfigureResult(
                diagnostics=(LocalDiagnostic(str(exc), "error", "configuration"),)
            )
        key = values.get("api_key", "").strip() or None
        old_active = self._active_state
        try:
            prior_for_endpoint = self.state_store.get(endpoint.server_root)
        except Exception:
            return LocalConfigureResult(
                message="Could not save llama.cpp settings.",
                diagnostics=(
                    LocalDiagnostic(
                        "The existing llama.cpp settings could not be read.",
                        "error",
                        "configuration",
                    ),
                ),
            )

        credential_ref = (
            f"{LLAMA_CPP_CREDENTIAL_PREFIX}{secrets.token_urlsafe(18)}" if key else None
        )
        if credential_ref is not None:
            assert key is not None
            try:
                self.credential_store.set(credential_ref, key)
            except Exception:
                return LocalConfigureResult(
                    message="Could not save llama.cpp settings.",
                    diagnostics=(
                        LocalDiagnostic(
                            "The API key could not be stored; no settings were changed.",
                            "error",
                            "credentials",
                        ),
                    ),
                )

        state = LlamaCppIntegrationState(
            endpoint=endpoint.server_root,
            selected_model=(prior_for_endpoint.selected_model if prior_for_endpoint else None),
            credential_ref=credential_ref,
            models=(prior_for_endpoint.models if prior_for_endpoint else ()),
            checked_at=(prior_for_endpoint.checked_at if prior_for_endpoint else None),
        )
        try:
            self.state_store.save(
                state,
                replace_endpoint=(
                    old_active.endpoint
                    if old_active is not None and old_active.endpoint != endpoint.server_root
                    else None
                ),
            )
        except Exception:
            orphaned = False
            if credential_ref is not None:
                try:
                    self.credential_store.delete(credential_ref)
                except Exception:
                    orphaned = True
            return LocalConfigureResult(
                message="llama.cpp settings were not saved.",
                diagnostics=(
                    LocalDiagnostic(
                        "The previous llama.cpp configuration is still active.",
                        "error",
                        "configuration",
                    ),
                ),
                credential_orphaned=orphaned,
            )

        self._active_state = state
        self._endpoint = endpoint
        self._endpoint_error = None
        self._last_error = None
        self._stale_selected_model = None
        cleanup_error = self._delete_unreferenced_credentials(
            old_active.credential_ref if old_active else None
        )
        self._publish_provider()
        discovery = await self._try_discover_current()
        status = (
            self._status_from_discovery(discovery)
            if discovery is not None
            else self._cached_status(
                diagnostics=(
                    LocalDiagnostic(
                        "Start llama-server at the configured endpoint and refresh.",
                        "warning",
                        "connection",
                    ),
                ),
                stale=True,
            )
        )
        diagnostics = list(status.diagnostics)
        if cleanup_error:
            diagnostics.append(LocalDiagnostic(cleanup_error, "warning", "credentials"))
        return LocalConfigureResult(
            committed=True,
            backend_status=status,
            message="llama.cpp settings saved.",
            diagnostics=tuple(diagnostics),
            credential_orphaned=bool(cleanup_error),
        )

    async def status(self, context: LocalOperationContext) -> LocalBackendStatus:
        del context
        if not self.configured:
            if self._state_error or self._endpoint_error:
                return self._cached_status()
            return self._status(
                "unconfigured",
                diagnostics=(
                    LocalDiagnostic(
                        "Configure an endpoint to connect to llama.cpp.",
                        "info",
                        "configuration",
                    ),
                ),
            )
        if self._last_discovery is not None:
            return self._status_from_discovery(self._last_discovery)
        return self._cached_status()

    async def refresh(self, context: LocalOperationContext) -> LocalOperationResult:
        if not self.configured:
            return LocalOperationResult(
                backend_status=self._status(
                    "unconfigured",
                    diagnostics=(
                        LocalDiagnostic(
                            "No configured endpoint was probed. Enter an endpoint in Configure.",
                            "info",
                            "configuration",
                        ),
                    ),
                )
            )
        try:
            auth = await _resolve_auth_for_backend(self)
            discovery = await self.discover(auth, signal=context.signal)
            self._last_discovery = discovery
            self._save_discovery(discovery)
            self._publish_provider()
            return LocalOperationResult(backend_status=self._status_from_discovery(discovery))
        except asyncio.CancelledError:
            return LocalOperationResult(cancelled=True)
        except LlamaCppError as exc:
            return LocalOperationResult(
                backend_status=self._cached_status(
                    diagnostics=(LocalDiagnostic(str(exc), "error", "connection"),),
                    stale=True,
                ),
                diagnostics=(LocalDiagnostic(str(exc), "error", "connection"),),
            )

    async def doctor(self, context: LocalOperationContext) -> LocalOperationResult:
        if not self.configured:
            return LocalOperationResult(
                backend_status=await self.status(context),
                diagnostics=(
                    LocalDiagnostic("Configure an endpoint before running doctor.", "warning"),
                ),
            )
        try:
            auth = await _resolve_auth_for_backend(self)
            discovery = await self.discover(auth, signal=context.signal)
        except asyncio.CancelledError:
            return LocalOperationResult(cancelled=True)
        except LlamaCppError as exc:
            return LocalOperationResult(
                backend_status=self._cached_status(
                    diagnostics=(LocalDiagnostic(str(exc), "error", "connection"),),
                    stale=True,
                ),
                diagnostics=(LocalDiagnostic(str(exc), "error", "connection"),),
            )
        selected = _selected_model(self._active_state, discovery.models)
        if selected is None:
            return LocalOperationResult(
                backend_status=self._status_from_discovery(discovery),
                diagnostics=(
                    LocalDiagnostic(
                        "The server is reachable but has no selectable model.",
                        "warning",
                        "models",
                    ),
                ),
            )
        diagnostics = [
            LocalDiagnostic(
                f"Found llama.cpp at {discovery.endpoint.inference_base}", "info", "server"
            ),
            LocalDiagnostic(f"Discovered model: {selected}", "info", "models"),
        ]
        stream_ok, stream_message = await self._probe_stream(auth, selected, context.signal)
        diagnostics.append(
            LocalDiagnostic(stream_message, "info" if stream_ok else "error", "streaming")
        )
        if stream_ok:
            tools_ok, tools_message = await self._probe_tools(auth, selected, context.signal)
            diagnostics.append(
                LocalDiagnostic(tools_message, "info" if tools_ok else "warning", "tools")
            )
        return LocalOperationResult(
            backend_status=self._status_from_discovery(discovery), diagnostics=tuple(diagnostics)
        )

    async def reset(self, context: LocalOperationContext) -> LocalOperationResult:
        del context
        try:
            refs = self.state_store.clear()
        except LlamaCppStateError as exc:
            return LocalOperationResult(diagnostics=(LocalDiagnostic(str(exc), "error", "reset"),))
        self._active_state = None
        self._last_discovery = None
        self._last_error = None
        self._stale_selected_model = None
        self._endpoint = self._endpoint_from_environment_or_default()
        self._publish_provider()
        diagnostics = (
            (
                LocalDiagnostic(
                    "Saved settings were removed. Stored credentials were retained; "
                    "confirm credential deletion separately.",
                    "warning",
                    "reset",
                ),
            )
            if refs
            else ()
        )
        return LocalOperationResult(
            message="llama.cpp integration settings reset.",
            backend_status=await self.status(_noop_context("reset")),
            diagnostics=diagnostics,
            committed=True,
        )

    def backend(self) -> LocalBackend:
        return LocalBackend(
            id=LLAMA_CPP_BACKEND_ID,
            provider_id=LLAMA_CPP_PROVIDER_ID,
            display_name=LLAMA_CPP_DISPLAY_NAME,
            configure_spec=LocalConfigureSpec(
                fields=(
                    LocalConfigField(
                        "endpoint",
                        "llama.cpp endpoint",
                        "text",
                        required=True,
                        placeholder=f"{LLAMA_CPP_DEFAULT_ENDPOINT} or .../v1",
                    ),
                    LocalConfigField(
                        "api_key",
                        "API key (optional)",
                        "secret",
                        required=False,
                        placeholder="Leave empty for no authentication",
                    ),
                )
            ),
            configure=self.configure,
            status=self.status,
            refresh=self.refresh,
            doctor=self.doctor,
            reset=self.reset,
            recommended=True,
        )

    def _resolve_effective_endpoint(
        self,
        active: LlamaCppIntegrationState | None,
    ) -> LlamaCppEndpoint | None:
        if active is not None:
            try:
                return normalize_llama_cpp_endpoint(active.endpoint)
            except LlamaCppEndpointError as exc:
                self._endpoint_error = str(exc)
                return None
        environment_endpoint = self.environment.get(LLAMA_CPP_ENDPOINT_ENV, "").strip()
        if not environment_endpoint:
            return None
        try:
            return normalize_llama_cpp_endpoint(environment_endpoint)
        except LlamaCppEndpointError as exc:
            self._endpoint_error = str(exc)
            return None

    def _endpoint_from_environment_or_default(self) -> LlamaCppEndpoint:
        endpoint = self._resolve_effective_endpoint(None)
        return endpoint or normalize_llama_cpp_endpoint(LLAMA_CPP_DEFAULT_ENDPOINT)

    def _cleanup_orphans(self) -> None:
        try:
            referenced = self.state_store.referenced_credentials()
            names = self.credential_store.names(prefix=LLAMA_CPP_CREDENTIAL_PREFIX)
            orphaned: list[str] = []
            for name in names:
                if name in referenced:
                    continue
                try:
                    self.credential_store.delete(name)
                except Exception:
                    orphaned.append(name)
            self._orphaned_credentials = tuple(orphaned)
        except Exception:
            self._orphaned_credentials = ()

    def _delete_unreferenced_credentials(self, old_ref: str | None) -> str | None:
        if not old_ref:
            return None
        try:
            if old_ref not in self.state_store.referenced_credentials():
                self.credential_store.delete(old_ref)
        except Exception:
            return "The new configuration is active, but the previous credential needs cleanup."
        return None

    async def _try_discover_current(self) -> LlamaCppDiscovery | None:
        try:
            auth = await _resolve_auth_for_backend(self)
            discovery = await self.discover(auth)
        except (LlamaCppError, ProviderAuthError):
            return None
        self._last_discovery = discovery
        self._save_discovery(discovery)
        self._publish_provider()
        return discovery

    def _save_discovery(self, discovery: LlamaCppDiscovery) -> None:
        if (
            self._active_state is None
            or self._active_state.endpoint != discovery.endpoint.server_root
        ):
            # Environment endpoints are intentionally not copied to safe state.
            return
        previous_selected = self._active_state.selected_model
        selected = _selected_model(self._active_state, discovery.models)
        self._stale_selected_model = (
            previous_selected
            if previous_selected is not None
            and selected is None
            and previous_selected not in {model.id for model in discovery.models}
            else None
        )
        state = LlamaCppIntegrationState(
            endpoint=discovery.endpoint.server_root,
            selected_model=selected,
            credential_ref=self._active_state.credential_ref,
            models=tuple(_stored_model(model) for model in discovery.models),
            checked_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        try:
            self.state_store.save(state)
            self._active_state = state
        except LlamaCppStateError as exc:
            self._state_error = str(exc)

    def _publish_provider(self) -> None:
        provider = self.provider()
        updated = self._update_provider(provider) if self._update_provider is not None else False
        if not updated and self._register_provider is not None:
            self._register_provider(provider)
        if not updated and self._register_backend is not None:
            # Initial setup and a source replacement need a paired backend
            # registration. Snapshot updates use ``update_provider`` above so
            # an in-flight local operation keeps its source-layer token.
            self._register_backend(self.backend())

    def _status_from_discovery(self, discovery: LlamaCppDiscovery) -> LocalBackendStatus:
        self._last_error = None
        stale_model = self._stale_selected_model
        diagnostics: list[LocalDiagnostic] = []
        if not discovery.models:
            diagnostics.append(
                LocalDiagnostic(
                    "Server is ready but exposes no selectable models.", "warning", "models"
                )
            )
        if stale_model is not None:
            diagnostics.append(
                LocalDiagnostic(
                    f"The active model {stale_model!r} is no longer reported by the server; "
                    "the current runtime remains usable, but select it again after it returns.",
                    "warning",
                    "models",
                )
            )
        return self._status(
            "stale"
            if stale_model is not None
            else ("ready" if discovery.models else "unavailable"),
            models=discovery.models,
            selected=(
                None
                if stale_model is not None
                else _selected_model(self._active_state, discovery.models)
            ),
            diagnostics=tuple(diagnostics),
            stale=stale_model is not None,
        )

    def _cached_status(
        self,
        *,
        diagnostics: tuple[LocalDiagnostic, ...] = (),
        stale: bool = False,
    ) -> LocalBackendStatus:
        active = self._active_state
        models = tuple(_provider_model(model) for model in active.models) if active else ()
        selected = _selected_model(active, models)
        if self._state_error:
            diagnostics = (*diagnostics, LocalDiagnostic(self._state_error, "warning", "state"))
        if self._endpoint_error:
            diagnostics = (*diagnostics, LocalDiagnostic(self._endpoint_error, "error", "endpoint"))
        if self._stale_selected_model is not None:
            diagnostics = (
                *diagnostics,
                LocalDiagnostic(
                    f"The active model {self._stale_selected_model!r} is not in the cached "
                    "snapshot; the current runtime remains usable.",
                    "warning",
                    "models",
                ),
            )
        return self._status(
            "stale"
            if (stale or self._stale_selected_model is not None) and models
            else "unavailable",
            models=models,
            selected=(None if self._stale_selected_model is not None else selected),
            diagnostics=diagnostics,
            cached=bool(models),
            stale=stale,
        )

    def _status(
        self,
        state: str,
        *,
        models: tuple[ProviderModel, ...] = (),
        selected: str | None = None,
        diagnostics: tuple[LocalDiagnostic, ...] = (),
        cached: bool = False,
        stale: bool = False,
    ) -> LocalBackendStatus:
        actions: list[LocalAction] = ["configure", "refresh", "doctor", "reset"]
        if selected is not None:
            actions.append("use")
        return LocalBackendStatus(
            state=state,  # type: ignore[arg-type]
            endpoint_display=self.endpoint.inference_base if self.configured else None,
            authentication_source=_authentication_source(
                self._active_state,
                self.environment,
                self.credential_store,
            ),
            models=tuple(_local_model(model) for model in models),
            selected_model=selected,
            actions=tuple(actions),
            diagnostics=diagnostics,
            cached=cached,
            stale=stale,
        )

    async def _probe_stream(
        self,
        auth: ResolvedProviderAuth,
        model: str,
        signal: SimpleCancellationToken,
    ) -> tuple[bool, str]:
        owned_client = self.client is None
        client = self.client or create_async_client(timeout=self.timeout_seconds)
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key=auth.api_key or "",
                base_url=self.endpoint.inference_base,
                provider_name=LLAMA_CPP_PROVIDER_ID,
                timeout_seconds=self.timeout_seconds,
                max_retries=0,
                omit_authorization_header=auth.omit_authorization_header,
                infer_api_from_model=False,
            ),
            client=client,
        )
        try:
            async for event in provider.stream_response(
                model=model,
                system="Reply briefly.",
                messages=[UserMessage(content="Reply with exactly: OK")],
                tools=[],
                signal=signal,
            ):
                if isinstance(event, AssistantStartEvent):
                    return True, "Streaming chat completions accepted."
                if isinstance(event, AssistantErrorEvent):
                    return False, f"Streaming chat completions failed: {event.error.text}"
        except httpx.HTTPError:
            return False, "Streaming chat completions could not be reached."
        finally:
            if owned_client:
                await provider.aclose()
        return False, "The server did not accept a streaming completion."

    async def _probe_tools(
        self,
        auth: ResolvedProviderAuth,
        model: str,
        signal: SimpleCancellationToken,
    ) -> tuple[bool, str]:
        async def executor(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: Callable[[AgentToolResult], None] | None = None,
        ) -> AgentToolResult:
            del tool_call_id, arguments, signal, on_update
            return AgentToolResult(content=[TextContent(text="ok")])

        tool = AgentTool(
            name="tau_probe",
            label="tau_probe",
            description="Call this tool to verify tool calling.",
            parameters={"type": "object", "properties": {}},
            execute_fn=executor,
        )
        owned_client = self.client is None
        client = self.client or create_async_client(timeout=self.timeout_seconds)
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key=auth.api_key or "",
                base_url=self.endpoint.inference_base,
                provider_name=LLAMA_CPP_PROVIDER_ID,
                timeout_seconds=self.timeout_seconds,
                max_retries=0,
                omit_authorization_header=auth.omit_authorization_header,
                infer_api_from_model=False,
            ),
            client=client,
        )
        try:
            async for event in provider.stream_response(
                model=model,
                system="Call the tau_probe tool.",
                messages=[UserMessage(content="Call tau_probe now.")],
                tools=[tool],
                signal=signal,
            ):
                if isinstance(event, ToolCallEndEvent):
                    return True, "Tool calls supported."
                if isinstance(event, AssistantErrorEvent):
                    return False, f"Tool-call probe failed: {event.error.text}"
        except httpx.HTTPError:
            return False, "Tool-call compatibility could not be checked."
        finally:
            if owned_client:
                await provider.aclose()
        return False, (
            "Streaming works, but the model did not emit a tool call. Use a tool-capable "
            "instruct GGUF and a compatible llama.cpp chat template."
        )

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: Mapping[str, str],
        signal: CancellationToken | None,
    ) -> httpx.Response:
        if signal is not None and signal.is_cancelled():
            raise asyncio.CancelledError
        try:
            response = await client.get(url, headers=dict(headers))
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            raise
        if response.status_code in {401, 403}:
            raise _HttpFailure(
                response.status_code,
                "llama.cpp rejected the request. Check the optional API key or LLAMA_API_KEY.",
            )
        if response.status_code == 503 and url.endswith("/health"):
            health = _safe_json(response)
            if _health_state(health) == "loading":
                raise LlamaCppError(
                    "llama.cpp is still loading a model. Wait for it to become ready and retry."
                )
        if response.status_code == 404 and url.endswith("/health"):
            return response
        if response.status_code >= 400:
            raise _HttpFailure(
                response.status_code,
                f"llama.cpp returned HTTP {response.status_code} while checking its server.",
            )
        return response


def normalize_llama_cpp_endpoint(value: str) -> LlamaCppEndpoint:
    """Normalize a server root or OpenAI-compatible ``/v1`` URL safely."""
    if not isinstance(value, str) or not value.strip():
        raise LlamaCppEndpointError("llama.cpp endpoint cannot be empty")
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        raise LlamaCppEndpointError("llama.cpp endpoint must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise LlamaCppEndpointError("llama.cpp endpoint must not contain credentials")
    if parsed.query or parsed.fragment:
        raise LlamaCppEndpointError("llama.cpp endpoint must not contain a query or fragment")
    if not parsed.netloc or parsed.hostname is None:
        raise LlamaCppEndpointError("llama.cpp endpoint must include a host")
    path = parsed.path.rstrip("/")
    if path == "/v1":
        path = ""
    elif path.endswith("/v1"):
        path = path[: -len("/v1")].rstrip("/")
    root = urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))
    root = root.rstrip("/")
    if not root:
        raise LlamaCppEndpointError("llama.cpp endpoint must include a host")
    return LlamaCppEndpoint(server_root=root, inference_base=f"{root}/v1")


def _auth_headers(auth: ResolvedProviderAuth) -> dict[str, str]:
    headers = dict(auth.headers)
    if auth.api_key is not None and not auth.omit_authorization_header:
        headers.setdefault("Authorization", f"Bearer {auth.api_key}")
    return headers


async def _resolve_auth_for_backend(service: LlamaCppService) -> ResolvedProviderAuth:
    active = service._active_state
    return await _LlamaCppAuth(active.credential_ref if active else None).resolve(
        ProviderAuthContext(
            credentials=service.credential_store,
            environment=service.environment,
        )
    )


def _safe_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _json_object(response: httpx.Response, endpoint: str) -> Mapping[str, object]:
    payload = _safe_json(response)
    if not isinstance(payload, Mapping):
        raise LlamaCppError(f"llama.cpp {endpoint} returned malformed JSON.")
    return payload


def _health_state(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return "ok"
    status = payload.get("status")
    if isinstance(status, str):
        normalized = status.casefold()
        if any(word in normalized for word in ("loading", "starting", "initializing")):
            return "loading"
        if any(word in normalized for word in ("unavailable", "error", "failed")):
            return "unavailable"
    return "ok"


def _parse_models(payload: Mapping[str, object]) -> tuple[ProviderModel, ...]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise LlamaCppError("llama.cpp /v1/models returned a malformed model list.")
    models: list[ProviderModel] = []
    ids: set[str] = set()
    for item in data:
        if not isinstance(item, Mapping):
            raise LlamaCppError("llama.cpp /v1/models returned a malformed model.")
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id or model_id != model_id.strip():
            raise LlamaCppError("llama.cpp /v1/models returned a model without an exact id.")
        if model_id in ids:
            raise LlamaCppError("llama.cpp /v1/models returned duplicate model ids.")
        ids.add(model_id)
        display = item.get("name", item.get("display_name"))
        display_name = display if isinstance(display, str) and display.strip() else model_id
        modalities = _modalities(item)
        context_window = _reported_context_window(item)
        models.append(
            ProviderModel(
                id=model_id,
                display_name=display_name,
                api="openai-completions",
                context_window=context_window,
                input_modalities=modalities,
                # No output-limit, reasoning, cost, or tool-support guesses.
                compat=_safe_model_compat(item),
            )
        )
    return tuple(models)


def _modalities(item: Mapping[str, object]) -> tuple[Literal["text", "image"], ...] | None:
    value = item.get("input_modalities", item.get("modalities"))
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(entry, str) and entry in {"text", "image"} for entry in value):
        return None
    result = tuple(dict.fromkeys(value))
    return cast(tuple[Literal["text", "image"], ...], result) or None


def _reported_context_window(item: Mapping[str, object]) -> int | None:
    # These names are accepted only when directly reported by a server. Tau
    # never derives one from a model family, max tokens, or a 128K convention.
    for key in ("context_window", "context_length"):
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _safe_model_compat(item: Mapping[str, object]) -> Mapping[str, JSONValue]:
    # Keep a tiny, non-secret diagnostic subset in memory.  It is not copied to
    # the disk snapshot and is never interpreted as capability metadata.
    result: dict[str, JSONValue] = {}
    for key in ("object", "owned_by"):
        value = item.get(key)
        if isinstance(value, str) and value:
            result[key] = value
    return MappingProxyType(result)


def _provider_model(model: LlamaCppStoredModel) -> ProviderModel:
    return ProviderModel(
        id=model.id,
        display_name=model.display_name or model.id,
        api="openai-completions",
        context_window=model.context_window,
        input_modalities=(
            cast(tuple[Literal["text", "image"], ...], model.input_modalities)
            if model.input_modalities is not None
            else None
        ),
    )


def _stored_model(model: ProviderModel) -> LlamaCppStoredModel:
    return LlamaCppStoredModel(
        id=model.id,
        display_name=model.display_name,
        context_window=model.context_window,
        input_modalities=model.input_modalities,
    )


def _local_model(model: ProviderModel) -> LocalModel:
    return LocalModel(model.id, model.display_name or model.id)


def _selected_model(
    state: LlamaCppIntegrationState | None,
    models: tuple[ProviderModel, ...],
) -> str | None:
    ids = {model.id for model in models}
    if state is not None and state.selected_model is not None:
        # Never silently replace an explicitly selected model when the server
        # no longer reports it. The active runtime may continue using it, but
        # a new selection must wait for the model to return.
        return state.selected_model if state.selected_model in ids else None
    if len(models) == 1:
        return models[0].id
    return None


def _cached_default(models: tuple[ProviderModel, ...]) -> str | None:
    return models[0].id if len(models) == 1 else None


def _authentication_source(
    state: LlamaCppIntegrationState | None,
    environment: Mapping[str, str],
    credentials: CredentialStore | None = None,
) -> Literal["none", "environment", "stored credential"]:
    if state is not None and state.credential_ref:
        try:
            if credentials is None or credentials.get(state.credential_ref):
                return "stored credential"
        except Exception:
            pass
    if environment.get(LLAMA_CPP_API_KEY_ENV):
        return "environment"
    return "none"


def _noop_context(action: str) -> LocalOperationContext:
    signal = SimpleCancellationToken()
    return LocalOperationContext(
        signal=signal,
        action=action,  # type: ignore[arg-type]
        generation_id="reset",
        backend_id=LLAMA_CPP_BACKEND_ID,
        source_id="built-in:llama.cpp",
        _is_current=lambda: True,
        _progress=lambda _: None,
    )


__all__ = [
    "DEFAULT_LLAMA_CPP_TIMEOUT_SECONDS",
    "LLAMA_CPP_API_KEY_ENV",
    "LLAMA_CPP_BACKEND_ID",
    "LLAMA_CPP_DEFAULT_ENDPOINT",
    "LLAMA_CPP_DISPLAY_NAME",
    "LLAMA_CPP_ENDPOINT_ENV",
    "LLAMA_CPP_PROVIDER_ID",
    "LlamaCppDiscovery",
    "LlamaCppEndpoint",
    "LlamaCppEndpointError",
    "LlamaCppError",
    "LlamaCppService",
    "normalize_llama_cpp_endpoint",
]
