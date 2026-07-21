"""Extension discovery and module loading."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from inspect import iscoroutinefunction
from pathlib import Path

from tau_coding.resources import ResourceDiagnostic, TauResourcePaths

EXTENSION_ENTRY_ATTRIBUTE = "setup"
_MODULE_NAME_PREFIX = "tau_extension"

_load_counter = 0


@dataclass(frozen=True, slots=True)
class DiscoveredExtension:
    """A discovered extension entry file before loading."""

    name: str
    path: Path
    package_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class LoadedExtension:
    """A successfully imported extension module and its entry point."""

    name: str
    path: Path
    setup: Callable[..., object]


@dataclass(frozen=True, slots=True)
class ExtensionLoadResult:
    """Loaded extensions plus non-fatal discovery/load diagnostics."""

    extensions: tuple[LoadedExtension, ...]
    diagnostics: tuple[ResourceDiagnostic, ...]


def extension_dirs(
    paths: TauResourcePaths,
    *,
    include_project_dir: bool = False,
) -> tuple[Path, ...]:
    """Return extension directories in load order (project first, then user).

    Unlike skills precedence, extension ordering follows Pi's rule: earlier
    directories win name conflicts, so project extensions shadow user ones.
    Project extensions are opt-in (`--project-extensions`) until Tau has a
    project trust store, because they execute at session startup.
    """
    dirs: list[Path] = []
    if include_project_dir and paths.cwd is not None:
        dirs.append(paths.cwd / ".tau" / "extensions")
    dirs.append(paths.root / "extensions")
    return tuple(_dedupe(dirs))


def discover_extensions(
    paths: TauResourcePaths,
    *,
    extra_paths: Sequence[Path] = (),
    include_resource_dirs: bool = True,
    include_project_dir: bool = False,
) -> tuple[tuple[DiscoveredExtension, ...], tuple[ResourceDiagnostic, ...]]:
    """Discover extension entry files.

    Discovery covers project and user extension directories plus explicitly
    passed paths (files or directories). Explicit paths always load, even when
    `include_resource_dirs` is False (the `--no-extensions` escape hatch).
    """
    discovered: list[DiscoveredExtension] = []
    diagnostics: list[ResourceDiagnostic] = []
    seen_paths: set[Path] = set()
    seen_names: set[str] = set()

    def add(entry: DiscoveredExtension) -> None:
        resolved = entry.path.resolve()
        if resolved in seen_paths:
            return
        if entry.name in seen_names:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    name=entry.name,
                    path=entry.path,
                    message="duplicate extension name ignored (first-loaded wins)",
                )
            )
            return
        seen_paths.add(resolved)
        seen_names.add(entry.name)
        discovered.append(entry)

    if include_resource_dirs:
        for directory in extension_dirs(paths, include_project_dir=include_project_dir):
            for entry in _discover_in_dir(directory, diagnostics):
                add(entry)

    for path in extra_paths:
        expanded = path.expanduser()
        if expanded.is_dir():
            manifest_entries = _manifest_entries(expanded, diagnostics)
            if manifest_entries:
                for entry in manifest_entries:
                    add(entry)
                continue
            entry_file = expanded / "extension.py"
            if entry_file.is_file():
                add(DiscoveredExtension(name=expanded.name, path=entry_file, package_dir=expanded))
                continue
            found_any = False
            for entry in _discover_in_dir(expanded, diagnostics):
                found_any = True
                add(entry)
            if not found_any:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="extension",
                        path=expanded,
                        message="no extensions found in explicit extension path",
                    )
                )
        elif expanded.is_file():
            add(DiscoveredExtension(name=expanded.stem, path=expanded))
        else:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    path=expanded,
                    message="explicit extension path does not exist",
                    severity="error",
                )
            )

    return tuple(discovered), tuple(diagnostics)


def load_extensions(
    paths: TauResourcePaths,
    *,
    extra_paths: Sequence[Path] = (),
    include_resource_dirs: bool = True,
    include_project_dir: bool = False,
) -> ExtensionLoadResult:
    """Discover and import extensions, isolating per-extension failures."""
    discovered, diagnostics = discover_extensions(
        paths,
        extra_paths=extra_paths,
        include_resource_dirs=include_resource_dirs,
        include_project_dir=include_project_dir,
    )
    loaded: list[LoadedExtension] = []
    all_diagnostics = list(diagnostics)
    for entry in discovered:
        extension, entry_diagnostics = _load_extension(entry)
        all_diagnostics.extend(entry_diagnostics)
        if extension is not None:
            loaded.append(extension)
    return ExtensionLoadResult(
        extensions=tuple(loaded),
        diagnostics=tuple(all_diagnostics),
    )


def _discover_in_dir(
    directory: Path, diagnostics: list[ResourceDiagnostic]
) -> Iterator[DiscoveredExtension]:
    if not directory.is_dir():
        return
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name.startswith(("_", ".")):
            continue
        if path.is_file() and path.suffix == ".py":
            yield DiscoveredExtension(name=path.stem, path=path)
        elif path.is_dir():
            manifest_entries = _manifest_entries(path, diagnostics)
            if manifest_entries:
                yield from manifest_entries
                continue
            entry_file = path / "extension.py"
            if entry_file.is_file():
                yield DiscoveredExtension(name=path.name, path=entry_file, package_dir=path)


def _manifest_entries(
    directory: Path, diagnostics: list[ResourceDiagnostic]
) -> list[DiscoveredExtension]:
    """Resolve entry files declared in `<dir>/pyproject.toml` under `[tool.tau]`.

    Pi's loader resolves a directory's `package.json` `pi.extensions` field
    before falling back to the `index.ts` convention ("complex packages must
    use package.json manifest"); `tool.tau.extensions` is the Python-shaped
    equivalent. An empty result means "no usable manifest" and callers fall
    back to the `extension.py` convention.
    """
    manifest = directory / "pyproject.toml"
    if not manifest.is_file():
        return []
    try:
        with manifest.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        diagnostics.append(
            ResourceDiagnostic(
                kind="extension",
                path=manifest,
                message=f"could not parse pyproject.toml: {exc}",
            )
        )
        return []
    tool_table = data.get("tool")
    tau_table = tool_table.get("tau") if isinstance(tool_table, dict) else None
    declared = tau_table.get("extensions") if isinstance(tau_table, dict) else None
    if declared is None:
        return []
    if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
        diagnostics.append(
            ResourceDiagnostic(
                kind="extension",
                path=manifest,
                message="`tool.tau.extensions` must be a list of file paths",
                severity="error",
            )
        )
        return []
    entries: list[DiscoveredExtension] = []
    for item in declared:
        entry_file = (directory / item).resolve()
        if not entry_file.is_file():
            diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    path=manifest,
                    message=f"declared extension entry does not exist: {item}",
                    severity="error",
                )
            )
            continue
        # Manifest entries always load as packages: the manifest exists to
        # point at structured layouts (e.g. src/<pkg>/extension.py), where
        # sibling modules must stay reachable through relative imports.
        name = entry_file.parent.name if entry_file.stem == "extension" else entry_file.stem
        entries.append(
            DiscoveredExtension(name=name, path=entry_file, package_dir=entry_file.parent)
        )
    return entries


def _load_extension(
    entry: DiscoveredExtension,
) -> tuple[LoadedExtension | None, list[ResourceDiagnostic]]:
    global _load_counter
    _load_counter += 1
    module_name = f"{_MODULE_NAME_PREFIX}_{_slugify(entry.name)}_{_load_counter}"

    # Directory extensions load as real packages so sibling modules are
    # reachable with relative imports and stay namespaced in sys.modules.
    search_locations = [str(entry.package_dir)] if entry.package_dir is not None else None
    spec = spec_from_file_location(
        module_name,
        entry.path,
        submodule_search_locations=search_locations,
    )
    if spec is None or spec.loader is None:
        return None, [_error_diagnostic(entry, f"could not create an import spec for {entry.path}")]

    module = module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:  # noqa: BLE001 - extensions are an isolation boundary
        del sys.modules[module_name]
        return None, [_error_diagnostic(entry, f"failed to import extension: {exc!r}")]

    setup = getattr(module, EXTENSION_ENTRY_ATTRIBUTE, None)
    if not callable(setup):
        del sys.modules[module_name]
        return None, [
            _error_diagnostic(
                entry,
                "extension does not define a callable "
                f"`{EXTENSION_ENTRY_ATTRIBUTE}(tau)` entry point",
            )
        ]
    if iscoroutinefunction(setup):
        del sys.modules[module_name]
        return None, [
            _error_diagnostic(
                entry,
                f"`{EXTENSION_ENTRY_ATTRIBUTE}` must be a sync function"
                " (async setup is not supported)",
            )
        ]

    return LoadedExtension(name=entry.name, path=entry.path, setup=setup), []


def unload_extension_modules() -> int:
    """Remove previously imported extension modules from `sys.modules`.

    Used by reload so re-discovered extensions import fresh module objects.
    Returns the number of modules removed.
    """
    stale = [name for name in sys.modules if name.startswith(f"{_MODULE_NAME_PREFIX}_")]
    for name in stale:
        del sys.modules[name]
    return len(stale)


def _error_diagnostic(entry: DiscoveredExtension, message: str) -> ResourceDiagnostic:
    return ResourceDiagnostic(
        kind="extension",
        name=entry.name,
        path=entry.path,
        message=message,
        severity="error",
    )


def _slugify(name: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in name).lower()


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped
