"""Bounded cache-aware routing policy for Tau's built-in Hugging Face provider."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from json import dumps
from typing import Literal, cast
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tau_agent.messages import AgentMessage, Usage
from tau_agent.tools import AgentTool
from tau_agent.types import JSONValue
from tau_coding.provider_config import (
    ProviderConfigError,
    validate_huggingface_inference_provider,
)

HF_CACHE_ROUTING_NAMESPACE = "tau.huggingface-cache-routing"
HF_CACHE_MIN_PROMPT_TOKENS = 4_096
HF_CACHE_PROBES_PER_ROUTE = 2
HF_CACHE_MAX_CANDIDATES = 3
HF_CACHE_MAX_ELIGIBLE_REQUESTS = 9
HF_MODEL_API_BASE_URL = "https://huggingface.co/api/models"

type HuggingFaceRoutingPhase = Literal["evaluating", "retained", "reroute", "exhausted"]


class RequestContextFingerprint(BaseModel):
    """Minimal durable evidence for append-only request comparability."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    static_digest: str
    message_count: int = Field(ge=0)
    message_digest: str


class HuggingFaceRoutingState(BaseModel):
    """Versioned session-owned state for Hugging Face adaptive routing."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    version: Literal[1] = 1
    model: str
    route: str | None = None
    phase: HuggingFaceRoutingPhase = "evaluating"
    attempted_routes: tuple[str, ...] = ()
    unavailable_routes: tuple[str, ...] = ()
    eligible_requests: int = Field(default=0, ge=0)
    absent_probes: int = Field(default=0, ge=0)
    zero_probes: int = Field(default=0, ge=0)
    last_context: RequestContextFingerprint | None = None
    last_reason: str | None = None

    @classmethod
    def automatic(cls, model: str, *, route: str | None = None) -> HuggingFaceRoutingState:
        """Create a fresh automatic-routing evaluator for one logical model."""
        attempted = (route,) if route is not None else ()
        return cls(model=model, route=route, attempted_routes=attempted)

    def to_custom_data(self) -> dict[str, JSONValue]:
        """Return a JSON-compatible snapshot for an application custom entry."""
        return cast(dict[str, JSONValue], self.model_dump(mode="json"))

    @classmethod
    def from_custom_data(cls, data: Mapping[str, JSONValue]) -> HuggingFaceRoutingState | None:
        """Load a supported snapshot, ignoring malformed or future versions."""
        if data.get("version") != 1:
            return None
        try:
            return cls.model_validate(data)
        except ValidationError:
            return None


def observe_huggingface_cache(
    state: HuggingFaceRoutingState,
    usage: Usage,
    *,
    system: str,
    messages: Sequence[AgentMessage],
    tools: Sequence[AgentTool],
) -> HuggingFaceRoutingState:
    """Apply one successful response's cache evidence to an automatic state."""
    if state.phase != "evaluating":
        return state
    if state.route is None:
        return state.model_copy(update={"last_reason": "waiting for automatic route resolution"})

    prompt_tokens = usage.input + usage.cache_read + usage.cache_write
    if prompt_tokens < HF_CACHE_MIN_PROMPT_TOKENS:
        return state.model_copy(
            update={
                "last_reason": (
                    f"prompt below {HF_CACHE_MIN_PROMPT_TOKENS}-token eligibility threshold"
                )
            }
        )

    context = _request_context_fingerprint(system=system, messages=messages, tools=tools)
    if state.last_context is None:
        eligible_requests = state.eligible_requests + 1
        cold_update: dict[str, object] = {
            "eligible_requests": eligible_requests,
            "last_context": context,
            "last_reason": "cold route warm-up",
        }
        if usage.cache_read_reported is True and usage.cache_read > 0:
            cold_update.update(phase="retained", last_reason="positive cache reuse reported")
        else:
            cold_update.update(
                _budget_exhaustion_update(state, eligible_requests=eligible_requests)
            )
        return state.model_copy(update=cold_update)

    if not _context_extends(
        state.last_context,
        system=system,
        messages=messages,
        tools=tools,
    ):
        return state.model_copy(
            update={
                "absent_probes": 0,
                "zero_probes": 0,
                "last_context": context,
                "last_reason": "request context was not append-only comparable",
            }
        )

    eligible_requests = state.eligible_requests + 1
    if usage.cache_read_reported is True and usage.cache_read > 0:
        return state.model_copy(
            update={
                "phase": "retained",
                "eligible_requests": eligible_requests,
                "last_context": context,
                "last_reason": "positive cache reuse reported",
            }
        )

    absent_probes = state.absent_probes + (usage.cache_read_reported is not True)
    zero_probes = state.zero_probes + (usage.cache_read_reported is True)
    failed_probes = absent_probes + zero_probes
    update: dict[str, object] = {
        "eligible_requests": eligible_requests,
        "absent_probes": absent_probes,
        "zero_probes": zero_probes,
        "last_context": context,
        "last_reason": (
            "no effective reported cache reuse after warmed probes "
            f"(telemetry absent: {absent_probes}, reported zero: {zero_probes})"
        ),
    }
    budget_update = _budget_exhaustion_update(state, eligible_requests=eligible_requests)
    if budget_update:
        update.update(budget_update)
    elif failed_probes >= HF_CACHE_PROBES_PER_ROUTE:
        update["phase"] = "reroute"
    return state.model_copy(update=update)


def resolve_automatic_huggingface_route(
    state: HuggingFaceRoutingState,
    route: str,
) -> HuggingFaceRoutingState:
    """Record the first route resolved by Hugging Face automatic routing."""
    normalized = validate_huggingface_inference_provider(route)
    attempted = _append_unique(state.attempted_routes, normalized)
    return state.model_copy(
        update={
            "route": normalized,
            "phase": "evaluating",
            "attempted_routes": attempted,
            "absent_probes": 0,
            "zero_probes": 0,
            "last_context": None,
            "last_reason": "automatic route resolved",
        }
    )


def reroute_huggingface_state(
    state: HuggingFaceRoutingState,
    route: str,
    *,
    reason: str,
) -> HuggingFaceRoutingState:
    """Start a cold bounded probe on a replacement route."""
    normalized = validate_huggingface_inference_provider(route)
    return state.model_copy(
        update={
            "route": normalized,
            "phase": "evaluating",
            "attempted_routes": _append_unique(state.attempted_routes, normalized),
            "absent_probes": 0,
            "zero_probes": 0,
            "last_context": None,
            "last_reason": reason,
        }
    )


def mark_huggingface_route_unavailable(
    state: HuggingFaceRoutingState,
    route: str,
    *,
    reason: str | None = None,
) -> HuggingFaceRoutingState:
    """Consume one candidate slot without counting it as cache evidence."""
    normalized = validate_huggingface_inference_provider(route)
    update: dict[str, object] = {
        "attempted_routes": _append_unique(state.attempted_routes, normalized),
        "unavailable_routes": _append_unique(state.unavailable_routes, normalized),
    }
    if reason is not None:
        update["last_reason"] = reason
    if normalized == state.route:
        update["phase"] = "reroute"
    return state.model_copy(update=update)


def exhaust_huggingface_routing(
    state: HuggingFaceRoutingState,
    *,
    reason: str,
) -> HuggingFaceRoutingState:
    """Stop automatic route changes while leaving the current route usable."""
    return state.model_copy(update={"phase": "exhausted", "last_reason": reason})


def next_huggingface_routes(
    state: HuggingFaceRoutingState,
    discovered_routes: Sequence[str],
) -> tuple[str, ...]:
    """Return deterministic untried routes within the total candidate budget."""
    remaining = max(0, HF_CACHE_MAX_CANDIDATES - len(state.attempted_routes))
    if remaining == 0:
        return ()
    attempted = set(state.attempted_routes)
    candidates: set[str] = set()
    for route in discovered_routes:
        try:
            normalized = validate_huggingface_inference_provider(route)
        except ProviderConfigError:
            continue
        if normalized not in attempted:
            candidates.add(normalized)
    return tuple(sorted(candidates))[:remaining]


async def discover_huggingface_routes(
    model: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, ...]:
    """Discover live conversational provider suffixes for one logical model."""
    url = f"{HF_MODEL_API_BASE_URL}/{quote(model, safe='/')}"
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await active_client.get(
            url,
            params={"expand[]": "inferenceProviderMapping"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            await active_client.aclose()

    if not isinstance(payload, Mapping):
        return ()
    raw_mapping = payload.get("inferenceProviderMapping")
    if not isinstance(raw_mapping, Mapping):
        return ()
    routes: set[str] = set()
    for raw_route, raw_details in raw_mapping.items():
        if not isinstance(raw_route, str) or not isinstance(raw_details, Mapping):
            continue
        if raw_details.get("status") != "live" or raw_details.get("task") != "conversational":
            continue
        try:
            routes.add(validate_huggingface_inference_provider(raw_route))
        except ProviderConfigError:
            continue
    return tuple(sorted(routes))


def _request_context_fingerprint(
    *,
    system: str,
    messages: Sequence[AgentMessage],
    tools: Sequence[AgentTool],
) -> RequestContextFingerprint:
    static_payload = {
        "system": system,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            }
            for tool in tools
        ],
    }
    return RequestContextFingerprint(
        static_digest=_json_digest(static_payload),
        message_count=len(messages),
        message_digest=_message_digest(messages),
    )


def _context_extends(
    previous: RequestContextFingerprint,
    *,
    system: str,
    messages: Sequence[AgentMessage],
    tools: Sequence[AgentTool],
) -> bool:
    current = _request_context_fingerprint(system=system, messages=messages, tools=tools)
    return (
        current.static_digest == previous.static_digest
        and current.message_count > previous.message_count
        and _message_digest(messages[: previous.message_count]) == previous.message_digest
    )


def _message_digest(messages: Sequence[AgentMessage]) -> str:
    return _json_digest(
        [message.model_dump(mode="json", by_alias=True, exclude_none=False) for message in messages]
    )


def _json_digest(value: object) -> str:
    encoded = dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return values if value in values else (*values, value)


def _budget_exhaustion_update(
    state: HuggingFaceRoutingState,
    *,
    eligible_requests: int,
) -> dict[str, object]:
    if len(state.attempted_routes) >= HF_CACHE_MAX_CANDIDATES:
        if eligible_requests >= HF_CACHE_MAX_ELIGIBLE_REQUESTS:
            return {
                "phase": "exhausted",
                "last_reason": "candidate budget and eligible request budget exhausted",
            }
    elif eligible_requests >= HF_CACHE_MAX_ELIGIBLE_REQUESTS:
        return {"phase": "exhausted", "last_reason": "eligible request budget exhausted"}
    return {}
