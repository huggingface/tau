"""Project-input trust policy, detection, persistence, and coordination.

Project trust controls ambient project resources. It is deliberately not a
filesystem, process, network, tool, model, or prompt-injection sandbox.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

from tau_coding.paths import TauPaths
from tau_coding.prompt_templates import is_prompt_template_candidate
from tau_coding.skills import is_skill_candidate

TrustDefault = Literal["ask", "always", "never"]
TrustDecision = Literal["trusted", "untrusted"]
TrustOverride = Literal["approve", "decline"]
TrustScope = Literal["exact", "parent", "run"]
TrustSource = Literal["override", "empty", "extension", "saved", "default", "ui"]
TrustChoice = Literal["trust-exact", "trust-parent", "trust-run", "decline-exact", "decline-run"]

_RESOURCE_CATEGORIES = (
    "context",
    "extensions",
    "prompts",
    "settings",
    "skills",
    "system-prompts",
    "themes",
)


class ProjectTrustError(RuntimeError):
    """A trust path, store, or persistence operation failed safely."""


@dataclass(frozen=True, slots=True)
class CanonicalProjectPath:
    """An existing, canonical project working directory."""

    value: Path


@dataclass(frozen=True, slots=True)
class ProtectedResourceSummary:
    """Bounded metadata-only summary of protected project inputs."""

    cwd: CanonicalProjectPath
    categories: tuple[str, ...]
    counts: Mapping[str, int]
    sample_paths: tuple[Path, ...] = ()

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True, slots=True)
class SavedTrustEntry:
    """A validated saved exact or inherited decision."""

    path: CanonicalProjectPath
    decision: TrustDecision


@dataclass(frozen=True, slots=True)
class ProjectTrustRequest:
    """Frontend-neutral request for an interactive trust decision."""

    cwd: CanonicalProjectPath
    resources: ProtectedResourceSummary
    inherited_entry: SavedTrustEntry | None
    choices: tuple[TrustChoice, ...] = (
        "trust-exact",
        "trust-parent",
        "trust-run",
        "decline-exact",
        "decline-run",
    )


@dataclass(frozen=True, slots=True)
class ProjectTrustResolution:
    """Completed decision for one canonical cwd."""

    trusted: bool
    source: TrustSource
    saved_path: CanonicalProjectPath | None = None
    diagnostics: tuple[str, ...] = ()
    had_candidates: bool = True
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ExtensionTrustResult:
    """Result returned by an eligible pre-trust extension."""

    decision: Literal["approve", "decline", "defer"] = "defer"
    remember: bool = False


@dataclass(frozen=True, slots=True)
class ProjectTrustEvent:
    """Content-free payload sent to eligible pre-trust extensions."""

    cwd: Path
    mode: Literal["interactive", "headless"]
    has_ui: bool
    categories: tuple[str, ...]
    counts: Mapping[str, int]
    type: Literal["project_trust"] = "project_trust"


TrustPrompt = Callable[[ProjectTrustRequest], Awaitable[TrustChoice | None]]
ExtensionDecider = Callable[[ProjectTrustEvent], Awaitable[ExtensionTrustResult | None]]


def _darwin_filesystem_path(path: Path) -> Path:
    """Return macOS's case-preserving path for an existing filesystem object."""
    import fcntl

    descriptor = os.open(path, os.O_RDONLY)
    try:
        raw = fcntl.fcntl(descriptor, 50, b"\0" * 1024)  # F_GETPATH / MAXPATHLEN
    finally:
        os.close(descriptor)
    value = raw.split(b"\0", 1)[0]
    if not value:
        raise OSError(f"Could not determine filesystem casing for {path}")
    return Path(os.fsdecode(value))


def canonicalize_project_path(path: Path, *, base: Path | None = None) -> CanonicalProjectPath:
    """Strictly canonicalize an existing destination cwd."""
    expanded = path.expanduser()
    if not expanded.is_absolute():
        if base is None:
            raise ProjectTrustError("A base directory is required for a relative project cwd")
        expanded = base.expanduser() / expanded
    try:
        resolved = expanded.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectTrustError(f"Could not canonicalize project cwd {expanded}: {exc}") from exc
    if not resolved.is_dir():
        raise ProjectTrustError(f"Project cwd is not a directory: {resolved}")
    try:
        if sys.platform == "win32":
            resolved = Path(os.path.normcase(str(resolved)))
        elif sys.platform == "darwin":
            # normcase() is a no-op on Darwin. F_GETPATH asks the mounted
            # filesystem for its case-preserving spelling, so aliases on a
            # case-insensitive volume share a key without collapsing distinct
            # paths on a case-sensitive volume.
            resolved = _darwin_filesystem_path(resolved)
    except (OSError, UnicodeError) as exc:
        raise ProjectTrustError(
            f"Could not canonicalize project cwd casing {resolved}: {exc}"
        ) from exc
    return CanonicalProjectPath(resolved)


class ProtectedResourceDetector:
    """Detect protected candidates using names and file metadata only."""

    def __init__(self, *, max_sample_paths: int = 12) -> None:
        self.max_sample_paths = max_sample_paths

    def detect(self, cwd: CanonicalProjectPath) -> ProtectedResourceSummary:
        root = cwd.value
        found: dict[str, list[Path]] = {category: [] for category in _RESOURCE_CATEGORIES}
        # Project settings are not supported by a Tau loader, so they cannot
        # trigger trust until that loader exists.
        for namespace in (".tau", ".agents"):
            self._glob(
                found,
                "skills",
                root / namespace / "skills",
                "*/SKILL.md",
                predicate=is_skill_candidate,
            )
            self._glob(
                found,
                "prompts",
                root / namespace / "prompts",
                "*.md",
                predicate=is_prompt_template_candidate,
            )
        self._glob(found, "themes", root / ".tau" / "themes", "*.json")
        self._file(found, "system-prompts", root / ".tau" / "SYSTEM.md")
        self._file(found, "system-prompts", root / ".tau" / "APPEND_SYSTEM.md")
        self._context(found, root)
        self._extensions(found, root / ".tau" / "extensions")
        counts = {
            category: len(found[category]) for category in _RESOURCE_CATEGORIES if found[category]
        }
        samples = tuple(path for category in _RESOURCE_CATEGORIES for path in found[category])[
            : self.max_sample_paths
        ]
        return ProtectedResourceSummary(
            cwd=cwd,
            categories=tuple(counts),
            counts=counts,
            sample_paths=samples,
        )

    @staticmethod
    def _is_candidate(path: Path) -> bool:
        try:
            return path.is_file() or path.is_symlink()
        except OSError:
            return True

    def _file(self, found: dict[str, list[Path]], category: str, path: Path) -> None:
        if self._is_candidate(path):
            found[category].append(path)

    def _glob(
        self,
        found: dict[str, list[Path]],
        category: str,
        directory: Path,
        pattern: str,
        *,
        predicate: Callable[[Path], bool] | None = None,
    ) -> None:
        try:
            entries = tuple(directory.glob(pattern)) if directory.is_dir() else ()
        except OSError:
            # An unreadable protected directory is itself a meaningful trigger.
            found[category].append(directory)
            return
        found[category].extend(
            path
            for path in entries
            if self._is_candidate(path) and (predicate is None or predicate(path))
        )

    def _context(self, found: dict[str, list[Path]], cwd: Path) -> None:
        # Match current Tau discovery: nearest project marker through cwd, then
        # cwd-local namespace context files.
        markers = (".git", "pyproject.toml", "uv.lock", "setup.py", "package.json")
        project_root = cwd
        for candidate in (cwd, *cwd.parents):
            if any((candidate / marker).exists() for marker in markers):
                project_root = candidate
                break
        try:
            relative = cwd.relative_to(project_root)
        except ValueError:
            relative = Path()
        current = project_root
        self._file(found, "context", current / "AGENTS.md")
        for part in relative.parts:
            current /= part
            self._file(found, "context", current / "AGENTS.md")
        self._file(found, "context", cwd / ".tau" / "AGENTS.md")
        self._file(found, "context", cwd / ".agents" / "AGENTS.md")

    def _extensions(self, found: dict[str, list[Path]], directory: Path) -> None:
        try:
            entries = tuple(directory.iterdir()) if directory.is_dir() else ()
        except OSError:
            found["extensions"].append(directory)
            return
        for path in entries:
            if path.name.startswith((".", "_")):
                continue
            if path.suffix == ".py" and self._is_candidate(path):
                found["extensions"].append(path)
            elif path.is_dir():
                for candidate in (path / "extension.py", path / "pyproject.toml"):
                    self._file(found, "extensions", candidate)


class ProjectTrustStore:
    """Versioned, locked, atomically replaced trust decision store."""

    def __init__(self, paths: TauPaths | None = None) -> None:
        self.paths = paths or TauPaths()
        self.path = self.paths.home / "trust.json"
        self.lock_path = self.paths.home / "trust.json.lock"
        self.pending_path = self.paths.home / "trust.json.pending"

    def nearest(self, cwd: CanonicalProjectPath) -> SavedTrustEntry | None:
        decisions = self.read()
        current = cwd.value
        while True:
            decision = decisions.get(current)
            if decision is not None:
                return SavedTrustEntry(CanonicalProjectPath(current), decision)
            if current.parent == current:
                return None
            current = current.parent

    def read(self) -> dict[Path, TrustDecision]:
        with self._locked():
            return self._read_unlocked()

    def set(self, path: CanonicalProjectPath, decision: TrustDecision) -> None:
        with self._locked():
            decisions = self._read_unlocked()
            decisions[path.value] = decision
            self._write_unlocked(decisions)

    def trust_parent(self, cwd: CanonicalProjectPath) -> CanonicalProjectPath:
        parent = CanonicalProjectPath(cwd.value.parent)
        with self._locked():
            decisions = self._read_unlocked()
            decisions.pop(cwd.value, None)
            decisions[parent.value] = "trusted"
            self._write_unlocked(decisions)
        return parent

    def remove(self, path: CanonicalProjectPath) -> None:
        with self._locked():
            decisions = self._read_unlocked()
            decisions.pop(path.value, None)
            self._write_unlocked(decisions)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            self.paths.home.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.lock_path.open("a+b") as handle:
                os.chmod(self.lock_path, 0o600)
                _lock(handle)
                try:
                    yield
                finally:
                    _unlock(handle)
        except ProjectTrustError:
            raise
        except OSError as exc:
            raise ProjectTrustError(
                f"Could not lock project trust store {self.path}: {exc}"
            ) from exc

    def _read_unlocked(self) -> dict[Path, TrustDecision]:
        # A pending journal means an update did not reach its commit point.
        # Ordinary reads must never guess whether the interrupted operation was
        # a grant or a revocation: either direction could resurrect trust.
        # Recovery is attempted only by the writer that observed its own
        # failure; a journal left by a crash remains visibly fail-closed.
        if self.pending_path.exists():
            raise ProjectTrustError(
                f"Project trust store {self.path} has an incomplete update; "
                f"pending journal requires explicit recovery: {self.pending_path}"
            )
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectTrustError(
                f"Could not read project trust store {self.path}: {exc}"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {"version", "decisions"}:
            raise ProjectTrustError(f"Malformed project trust store {self.path}: unknown schema")
        if payload["version"] != 1 or not isinstance(payload["decisions"], list):
            raise ProjectTrustError(f"Unsupported or malformed project trust store {self.path}")
        result: dict[Path, TrustDecision] = {}
        for raw in payload["decisions"]:
            if not isinstance(raw, dict) or set(raw) != {"path", "decision"}:
                raise ProjectTrustError(f"Malformed decision in project trust store {self.path}")
            raw_path = raw["path"]
            decision = raw["decision"]
            if not isinstance(raw_path, str) or decision not in {"trusted", "untrusted"}:
                raise ProjectTrustError(f"Malformed decision in project trust store {self.path}")
            candidate = Path(raw_path)
            if not candidate.is_absolute() or Path(os.path.normpath(raw_path)) != candidate:
                raise ProjectTrustError(f"Noncanonical path in project trust store {self.path}")
            normalized = Path(os.path.normcase(raw_path)) if sys.platform == "win32" else candidate
            if sys.platform == "darwin" and candidate.exists():
                try:
                    normalized = _darwin_filesystem_path(candidate)
                except (OSError, UnicodeError) as exc:
                    raise ProjectTrustError(
                        f"Could not validate path casing in project trust store {self.path}: {exc}"
                    ) from exc
                if normalized != candidate:
                    raise ProjectTrustError(
                        f"Noncanonical path casing in project trust store {self.path}: {candidate}"
                    )
            if normalized in result:
                raise ProjectTrustError(f"Duplicate path in project trust store {self.path}")
            result[normalized] = decision
        return result

    def _write_unlocked(self, decisions: Mapping[Path, TrustDecision]) -> None:
        payload = {
            "version": 1,
            "decisions": [
                {"path": str(path), "decision": decision}
                for path, decision in sorted(decisions.items(), key=lambda item: str(item[0]))
            ],
        }
        data = (json.dumps(payload, indent=2) + "\n").encode()
        prior_bytes = self.path.read_bytes() if self.path.exists() else None

        # Persist a fail-closed undo journal before touching trust.json. Readers
        # reject the store while this marker exists, so even failed recovery can
        # never expose a newly granting destination.
        journal = (b"present\n" + prior_bytes) if prior_bytes is not None else b"absent\n"
        try:
            self._atomic_replace(self.pending_path, journal, prefix=".trust-pending-")
            self._atomic_replace(self.path, data, prefix=".trust-")
        except OSError as exc:
            recovery_error = self._recover_unlocked()
            detail = f"; recovery failed: {recovery_error}" if recovery_error else ""
            raise ProjectTrustError(
                f"Could not write project trust store {self.path}: {exc}{detail}"
            ) from exc

        # The destination and its directory entry are durable. Failure to clear
        # the journal is still a failed update and must restore the prior state.
        try:
            self.pending_path.unlink()
        except OSError as exc:
            recovery_error = self._recover_unlocked()
            detail = f"; recovery failed: {recovery_error}" if recovery_error else ""
            raise ProjectTrustError(
                f"Could not commit project trust store {self.path}: {exc}{detail}"
            ) from exc
        # Journal cleanup is not part of the data commit. If this fsync fails,
        # either the deletion persists (the durable destination grants) or the
        # journal reappears after a crash (reads fail closed).
        with suppress(OSError):
            _fsync_directory(self.paths.home)

    def _atomic_replace(self, destination: Path, data: bytes, *, prefix: str) -> None:
        fd = -1
        temporary: Path | None = None
        try:
            fd, raw = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=self.paths.home)
            temporary = Path(raw)
            os.chmod(temporary, 0o600)
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            temporary = None
            _fsync_directory(self.paths.home)
        finally:
            if fd >= 0:
                os.close(fd)
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def _recover_unlocked(self) -> OSError | None:
        """Restore the journaled state; retain the marker on every failure."""
        if not self.pending_path.exists():
            return None
        try:
            journal = self.pending_path.read_bytes()
            marker, separator, prior_bytes = journal.partition(b"\n")
            if not separator or marker not in {b"present", b"absent"}:
                raise OSError("malformed project trust recovery journal")
            if marker == b"present":
                self._atomic_replace(self.path, prior_bytes, prefix=".trust-rollback-")
            else:
                self.path.unlink(missing_ok=True)
                _fsync_directory(self.paths.home)
            self.pending_path.unlink()
            with suppress(OSError):
                _fsync_directory(self.paths.home)
        except OSError as exc:
            return exc
        return None


class ProjectTrustCoordinator:
    """Resolve and cache trust outcomes per canonical cwd for one invocation."""

    def __init__(
        self, store: ProjectTrustStore, detector: ProtectedResourceDetector | None = None
    ) -> None:
        self.store = store
        self.detector = detector or ProtectedResourceDetector()
        self._cache: dict[Path, ProjectTrustResolution] = {}

    async def resolve(
        self,
        cwd: Path,
        *,
        override: TrustOverride | None = None,
        default: TrustDefault = "ask",
        interactive: bool = False,
        prompt: TrustPrompt | None = None,
        extension_deciders: Sequence[ExtensionDecider] = (),
        refresh: bool = False,
        cache_result: bool = True,
    ) -> tuple[ProtectedResourceSummary, ProjectTrustResolution]:
        canonical = canonicalize_project_path(cwd, base=Path.cwd())
        summary = self.detector.detect(canonical)

        def finish(result: ProjectTrustResolution) -> ProjectTrustResolution:
            if cache_result:
                self._cache[canonical.value] = result
            return result

        cached = self._cache.get(canonical.value)
        if cached is not None and cached.had_candidates:
            return summary, cached
        if cached is not None and not refresh and not summary.categories:
            return summary, cached
        diagnostics: list[str] = []
        if override is not None:
            result = ProjectTrustResolution(
                trusted=override == "approve",
                source="override",
                had_candidates=bool(summary.categories),
            )
            return summary, finish(result)
        if not summary.categories:
            result = ProjectTrustResolution(trusted=True, source="empty", had_candidates=False)
            return summary, finish(result)

        event = ProjectTrustEvent(
            cwd=canonical.value,
            mode="interactive" if interactive else "headless",
            has_ui=interactive and prompt is not None,
            categories=summary.categories,
            counts=summary.counts,
        )
        for decide in extension_deciders:
            try:
                extension_result = await decide(event)
            except Exception as exc:  # noqa: BLE001 - extensions safely defer on errors
                diagnostics.append(f"project_trust extension failed: {type(exc).__name__}: {exc}")
                continue
            if extension_result is None or extension_result.decision == "defer":
                continue
            trusted = extension_result.decision == "approve"
            saved_path: CanonicalProjectPath | None = None
            if extension_result.remember:
                try:
                    self.store.set(canonical, "trusted" if trusted else "untrusted")
                except ProjectTrustError as exc:
                    diagnostics.append(str(exc))
                    trusted = False
                else:
                    saved_path = canonical
            result = ProjectTrustResolution(
                trusted=trusted,
                source="extension",
                saved_path=saved_path,
                diagnostics=tuple(diagnostics),
            )
            return summary, finish(result)

        inherited: SavedTrustEntry | None = None
        store_failed = False
        try:
            inherited = self.store.nearest(canonical)
        except ProjectTrustError as exc:
            store_failed = True
            diagnostics.append(str(exc))
        if inherited is not None:
            result = ProjectTrustResolution(
                trusted=inherited.decision == "trusted",
                source="saved",
                saved_path=inherited.path,
                diagnostics=tuple(diagnostics),
            )
            return summary, finish(result)
        if default != "ask":
            result = ProjectTrustResolution(
                trusted=default == "always" and not store_failed,
                source="default",
                diagnostics=tuple(diagnostics),
            )
            return summary, finish(result)
        if not interactive or prompt is None:
            result = ProjectTrustResolution(
                trusted=False, source="default", diagnostics=tuple(diagnostics)
            )
            return summary, finish(result)

        choice = await prompt(ProjectTrustRequest(canonical, summary, inherited))
        trusted = choice in {"trust-exact", "trust-parent", "trust-run"}
        saved_path = None
        try:
            if choice == "trust-exact":
                self.store.set(canonical, "trusted")
                saved_path = canonical
            elif choice == "trust-parent":
                saved_path = self.store.trust_parent(canonical)
            elif choice == "decline-exact":
                self.store.set(canonical, "untrusted")
                saved_path = canonical
        except ProjectTrustError as exc:
            diagnostics.append(str(exc))
            trusted = False
            saved_path = None
        result = ProjectTrustResolution(
            trusted=trusted,
            source="ui",
            saved_path=saved_path,
            diagnostics=tuple(diagnostics),
            cancelled=choice is None,
        )
        return summary, finish(result)

    def commit(self, cwd: CanonicalProjectPath, result: ProjectTrustResolution) -> None:
        """Publish a staged resolution after its resource snapshot succeeds."""
        self._cache[cwd.value] = result


def format_trust_diagnostic(
    summary: ProtectedResourceSummary, resolution: ProjectTrustResolution
) -> str:
    """Return one bounded, content-free decision diagnostic."""
    categories = (
        ", ".join(f"{category}={summary.counts[category]}" for category in summary.categories)
        or "none"
    )
    scope = f" via {resolution.saved_path.value}" if resolution.saved_path is not None else ""
    outcome = "trusted" if resolution.trusted else "untrusted"
    return (
        f"Project inputs for {summary.cwd.value}: {outcome} "
        f"(source={resolution.source}{scope}; {categories}). "
        "Project trust is an input-loading guard, not a sandbox."
    )


def _lock(handle: IO[bytes]) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        raise ProjectTrustError(f"Could not acquire project trust lock: {exc}") from exc


def _unlock(handle: IO[bytes]) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
