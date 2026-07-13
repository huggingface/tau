"""llama.cpp server discovery, setup, and diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

import httpx

from tau_agent.messages import UserMessage
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken
from tau_agent.types import JSONValue
from tau_ai import OpenAICompatibleConfig, OpenAICompatibleProvider
from tau_ai.events import ProviderErrorEvent, ProviderResponseStartEvent, ProviderToolCallEvent
from tau_ai.http import create_async_client
from tau_coding.provider_config import OpenAICompatibleProviderConfig

LLAMA_CPP_PROVIDER_NAME = "llama-cpp"
LLAMA_CPP_DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
LLAMA_CPP_API_KEY_ENV = "LLAMA_API_KEY"


class LlamaCppError(RuntimeError):
    """Raised when Tau cannot connect to or inspect a llama.cpp server."""


@dataclass(frozen=True, slots=True)
class LlamaCppServerInfo:
    """Models discovered from a running llama.cpp server."""

    base_url: str
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LlamaCppDiagnostic:
    """One setup/doctor check and its human-readable result."""

    check: str
    status: Literal["ok", "warning", "error"]
    message: str


@dataclass(frozen=True, slots=True)
class LlamaCppDoctorReport:
    """Complete diagnostic report for a llama.cpp provider."""

    diagnostics: tuple[LlamaCppDiagnostic, ...]

    @property
    def ok(self) -> bool:
        """Return whether every required check passed."""
        return all(item.status != "error" for item in self.diagnostics)


def normalize_llama_cpp_base_url(base_url: str) -> str:
    """Return a normalized OpenAI-compatible llama.cpp base URL."""
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise LlamaCppError("llama.cpp base URL cannot be empty")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


async def discover_llama_cpp(
    base_url: str = LLAMA_CPP_DEFAULT_BASE_URL,
    *,
    api_key: str | None = None,
    timeout_seconds: float = 5.0,
    client: httpx.AsyncClient | None = None,
) -> LlamaCppServerInfo:
    """Connect to llama.cpp and return model IDs from its OpenAI-compatible API."""
    normalized = normalize_llama_cpp_base_url(base_url)
    owned_client = client is None
    resolved_client = client or create_async_client(timeout=timeout_seconds)
    try:
        response = await resolved_client.get(
            f"{normalized}/models",
            headers=_authorization_headers(api_key),
        )
        if response.status_code in {401, 403}:
            raise LlamaCppError(
                "llama.cpp requires an API key. Set LLAMA_API_KEY or pass --api-key."
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise LlamaCppError("llama.cpp /v1/models returned invalid JSON") from exc
        models = _model_ids(payload)
        if not models:
            raise LlamaCppError(
                "llama.cpp returned no models. Wait for the model to finish loading and retry."
            )
        return LlamaCppServerInfo(base_url=normalized, models=models)
    except LlamaCppError:
        raise
    except httpx.HTTPStatusError as exc:
        raise LlamaCppError(
            f"llama.cpp /v1/models returned HTTP {exc.response.status_code}: "
            f"{exc.response.text[:200]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise LlamaCppError(
            f"Could not connect to llama.cpp at {normalized}. "
            "Start llama-server (port 8080 by default) and retry. "
            f"{exc}"
        ) from exc
    finally:
        if owned_client:
            await resolved_client.aclose()


def configured_llama_cpp_provider(
    provider: OpenAICompatibleProviderConfig,
    server: LlamaCppServerInfo,
    *,
    model: str | None = None,
) -> OpenAICompatibleProviderConfig:
    """Return the built-in llama.cpp provider updated with discovered models."""
    selected = model or server.models[0]
    if selected not in server.models:
        available = ", ".join(server.models)
        raise LlamaCppError(
            f"Model {selected!r} is not served by llama.cpp. Available models: {available}"
        )
    return replace(
        provider,
        base_url=server.base_url,
        models=server.models,
        default_model=selected,
    )


async def diagnose_llama_cpp(
    provider: OpenAICompatibleProviderConfig,
    *,
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> LlamaCppDoctorReport:
    """Check connectivity, discovery, streaming, and tool-call support."""
    diagnostics: list[LlamaCppDiagnostic] = []
    try:
        server = await discover_llama_cpp(
            provider.base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
        )
    except LlamaCppError as exc:
        return LlamaCppDoctorReport((LlamaCppDiagnostic("server", "error", str(exc)),))

    diagnostics.extend(
        (
            LlamaCppDiagnostic("server", "ok", f"Found llama.cpp at {server.base_url}"),
            LlamaCppDiagnostic("models", "ok", f"Models: {', '.join(server.models)}"),
        )
    )
    selected = (
        provider.default_model if provider.default_model in server.models else server.models[0]
    )
    runtime = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            api_key=api_key,
            base_url=server.base_url,
            provider_name=LLAMA_CPP_PROVIDER_NAME,
            timeout_seconds=timeout_seconds,
            max_retries=0,
            compat={"supportsStore": False, "supportsUsageInStreaming": False},
        ),
        client=client,
    )
    try:
        stream_ok, stream_error = await _probe_stream(runtime, selected)
        diagnostics.append(
            LlamaCppDiagnostic(
                "streaming",
                "ok" if stream_ok else "error",
                "Streaming chat completions accepted"
                if stream_ok
                else f"Streaming chat completion failed: {stream_error}",
            )
        )
        if stream_ok:
            tools_ok, tools_message = await _probe_tools(runtime, selected)
            diagnostics.append(
                LlamaCppDiagnostic(
                    "tools",
                    "ok" if tools_ok else "warning",
                    "Tool calls supported" if tools_ok else tools_message,
                )
            )
    finally:
        if client is None:
            await runtime.aclose()
    return LlamaCppDoctorReport(tuple(diagnostics))


async def _probe_stream(
    provider: OpenAICompatibleProvider,
    model: str,
) -> tuple[bool, str | None]:
    error: str | None = None
    accepted = False
    async for event in provider.stream_response(
        model=model,
        system="Reply briefly.",
        messages=[UserMessage(content="Reply with exactly: OK")],
        tools=[],
    ):
        if isinstance(event, ProviderResponseStartEvent):
            accepted = True
        elif isinstance(event, ProviderErrorEvent):
            error = event.message
    return accepted and error is None, error


async def _probe_tools(
    provider: OpenAICompatibleProvider,
    model: str,
) -> tuple[bool, str]:
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> AgentToolResult:
        del arguments, signal
        return AgentToolResult(tool_call_id="probe", name="tau_probe", ok=True, content="ok")

    tool = AgentTool(
        name="tau_probe",
        description="Call this tool to verify tool calling. Always call it.",
        input_schema={"type": "object", "properties": {}},
        executor=executor,
    )
    error: str | None = None
    called = False
    async for event in provider.stream_response(
        model=model,
        system="You must call the tau_probe tool.",
        messages=[UserMessage(content="Call tau_probe now.")],
        tools=[tool],
    ):
        if isinstance(event, ProviderToolCallEvent):
            called = True
        elif isinstance(event, ProviderErrorEvent):
            error = event.message
    if called:
        return True, "Tool calls supported"
    if error:
        return False, f"Tool-call probe failed: {error}"
    return False, (
        "The model did not call the probe tool. Use a tool-capable instruct GGUF and a "
        "compatible llama.cpp chat template."
    )


def _authorization_headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _model_ids(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        return ()
    data = payload.get("data")
    if not isinstance(data, list):
        return ()
    models: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip():
            models.append(model_id.strip())
    return tuple(dict.fromkeys(models))
