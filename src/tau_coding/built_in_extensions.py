"""Trusted extension declarations bundled with Tau."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau_coding.extensions.api import ExtensionAPI

BuiltInExtensionSetup = Callable[["ExtensionAPI"], None]


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

    @property
    def source_id(self) -> str:
        """Return the stable host-owned source identity for this declaration."""
        return f"built-in:{self.name}"


# Product capabilities are added here by later phases. Keeping this registry
# explicit makes built-in code reviewable and prevents filesystem discovery or
# project inputs from changing what Tau treats as trusted.
BUILT_IN_EXTENSIONS: tuple[BuiltInExtension, ...] = ()

__all__ = ["BUILT_IN_EXTENSIONS", "BuiltInExtension", "BuiltInExtensionSetup"]
