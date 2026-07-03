"""Best-effort PyPI update checks for the Tau CLI."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

from tau_coding.paths import TauPaths

PYPI_PACKAGE_NAME = "tau-ai"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PYPI_PACKAGE_NAME}/json"
UPDATE_CHECK_INTERVAL = timedelta(days=1)
UPDATE_CHECK_TIMEOUT_SECONDS = 1.5
UPDATE_CHECK_ENV_DISABLE = "TAU_NO_UPDATE_CHECK"
RELEASE_NOTES_STATE_FILENAME = "release-notes-state.json"
RELEASE_NOTES_PATH = Path(__file__).resolve().parents[2] / "release-notes" / "releases.json"

Fetcher = Callable[[str, float], dict[str, Any]]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class UpdateNotice:
    """A user-facing update notice."""

    current_version: str
    latest_version: str
    package_name: str = PYPI_PACKAGE_NAME

    @property
    def message(self) -> str:
        """Return concise update guidance."""
        return (
            f"Tau {self.latest_version} is available (installed: {self.current_version}). "
            f"Update with: uv tool upgrade {self.package_name}"
        )


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    """Cached latest-version lookup result."""

    checked_at: datetime
    latest_version: str | None


@dataclass(frozen=True, slots=True)
class ReleaseNoteSection:
    """A named release-note section."""

    title: str
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReleaseNotesEntry:
    """Structured release notes for one Tau version."""

    version: str
    date: str | None
    sections: tuple[ReleaseNoteSection, ...]

    @property
    def transcript_items(self) -> tuple[str, ...]:
        """Return flattened note text for compact in-app display."""
        return tuple(item for section in self.sections for item in section.items)


@dataclass(frozen=True, slots=True)
class ReleaseNotesNotice:
    """Release notes shown once after the installed Tau version changes."""

    current_version: str
    previous_version: str
    entries: tuple[ReleaseNotesEntry, ...]

    @property
    def notes(self) -> tuple[str, ...]:
        """Return all release note items included in this notice."""
        return tuple(item for entry in self.entries for item in entry.transcript_items)

    @property
    def message(self) -> str:
        """Return a compact markdown release-notes block for the transcript."""
        if self.entries:
            version_blocks = [self._format_entry(entry) for entry in self.entries]
            body = "\n\n".join(version_blocks)
        else:
            body = "- See the changelog for details."
        return f"Tau updated to {self.current_version}\n\n{body}"

    def _format_entry(self, entry: ReleaseNotesEntry) -> str:
        section_blocks: list[str] = []
        for section in entry.sections:
            bullets = "\n".join(f"- {item}" for item in section.items)
            section_blocks.append(f"**{section.title}**\n{bullets}")
        return "\n\n".join(section_blocks)


def startup_update_notice(
    current_version: str,
    *,
    fetcher: Fetcher | None = None,
    cache_path: Path | None = None,
    now: Clock | None = None,
    env: Mapping[str, str] | None = None,
) -> UpdateNotice | None:
    """Return an update notice when PyPI has a newer stable Tau release.

    This function is intentionally best-effort: cache, network, JSON, and version
    parsing failures all become quiet no-ops so startup can continue.
    """
    environment = environ if env is None else env
    if _update_check_disabled(environment):
        return None

    current_time = (now or _utc_now)()
    cached_result = _cached_update_check_result(cache_path, current_time)
    if cached_result is None:
        try:
            latest_version = fetch_latest_pypi_version(fetcher=fetcher)
        except Exception:  # noqa: BLE001 - update checks must never block startup
            return None
        _write_update_check_cache(cache_path, current_time, latest_version)
    else:
        latest_version = cached_result.latest_version

    if latest_version is None:
        return None

    try:
        if Version(latest_version) <= Version(current_version):
            return None
    except InvalidVersion:
        return None

    return UpdateNotice(current_version=current_version, latest_version=latest_version)


def startup_release_notes_notice(
    current_version: str,
    *,
    state_path: Path | None = None,
    release_notes: tuple[ReleaseNotesEntry, ...] | None = None,
) -> ReleaseNotesNotice | None:
    """Return release notes once after Tau starts with a newer installed version.

    The first run only records the current version so fresh installs do not see an
    upgrade banner. Read/write failures are quiet no-ops so startup can continue.
    """
    path = state_path or default_release_notes_state_path()
    try:
        previous_version = _read_last_seen_version(path)
    except Exception:  # noqa: BLE001 - release-note state should not block startup
        previous_version = None

    _write_release_notes_state(path, current_version)
    if previous_version is None:
        return None

    try:
        if Version(current_version) <= Version(previous_version):
            return None
    except InvalidVersion:
        return None

    entries = release_notes_between(
        previous_version,
        current_version,
        release_notes if release_notes is not None else load_release_notes(),
    )
    return ReleaseNotesNotice(
        current_version=current_version,
        previous_version=previous_version,
        entries=entries,
    )


def load_release_notes(path: Path | None = None) -> tuple[ReleaseNotesEntry, ...]:
    """Load structured release notes from the repo-owned JSON file."""
    data = json.loads((path or RELEASE_NOTES_PATH).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("release notes must be a JSON array")
    return tuple(_parse_release_notes_entry(item) for item in data)


def release_notes_between(
    previous_version: str,
    current_version: str,
    release_notes: tuple[ReleaseNotesEntry, ...],
) -> tuple[ReleaseNotesEntry, ...]:
    """Return release-note entries newer than previous_version up to current_version."""
    try:
        previous = Version(previous_version)
        current = Version(current_version)
    except InvalidVersion:
        return ()

    selected: list[ReleaseNotesEntry] = []
    for entry in release_notes:
        try:
            parsed = Version(entry.version)
        except InvalidVersion:
            continue
        if previous < parsed <= current:
            selected.append(entry)
    return tuple(sorted(selected, key=lambda entry: Version(entry.version)))


def fetch_latest_pypi_version(*, fetcher: Fetcher | None = None) -> str | None:
    """Fetch the latest stable Tau version from PyPI."""
    data = (fetcher or _httpx_fetch_json)(PYPI_JSON_URL, UPDATE_CHECK_TIMEOUT_SECONDS)
    releases = data.get("releases")
    if isinstance(releases, dict):
        versions = _stable_release_versions(releases)
        if versions:
            return str(max(versions))

    info = data.get("info")
    if isinstance(info, dict):
        version = info.get("version")
        if isinstance(version, str):
            parsed = Version(version)
            if not parsed.is_prerelease and not parsed.is_devrelease:
                return version
    return None


def default_update_check_cache_path(paths: TauPaths | None = None) -> Path:
    """Return the on-disk cache path for startup update checks."""
    return (paths or TauPaths()).home / "cache" / "update-check.json"


def default_release_notes_state_path(paths: TauPaths | None = None) -> Path:
    """Return the on-disk state path for one-time release notes."""
    return (paths or TauPaths()).home / "cache" / RELEASE_NOTES_STATE_FILENAME


def _parse_release_notes_entry(data: Any) -> ReleaseNotesEntry:
    if not isinstance(data, dict):
        raise ValueError("release note entry must be an object")
    version = data.get("version")
    if not isinstance(version, str):
        raise ValueError("release note entry missing version")
    date = data.get("date")
    if date is not None and not isinstance(date, str):
        raise ValueError("release note date must be a string")
    raw_sections = data.get("sections")
    if not isinstance(raw_sections, dict):
        raise ValueError("release note sections must be an object")

    sections: list[ReleaseNoteSection] = []
    for title, raw_items in raw_sections.items():
        if not isinstance(title, str):
            raise ValueError("release note section title must be a string")
        if not isinstance(raw_items, list) or not all(isinstance(item, str) for item in raw_items):
            raise ValueError("release note section items must be strings")
        sections.append(ReleaseNoteSection(title=title, items=tuple(raw_items)))
    return ReleaseNotesEntry(version=version, date=date, sections=tuple(sections))


def _stable_release_versions(releases: dict[Any, Any]) -> list[Version]:
    versions: list[Version] = []
    for version_text, files in releases.items():
        if not isinstance(version_text, str):
            continue
        if isinstance(files, list) and not files:
            continue
        try:
            parsed = Version(version_text)
        except InvalidVersion:
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        versions.append(parsed)
    return versions


def _httpx_fetch_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    response = httpx.get(url, timeout=timeout_seconds, follow_redirects=True)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("PyPI response must be a JSON object")
    return data


def _cached_update_check_result(cache_path: Path | None, now: datetime) -> UpdateCheckResult | None:
    path = cache_path or default_update_check_cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = _parse_cached_result(data)
    except Exception:  # noqa: BLE001 - corrupt caches should be ignored
        return None
    if now - result.checked_at > UPDATE_CHECK_INTERVAL:
        return None
    return result


def _parse_cached_result(data: Any) -> UpdateCheckResult:
    if not isinstance(data, dict):
        raise ValueError("cache must be a JSON object")
    checked_at = data.get("checked_at")
    latest_version = data.get("latest_version")
    if not isinstance(checked_at, str):
        raise ValueError("cache missing checked_at")
    if latest_version is not None and not isinstance(latest_version, str):
        raise ValueError("cache latest_version must be a string")
    parsed_checked_at = datetime.fromisoformat(checked_at)
    if parsed_checked_at.tzinfo is None:
        parsed_checked_at = parsed_checked_at.replace(tzinfo=UTC)
    return UpdateCheckResult(checked_at=parsed_checked_at, latest_version=latest_version)


def _write_update_check_cache(
    cache_path: Path | None,
    checked_at: datetime,
    latest_version: str | None,
) -> None:
    path = cache_path or default_update_check_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checked_at": checked_at.astimezone(UTC).isoformat(),
                    "latest_version": latest_version,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def _read_last_seen_version(path: Path) -> str | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("release-note state must be a JSON object")
    version = data.get("last_seen_version")
    if version is not None and not isinstance(version, str):
        raise ValueError("release-note last_seen_version must be a string")
    return version


def _write_release_notes_state(path: Path, current_version: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_seen_version": current_version}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def _update_check_disabled(env: Mapping[str, str]) -> bool:
    value = env.get(UPDATE_CHECK_ENV_DISABLE)
    if value is not None and value.strip().lower() not in {"", "0", "false", "no"}:
        return True
    return bool(env.get("CI"))


def _utc_now() -> datetime:
    return datetime.now(UTC)
