"""Safe, user-level state for the trusted llama.cpp integration.

Only endpoint-keyed discovery metadata lives here.  Credentials are kept in
Tau's credential store and are referenced by an opaque generation name.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tau_coding.paths import TauPaths

LLAMA_CPP_STATE_SCHEMA_VERSION = 1
LLAMA_CPP_CREDENTIAL_PREFIX = "llama.cpp:"


class LlamaCppStateError(RuntimeError):
    """Raised for an unreadable or unsupported llama.cpp state file."""


@dataclass(frozen=True, slots=True)
class LlamaCppStoredModel:
    """Allowlisted model metadata safe to retain outside the server."""

    id: str
    display_name: str | None = None
    context_window: int | None = None
    input_modalities: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or self.id != self.id.strip():
            raise LlamaCppStateError("Stored llama.cpp model id must be a non-empty exact string")
        if self.display_name is not None and (
            not isinstance(self.display_name, str) or not self.display_name.strip()
        ):
            raise LlamaCppStateError("Stored llama.cpp display name must be non-empty")
        if self.context_window is not None and (
            not isinstance(self.context_window, int)
            or isinstance(self.context_window, bool)
            or self.context_window <= 0
        ):
            raise LlamaCppStateError("Stored context window must be a positive integer")
        if self.input_modalities is not None:
            if not isinstance(self.input_modalities, tuple) or not self.input_modalities:
                raise LlamaCppStateError("Stored input modalities must be a non-empty tuple")
            if any(item not in {"text", "image"} for item in self.input_modalities):
                raise LlamaCppStateError("Stored input modalities are unsupported")
            if len(set(self.input_modalities)) != len(self.input_modalities):
                raise LlamaCppStateError("Stored input modalities must be unique")

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"id": self.id}
        if self.display_name is not None:
            value["display_name"] = self.display_name
        if self.context_window is not None:
            value["context_window"] = self.context_window
        if self.input_modalities is not None:
            value["input_modalities"] = list(self.input_modalities)
        return value


@dataclass(frozen=True, slots=True)
class LlamaCppIntegrationState:
    """One endpoint's safe integration snapshot."""

    endpoint: str
    selected_model: str | None = None
    credential_ref: str | None = None
    models: tuple[LlamaCppStoredModel, ...] = ()
    checked_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, str) or not self.endpoint.strip():
            raise LlamaCppStateError("Llama.cpp state endpoint must be non-empty")
        if self.selected_model is not None and (
            not isinstance(self.selected_model, str)
            or not self.selected_model.strip()
            or self.selected_model != self.selected_model.strip()
        ):
            raise LlamaCppStateError(
                "Selected llama.cpp model must be a non-empty exact string or None"
            )
        if self.credential_ref is not None and not _valid_credential_ref(self.credential_ref):
            raise LlamaCppStateError("Credential reference is not owned by llama.cpp")
        if not isinstance(self.models, tuple) or any(
            not isinstance(model, LlamaCppStoredModel) for model in self.models
        ):
            raise LlamaCppStateError("Llama.cpp state models are malformed")
        ids = [model.id for model in self.models]
        if len(ids) != len(set(ids)):
            raise LlamaCppStateError("Llama.cpp state model ids must be unique")
        # Keep a selected reference even when a later discovery no longer
        # reports that model. The reference is not an availability claim: it
        # lets a cached/offline resume recover the exact model if the server
        # reports it again, while the provider layer keeps it unavailable until
        # then.
        if self.checked_at is not None and not isinstance(self.checked_at, str):
            raise LlamaCppStateError("Checked timestamp must be a string or None")

    def to_json(self) -> dict[str, object]:
        return {
            "endpoint": self.endpoint,
            "selected_model": self.selected_model,
            "credential_ref": self.credential_ref,
            "models": [model.to_json() for model in self.models],
            "checked_at": self.checked_at,
        }


class LlamaCppStateStore:
    """Locked and atomically replaced endpoint-keyed integration state."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        lock_path: Path | None = None,
        paths: TauPaths | None = None,
    ) -> None:
        resolved_paths = paths or TauPaths()
        self.path = path or resolved_paths.llama_cpp_state_path
        self.lock_path = lock_path or self.path.with_name(f"{self.path.name}.lock")

    def get(self, endpoint: str) -> LlamaCppIntegrationState | None:
        with self._locked():
            _, endpoints = self._read_unlocked()
            return endpoints.get(endpoint)

    def active(self) -> LlamaCppIntegrationState | None:
        with self._locked():
            active_endpoint, endpoints = self._read_unlocked()
            return endpoints.get(active_endpoint) if active_endpoint else None

    def all(self) -> tuple[LlamaCppIntegrationState, ...]:
        with self._locked():
            _, endpoints = self._read_unlocked()
            return tuple(endpoints.values())

    def save(
        self,
        state: LlamaCppIntegrationState,
        *,
        replace_endpoint: str | None = None,
    ) -> None:
        """Publish one endpoint snapshot and make it the saved endpoint.

        ``replace_endpoint`` lets configuration replace the prior active
        endpoint in the same atomic state-file transaction. Discovery updates
        omit it so a caller can retain endpoint-keyed snapshots when desired.
        """
        with self._locked():
            active_endpoint, endpoints = self._read_unlocked()
            del active_endpoint
            if replace_endpoint is not None and replace_endpoint != state.endpoint:
                endpoints.pop(replace_endpoint, None)
            endpoints[state.endpoint] = state
            self._write_unlocked(state.endpoint, endpoints)

    def remove(self, endpoint: str) -> tuple[str, ...]:
        """Remove one endpoint and return credential refs no longer referenced."""
        with self._locked():
            active_endpoint, endpoints = self._read_unlocked()
            before = _credential_refs(endpoints.values())
            endpoints.pop(endpoint, None)
            next_active = active_endpoint if active_endpoint != endpoint else None
            self._write_unlocked(next_active, endpoints)
            return tuple(sorted(before - _credential_refs(endpoints.values())))

    def clear(self) -> tuple[str, ...]:
        """Remove all integration settings, returning referenced credentials."""
        with self._locked():
            _, endpoints = self._read_unlocked()
            refs = tuple(sorted(_credential_refs(endpoints.values())))
            if self.path.exists():
                self.path.unlink()
                _fsync_directory(self.path.parent)
            return refs

    def referenced_credentials(self) -> frozenset[str]:
        with self._locked():
            _, endpoints = self._read_unlocked()
            return frozenset(_credential_refs(endpoints.values()))

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.lock_path.parent != self.path.parent:
            self.lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir(mode=...) does not tighten an already-existing directory. The
        # state directory is user-private even when it was created earlier by
        # another Tau process.
        with suppress(OSError):
            self.path.parent.chmod(0o700)
        if self.lock_path.parent != self.path.parent:
            with suppress(OSError):
                self.lock_path.parent.chmod(0o700)
        try:
            with self.lock_path.open("a+b") as handle:
                self.lock_path.chmod(0o600)
                _lock(handle)
                try:
                    _remove_temporary_files(self.path)
                    yield
                finally:
                    _unlock(handle)
        except LlamaCppStateError:
            raise
        except OSError as exc:
            raise LlamaCppStateError(f"Could not access llama.cpp state: {exc}") from exc

    def _read_unlocked(
        self,
    ) -> tuple[str | None, dict[str, LlamaCppIntegrationState]]:
        if not self.path.exists():
            return None, {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LlamaCppStateError(f"Could not read llama.cpp state: {exc}") from exc
        if not isinstance(payload, dict):
            raise LlamaCppStateError("Llama.cpp state must be a JSON object")

        # Accept the early single-endpoint shape from the accepted plan.  It is
        # read-only compatible and is upgraded only on the next intentional save.
        if "endpoints" not in payload and "endpoint" in payload:
            allowed = {
                "schema_version",
                "endpoint",
                "selected_model",
                "credential_ref",
                "models",
                "checked_at",
            }
            if set(payload) - allowed or payload.get("schema_version") != 1:
                raise LlamaCppStateError("Unsupported llama.cpp state schema")
            state = _state_from_json(
                {key: value for key, value in payload.items() if key != "schema_version"}
            )
            return state.endpoint, {state.endpoint: state}

        if set(payload) - {"schema_version", "active_endpoint", "endpoints"}:
            raise LlamaCppStateError("Unknown field in llama.cpp state")
        if payload.get("schema_version") != LLAMA_CPP_STATE_SCHEMA_VERSION:
            raise LlamaCppStateError("Unsupported llama.cpp state schema version")
        active_endpoint = payload.get("active_endpoint")
        endpoints_raw = payload.get("endpoints")
        if active_endpoint is not None and not isinstance(active_endpoint, str):
            raise LlamaCppStateError("Active llama.cpp endpoint is malformed")
        if not isinstance(endpoints_raw, dict):
            raise LlamaCppStateError("Llama.cpp endpoints must be an object")
        endpoints: dict[str, LlamaCppIntegrationState] = {}
        for key, raw in endpoints_raw.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(raw, dict):
                raise LlamaCppStateError("Llama.cpp endpoint state is malformed")
            state = _state_from_json(raw)
            if key != state.endpoint:
                raise LlamaCppStateError("Llama.cpp endpoint key does not match its state")
            endpoints[key] = state
        if active_endpoint is not None and active_endpoint not in endpoints:
            raise LlamaCppStateError("Active llama.cpp endpoint is not stored")
        return active_endpoint, endpoints

    def _write_unlocked(
        self,
        active_endpoint: str | None,
        endpoints: Mapping[str, LlamaCppIntegrationState],
    ) -> None:
        payload = {
            "schema_version": LLAMA_CPP_STATE_SCHEMA_VERSION,
            "active_endpoint": active_endpoint,
            "endpoints": {
                endpoint: state.to_json() for endpoint, state in sorted(endpoints.items())
            },
        }
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary: Path | None = None
        fd = -1
        try:
            fd, raw = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(raw)
            temporary.chmod(0o600)
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self.path.chmod(0o600)
            _fsync_directory(self.path.parent)
        except OSError as exc:
            raise LlamaCppStateError(f"Could not write llama.cpp state: {exc}") from exc
        finally:
            if fd >= 0:
                os.close(fd)
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink()


def _state_from_json(raw: Mapping[str, object]) -> LlamaCppIntegrationState:
    allowed = {"endpoint", "selected_model", "credential_ref", "models", "checked_at"}
    if set(raw) - allowed:
        raise LlamaCppStateError("Unknown field in llama.cpp endpoint state")
    endpoint = raw.get("endpoint")
    models_raw = raw.get("models", [])
    if not isinstance(endpoint, str) or not isinstance(models_raw, list):
        raise LlamaCppStateError("Malformed llama.cpp endpoint state")
    if not isinstance(raw.get("selected_model"), (str, type(None))):
        raise LlamaCppStateError("Malformed selected llama.cpp model")
    if not isinstance(raw.get("credential_ref"), (str, type(None))):
        raise LlamaCppStateError("Malformed llama.cpp credential reference")
    models = tuple(_model_from_json(item) for item in models_raw)
    return LlamaCppIntegrationState(
        endpoint=endpoint,
        selected_model=cast(str | None, raw.get("selected_model")),
        credential_ref=cast(str | None, raw.get("credential_ref")),
        models=models,
        checked_at=cast(str | None, raw.get("checked_at")),
    )


def _model_from_json(raw: object) -> LlamaCppStoredModel:
    if not isinstance(raw, dict):
        raise LlamaCppStateError("Malformed stored llama.cpp model")
    allowed = {"id", "display_name", "context_window", "input_modalities"}
    if set(raw) - allowed:
        raise LlamaCppStateError("Unknown field in stored llama.cpp model")
    modalities = raw.get("input_modalities")
    if modalities is not None and not isinstance(modalities, list):
        raise LlamaCppStateError("Malformed stored input modalities")
    return LlamaCppStoredModel(
        id=cast(str, raw.get("id")),
        display_name=cast(str | None, raw.get("display_name")),
        context_window=cast(int | None, raw.get("context_window")),
        input_modalities=tuple(modalities) if modalities is not None else None,
    )


def _credential_refs(states: Iterable[LlamaCppIntegrationState]) -> set[str]:
    return {state.credential_ref for state in states if state.credential_ref}


def _valid_credential_ref(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(LLAMA_CPP_CREDENTIAL_PREFIX):
        return False
    suffix = value.removeprefix(LLAMA_CPP_CREDENTIAL_PREFIX)
    # secrets.token_urlsafe() emits this conservative alphabet. Reject path
    # separators and control characters so state can never name an unrelated
    # credential or become a path-like injection surface.
    return bool(suffix) and all(character.isalnum() or character in "-_" for character in suffix)


def _remove_temporary_files(path: Path) -> None:
    """Remove only this store's interrupted atomic-write artifacts."""
    pattern = f".{path.name}.*.tmp"
    for temporary in path.parent.glob(pattern):
        with suppress(OSError):
            temporary.unlink()


def _lock(handle: object) -> None:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
    except ImportError:
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
    except OSError as exc:
        raise LlamaCppStateError(f"Could not lock llama.cpp state: {exc}") from exc


def _unlock(handle: object) -> None:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except ImportError:
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]


def _fsync_directory(path: Path) -> None:
    with suppress(OSError):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "LLAMA_CPP_CREDENTIAL_PREFIX",
    "LLAMA_CPP_STATE_SCHEMA_VERSION",
    "LlamaCppIntegrationState",
    "LlamaCppStateError",
    "LlamaCppStateStore",
    "LlamaCppStoredModel",
]
