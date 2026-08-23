"""Hugging Face GGUF discovery for llama.cpp server-side downloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

HF_API_ROOT = "https://huggingface.co/api"
HF_TOKEN_ENV = "HF_TOKEN"
_QUANTIZATION = re.compile(
    r"(?:^|[-_.])((?:IQ|Q)\d(?:_[A-Z0-9]+)+|F(?:16|32)|BF16)(?:[-_.]|$)", re.IGNORECASE
)
_EXACT_REPOSITORY = re.compile(r"^[^\s/:]+/[^\s/:]+(?::[^\s:]+)?$")


class HuggingFaceSearchError(RuntimeError):
    """A secret-free Hugging Face search/details failure."""


@dataclass(frozen=True, slots=True)
class GgufVariant:
    quantization: str
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class GgufRepository:
    id: str
    gated: bool
    variants: tuple[GgufVariant, ...]


def discover_hf_token(
    environment: Mapping[str, str],
    *,
    token_paths: Sequence[Path] | None = None,
) -> str | None:
    """Read a search-only token from environment or standard HF token files."""
    token = environment.get(HF_TOKEN_ENV, "").strip()
    if token:
        return token
    paths = tuple(token_paths) if token_paths is not None else _standard_token_paths(environment)
    for path in paths:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if value:
            return value
    return None


async def search_gguf_repositories(
    client: httpx.AsyncClient,
    query: str,
    *,
    token: str | None,
    limit: int = 10,
) -> tuple[GgufRepository, ...]:
    """Search repositories, or inspect one exact owner/repository reference."""
    normalized = query.strip()
    exact = normalized.rsplit(":", 1)[0] if _EXACT_REPOSITORY.fullmatch(normalized) else None
    if exact is not None:
        return (await repository_details(client, exact, token=token),)
    response = await client.get(
        HF_API_ROOT + "/models",
        params={"search": normalized, "filter": "gguf", "limit": str(limit)},
        headers=_headers(token),
    )
    _raise_http(response, "searching Hugging Face")
    payload = _json(response)
    if not isinstance(payload, list):
        raise HuggingFaceSearchError("Hugging Face search returned malformed results.")
    repositories: list[GgufRepository] = []
    for raw in payload:
        if not isinstance(raw, Mapping):
            continue
        repo_id = raw.get("id", raw.get("modelId"))
        if not isinstance(repo_id, str) or repo_id.count("/") != 1:
            continue
        repositories.append(await repository_details(client, repo_id, token=token))
    return tuple(repositories)


async def repository_details(
    client: httpx.AsyncClient,
    repository: str,
    *,
    token: str | None,
) -> GgufRepository:
    response = await client.get(
        f"{HF_API_ROOT}/models/{repository}",
        headers=_headers(token),
    )
    _raise_http(response, f"reading Hugging Face repository {repository}")
    payload = _json(response)
    if not isinstance(payload, Mapping):
        raise HuggingFaceSearchError("Hugging Face repository details were malformed.")
    repo_id = payload.get("id", payload.get("modelId", repository))
    if not isinstance(repo_id, str) or repo_id.count("/") != 1:
        raise HuggingFaceSearchError("Hugging Face repository details omitted an exact id.")
    gated_raw = payload.get("gated", False)
    gated = gated_raw is True or (
        isinstance(gated_raw, str) and gated_raw.casefold() not in {"", "false", "none"}
    )
    totals: dict[str, int | None] = {}
    siblings = payload.get("siblings", ())
    if isinstance(siblings, list):
        for sibling in siblings:
            if not isinstance(sibling, Mapping):
                continue
            filename = sibling.get("rfilename", sibling.get("path"))
            if not isinstance(filename, str) or not filename.casefold().endswith(".gguf"):
                continue
            match = _QUANTIZATION.search(Path(filename).name)
            if match is None:
                continue
            quantization = match.group(1).upper()
            size = _file_size(sibling)
            prior = totals.get(quantization)
            totals[quantization] = (
                None
                if size is None or (quantization in totals and prior is None)
                else (prior or 0) + size
            )
    variants = tuple(
        GgufVariant(quantization, size)
        for quantization, size in sorted(totals.items(), key=lambda item: item[0])
    )
    return GgufRepository(repo_id, gated, variants)


def _file_size(raw: Mapping[str, object]) -> int | None:
    size = raw.get("size")
    lfs = raw.get("lfs")
    if not isinstance(size, int) and isinstance(lfs, Mapping):
        size = lfs.get("size")
    return size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None


def _headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError as exc:
        raise HuggingFaceSearchError("Hugging Face returned malformed JSON.") from exc


def _raise_http(response: httpx.Response, operation: str) -> None:
    if response.status_code in {401, 403}:
        raise HuggingFaceSearchError(
            "Hugging Face denied access. Accept the repository terms and provide HF_TOKEN "
            "for Tau search; the independent llama.cpp server also needs its own HF_TOKEN "
            "for gated downloads."
        )
    if response.status_code == 429:
        raise HuggingFaceSearchError("Hugging Face rate limited the search. Wait and retry.")
    if response.status_code >= 400:
        raise HuggingFaceSearchError(
            f"Hugging Face returned HTTP {response.status_code} while {operation}."
        )


def _standard_token_paths(environment: Mapping[str, str]) -> tuple[Path, ...]:
    home = Path(environment.get("HOME") or Path.home())
    hf_home = Path(environment.get("HF_HOME") or home / ".cache" / "huggingface")
    return (hf_home / "token", home / ".huggingface" / "token")


__all__ = [
    "GgufRepository",
    "GgufVariant",
    "HF_API_ROOT",
    "HF_TOKEN_ENV",
    "HuggingFaceSearchError",
    "discover_hf_token",
    "repository_details",
    "search_gguf_repositories",
]
