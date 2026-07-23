"""Shared extension/provider startup ordering for TUI and print mode."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tau_coding.extensions import ExtensionRuntime
from tau_coding.provider_config import ProviderSettings, load_provider_settings
from tau_coding.resources import TauResourcePaths


@dataclass(frozen=True, slots=True)
class ProviderStartup:
    """Provider state prepared before startup model resolution."""

    resource_paths: TauResourcePaths
    extension_runtime: ExtensionRuntime
    durable_settings: ProviderSettings
    settings: ProviderSettings


async def prepare_provider_startup(
    *,
    cwd: Path,
    requested_provider: str | None,
    extension_paths: tuple[Path, ...] = (),
    extensions_enabled: bool = True,
    project_extensions_enabled: bool = False,
    settings_loader: Callable[[], ProviderSettings] = load_provider_settings,
) -> ProviderStartup:
    """Load explicit/trusted extensions, target-refresh, then compose overlays.

    Unrelated providers are not refreshed, so ordinary startup does not gain
    mandatory local-network access. Cached model snapshots registered by sync
    ``setup(tau)`` are available before the targeted refresh.
    """
    resource_paths = TauResourcePaths(cwd=cwd)
    runtime = ExtensionRuntime()
    if extensions_enabled or extension_paths:
        runtime.load(
            resource_paths,
            extra_paths=extension_paths,
            include_resource_dirs=extensions_enabled,
            include_project_dir=project_extensions_enabled,
        )
    if requested_provider is not None:
        await runtime.refresh_provider(requested_provider)
    durable = settings_loader()
    return ProviderStartup(
        resource_paths=resource_paths,
        extension_runtime=runtime,
        durable_settings=durable,
        settings=runtime.compose_provider_settings(durable),
    )
