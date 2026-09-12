"""Pluggable session storage resolution for coding sessions.

`tau_agent` defines the `SessionStorage` protocol and `tau_coding` defaults to
local JSONL files. This module is the seam between the two: it decides which
storage backend a coding session uses, without adding any backend-specific
code or dependencies to Tau itself.

Resolution order for `create_session_storage(record)`:

1. An explicit factory argument (embedders wiring things up in Python).
2. The `TAU_SESSION_STORAGE` environment variable, a `package.module:attribute`
   spec naming a callable that takes a `CodingSessionRecord` and returns a
   `SessionStorage` (external packages plugging into the stock CLI/TUI).
3. The default: `JsonlSessionStorage(record.path)` — existing behavior,
   unchanged when nothing is configured.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from typing import cast

from tau_agent.session import JsonlSessionStorage, SessionStorage
from tau_coding.session_manager import CodingSessionRecord

SESSION_STORAGE_ENV_VAR = "TAU_SESSION_STORAGE"
"""Environment variable holding a `package.module:attribute` factory spec."""

type SessionStorageFactory = Callable[[CodingSessionRecord], SessionStorage]
"""Builds a SessionStorage for one coding-session record.

The record provides a stable `id` to key the session on, plus `cwd`, `path`,
and metadata. Factories are free to ignore `path` — it only means something to
the JSONL backend.
"""


class SessionStorageResolutionError(RuntimeError):
    """Raised when a configured session storage factory cannot be loaded."""


def load_session_storage_factory(spec: str) -> SessionStorageFactory:
    """Load a session storage factory from a `package.module:attribute` spec.

    The attribute part may be dotted (`module:Class.method`). Raises
    `SessionStorageResolutionError` with a pointed message when the spec is
    malformed, the module cannot be imported, the attribute is missing, or the
    resolved object is not callable.
    """
    module_name, separator, attribute_path = spec.partition(":")
    module_name = module_name.strip()
    attribute_path = attribute_path.strip()
    if not separator or not module_name or not attribute_path:
        raise SessionStorageResolutionError(
            f"Invalid session storage spec {spec!r}: expected the form 'package.module:attribute'."
        )

    try:
        target: object = importlib.import_module(module_name)
    except ImportError as error:
        raise SessionStorageResolutionError(
            f"Could not import session storage module {module_name!r} "
            f"from {SESSION_STORAGE_ENV_VAR}: {error}"
        ) from error

    for part in attribute_path.split("."):
        try:
            target = getattr(target, part)
        except AttributeError as error:
            raise SessionStorageResolutionError(
                f"Session storage module {module_name!r} has no attribute {attribute_path!r}."
            ) from error

    if not callable(target):
        raise SessionStorageResolutionError(f"Session storage factory {spec!r} is not callable.")
    return cast("SessionStorageFactory", target)


def resolve_session_storage_factory(
    env: Mapping[str, str] | None = None,
) -> SessionStorageFactory | None:
    """Return the factory configured via `TAU_SESSION_STORAGE`, if any.

    Returns `None` when the variable is unset or blank, which means "use the
    default JSONL storage". `env` exists for tests; production callers use the
    process environment.
    """
    variables = os.environ if env is None else env
    spec = variables.get(SESSION_STORAGE_ENV_VAR, "").strip()
    if not spec:
        return None
    return load_session_storage_factory(spec)


def create_session_storage(
    record: CodingSessionRecord,
    factory: SessionStorageFactory | None = None,
) -> SessionStorage:
    """Create the session storage for one coding-session record.

    Uses `factory` when provided, otherwise the `TAU_SESSION_STORAGE`
    environment factory, otherwise local JSONL at `record.path` (the existing
    default behavior).
    """
    active_factory = factory if factory is not None else resolve_session_storage_factory()
    if active_factory is None:
        return JsonlSessionStorage(record.path)
    return active_factory(record)
