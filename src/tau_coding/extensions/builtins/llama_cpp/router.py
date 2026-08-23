"""Pinned llama.cpp router protocol adapter.

The mutating API is used only after ``/props`` proves both router identity and
a tested build. Unknown builds intentionally fall back to OpenAI-compatible
model discovery in the owning service.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

import httpx

LLAMA_CPP_ROUTER_MIN_BUILD = 9688
LLAMA_CPP_ROUTER_MAX_BUILD = 10595

RouterState = Literal[
    "loaded", "sleeping", "unloaded", "loading", "downloading", "failed", "unknown"
]


class LlamaCppRouterError(RuntimeError):
    """A malformed or failed request to a confirmed compatible router."""


@dataclass(frozen=True, slots=True)
class RouterCapability:
    role: Literal["standard", "compatible", "incompatible"]
    build: int | None = None
    diagnostic: str | None = None

    @property
    def compatible(self) -> bool:
        return self.role == "compatible"


@dataclass(frozen=True, slots=True)
class RouterModel:
    id: str
    state: RouterState
    display_name: str | None = None
    input_modalities: tuple[Literal["text", "image"], ...] | None = None
    failed: bool = False


async def detect_router(
    client: httpx.AsyncClient,
    server_root: str,
    headers: Mapping[str, str],
) -> RouterCapability:
    """Identify only the documented router and gate it to the tested builds."""
    response = await client.get(server_root + "/props", headers=dict(headers))
    if response.status_code == 404:
        return RouterCapability("standard")
    _raise_http(response, "detecting router capabilities")
    payload = _object(response, "/props")
    if payload.get("role") != "router":
        return RouterCapability("standard")
    build_info = payload.get("build_info")
    match = re.match(r"^b(\d+)(?:-|$)", build_info) if isinstance(build_info, str) else None
    if match is None:
        return RouterCapability(
            "incompatible",
            diagnostic=(
                "Router build is unknown; management is disabled and Tau is using "
                "standard OpenAI-compatible discovery."
            ),
        )
    build = int(match.group(1))
    if not LLAMA_CPP_ROUTER_MIN_BUILD <= build <= LLAMA_CPP_ROUTER_MAX_BUILD:
        return RouterCapability(
            "incompatible",
            build,
            (
                f"Router build b{build} is outside Tau's tested range "
                f"b{LLAMA_CPP_ROUTER_MIN_BUILD}-b{LLAMA_CPP_ROUTER_MAX_BUILD}; "
                "management is disabled."
            ),
        )
    return RouterCapability("compatible", build)


async def list_router_models(
    client: httpx.AsyncClient,
    server_root: str,
    headers: Mapping[str, str],
    *,
    reload: bool = False,
) -> tuple[RouterModel, ...]:
    response = await client.get(
        server_root + "/models",
        params={"reload": "1"} if reload else None,
        headers=dict(headers),
    )
    _raise_http(response, "listing router models")
    payload = _object(response, "/models")
    data = payload.get("data")
    if not isinstance(data, list):
        raise LlamaCppRouterError("llama.cpp /models returned a malformed model list.")
    result: list[RouterModel] = []
    ids: set[str] = set()
    for raw in data:
        if not isinstance(raw, Mapping):
            raise LlamaCppRouterError("llama.cpp /models returned a malformed model.")
        model_id = raw.get("id")
        if not isinstance(model_id, str) or not model_id or model_id != model_id.strip():
            raise LlamaCppRouterError("llama.cpp /models returned a model without an exact id.")
        if model_id in ids:
            raise LlamaCppRouterError("llama.cpp /models returned duplicate model ids.")
        ids.add(model_id)
        status = raw.get("status")
        value = status.get("value") if isinstance(status, Mapping) else None
        failed = bool(status.get("failed")) if isinstance(status, Mapping) else False
        state: RouterState
        if failed:
            state = "failed"
        elif value in {"loaded", "sleeping", "unloaded", "loading", "downloading"}:
            state = cast(RouterState, value)
        else:
            state = "unknown"
        architecture = raw.get("architecture")
        modalities_raw = (
            architecture.get("input_modalities") if isinstance(architecture, Mapping) else None
        )
        modalities = None
        if (
            isinstance(modalities_raw, list)
            and modalities_raw
            and all(item in {"text", "image"} for item in modalities_raw)
        ):
            modalities = cast(
                tuple[Literal["text", "image"], ...], tuple(dict.fromkeys(modalities_raw))
            )
        display = raw.get("name", raw.get("display_name"))
        result.append(
            RouterModel(
                model_id,
                state,
                display if isinstance(display, str) and display.strip() else model_id,
                modalities,
                failed,
            )
        )
    return tuple(result)


async def mutate_router_model(
    client: httpx.AsyncClient,
    server_root: str,
    headers: Mapping[str, str],
    *,
    action: Literal["load", "unload", "download"],
    model_id: str,
) -> None:
    endpoint = "/models" if action == "download" else f"/models/{action}"
    response = await client.post(
        server_root + endpoint,
        headers=dict(headers),
        json={"model": model_id},
    )
    _raise_http(response, f"requesting model {action}")
    payload = _object(response, endpoint)
    if payload.get("success") is not True:
        raise LlamaCppRouterError(f"llama.cpp did not accept the model {action} request.")


def _object(response: httpx.Response, endpoint: str) -> Mapping[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise LlamaCppRouterError(f"llama.cpp {endpoint} returned malformed JSON.") from exc
    if not isinstance(payload, Mapping):
        raise LlamaCppRouterError(f"llama.cpp {endpoint} returned malformed JSON.")
    return payload


def _raise_http(response: httpx.Response, operation: str) -> None:
    if response.status_code in {401, 403}:
        raise LlamaCppRouterError(
            "llama.cpp rejected the router request. Check the optional API key or LLAMA_API_KEY."
        )
    if response.status_code >= 400:
        detail = _server_error_detail(response)
        suffix = f": {detail}" if detail else "."
        raise LlamaCppRouterError(
            f"llama.cpp returned HTTP {response.status_code} while {operation}{suffix}"
        )


def _server_error_detail(response: httpx.Response) -> str | None:
    """Extract one bounded, user-actionable message from a router error."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    message = error.get("message") if isinstance(error, Mapping) else None
    if not isinstance(message, str):
        return None
    normalized = " ".join(message.split())
    if not normalized:
        return None
    return normalized[:300]


__all__ = [
    "LLAMA_CPP_ROUTER_MAX_BUILD",
    "LLAMA_CPP_ROUTER_MIN_BUILD",
    "LlamaCppRouterError",
    "RouterCapability",
    "RouterModel",
    "RouterState",
    "detect_router",
    "list_router_models",
    "mutate_router_model",
]
