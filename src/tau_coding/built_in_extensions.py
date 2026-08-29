"""Trusted extension declarations bundled with Tau."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING

import httpx2

from tau_coding.credentials import CredentialStore
from tau_coding.paths import TauPaths

if TYPE_CHECKING:
    from tau_coding.extensions.api import ExtensionAPI


@dataclass(frozen=True, slots=True)
class BuiltInExtensionContext:
    """Trusted runtime dependencies supplied only to bundled extensions.

    Filesystem extensions receive the ordinary :class:`ExtensionAPI` only. The
    context exists so a bundled integration uses the session's Tau home,
    credential store, environment snapshot, and deterministic HTTP client
    rather than silently reaching process-global defaults.
    """

    paths: TauPaths
    credential_store: CredentialStore
    environment: Mapping[str, str]
    http_client: httpx2.AsyncClient | None = None


BuiltInExtensionSetup = Callable[["ExtensionAPI"], None]
BuiltInExtensionContextSetup = Callable[["ExtensionAPI", BuiltInExtensionContext], None]


@dataclass(frozen=True, slots=True)
class BuiltInExtension:
    """One trusted extension setup function shipped as part of Tau.

    Built-ins use the normal extension API and runtime lifecycle. They differ
    only in provenance: Tau declares their callable directly, loads them before
    filesystem extensions, and may hide them from ordinary extension listings.
    """

    name: str
    setup: BuiltInExtensionSetup
    hidden: bool = True
    setup_with_context: BuiltInExtensionContextSetup | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Built-in extension name must be a non-empty string")
        if self.name != self.name.strip():
            raise ValueError("Built-in extension name must not have surrounding whitespace")
        if not callable(self.setup):
            raise ValueError("Built-in extension setup must be callable")
        setup_call = type(self.setup).__call__
        if iscoroutinefunction(self.setup) or iscoroutinefunction(setup_call):
            raise ValueError("Built-in extension setup must be a sync function")
        if not isinstance(self.hidden, bool):
            raise ValueError("Built-in extension hidden flag must be a boolean")
        if self.setup_with_context is not None:
            if not callable(self.setup_with_context):
                raise ValueError("Built-in contextual setup must be callable")
            context_setup_call = type(self.setup_with_context).__call__
            if iscoroutinefunction(self.setup_with_context) or iscoroutinefunction(
                context_setup_call
            ):
                raise ValueError("Built-in contextual setup must be a sync function")

    @property
    def source_id(self) -> str:
        """Return the stable host-owned source identity for this declaration."""
        return f"built-in:{self.name}"


def _llama_cpp_setup(api: ExtensionAPI) -> None:
    """Load the product extension with its normal process dependencies."""
    from tau_coding.extensions.builtins.llama_cpp import setup

    setup(api)


def _llama_cpp_setup_with_context(
    api: ExtensionAPI,
    context: BuiltInExtensionContext,
) -> None:
    """Load the product extension lazily with trusted runtime dependencies."""
    from tau_coding.extensions.builtins.llama_cpp import setup

    setup(api, context)


# Product capabilities are declared explicitly so filesystem discovery or
# project inputs cannot change what Tau treats as trusted.
BUILT_IN_EXTENSIONS: tuple[BuiltInExtension, ...] = (
    BuiltInExtension(
        name="llama.cpp",
        setup=_llama_cpp_setup,
        hidden=True,
        setup_with_context=_llama_cpp_setup_with_context,
    ),
)

__all__ = [
    "BUILT_IN_EXTENSIONS",
    "BuiltInExtension",
    "BuiltInExtensionContext",
    "BuiltInExtensionContextSetup",
    "BuiltInExtensionSetup",
]
