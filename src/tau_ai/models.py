"""Model discovery for OpenAI-compatible providers.

Some OpenAI-compatible endpoints (for example Nebius Token Factory) expose a
``GET /v1/models`` listing that can be expanded with a ``verbose`` query
parameter. Tau uses this to populate a provider's model list dynamically at
build time instead of hardcoding a catalog.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from json import loads
from typing import Any

import httpx

from tau_ai.env import OpenAICompatibleConfig


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """One model advertised by an OpenAI-compatible ``/models`` endpoint."""

    id: str
    context_window: int | None = None


async def list_openai_compatible_models(
    config: OpenAICompatibleConfig,
    *,
    verbose: bool = False,
    client: httpx.AsyncClient | None = None,
) -> tuple[ModelInfo, ...]:
    """List models from an OpenAI-compatible ``/models`` endpoint.

    When ``verbose`` is true, the ``verbose=true`` query parameter is sent so
    providers that support it (such as Nebius Token Factory) return the full
    model catalog with metadata. The response is parsed tolerantly: only the
    ``id`` of each entry in ``data`` is required, and an optional integer
    context-window field is extracted when present.
    """
    headers: dict[str, str] = {**(dict(config.headers or {}))}
    headers.setdefault("Authorization", f"Bearer {config.api_key}")
    params: dict[str, str] = {}
    if verbose:
        params["verbose"] = "true"
    url = f"{config.base_url.rstrip('/')}/models"

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=config.timeout_seconds)
    try:
        response = await http_client.get(url, headers=headers, params=params)
        response.raise_for_status()
        payload = loads(response.content)
    finally:
        if owns_client:
            await http_client.aclose()

    data = _data_array(payload)
    models: list[ModelInfo] = []
    seen: set[str] = set()
    for entry in data:
        model_id = _model_id(entry)
        if model_id is None or model_id in seen:
            continue
        seen.add(model_id)
        models.append(ModelInfo(id=model_id, context_window=_context_window(entry)))
    return tuple(models)


def _data_array(payload: object) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, Mapping)]


def _model_id(entry: Mapping[str, Any]) -> str | None:
    model_id = entry.get("id")
    if isinstance(model_id, str) and model_id.strip():
        return model_id.strip()
    return None


def _context_window(entry: Mapping[str, Any]) -> int | None:
    for field_name in ("context_window", "context_length", "max_context_length"):
        value = entry.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None
