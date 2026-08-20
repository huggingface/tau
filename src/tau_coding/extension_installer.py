"""Install Tau extensions from local paths or Git repositories."""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from tau_coding.extensions import discover_extensions
from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class ExtensionInstallError(RuntimeError):
    """Raised when an extension source cannot be installed safely."""


@dataclass(frozen=True, slots=True)
class GitExtensionSource:
    """A normalized Git repository source and optional checkout ref."""

    url: str
    ref: str | None
    name: str


def install_extension(
    source: str,
    *,
    force: bool = False,
    extensions_dir: Path | None = None,
    command_runner: CommandRunner = subprocess.run,
) -> Path:
    """Install one extension into Tau's user extension directory.

    Local Python files are copied. Local package directories and Git
    repositories must expose ``extension.py`` or ``[tool.tau].extensions`` so
    the normal user-directory discovery path can load them on the next run.
    """
    destination_root = extensions_dir or TauPaths().user_extensions_dir
    staging_root: Path | None = None
    try:
        destination_root.mkdir(parents=True, exist_ok=True)
        local_source = Path(source).expanduser()
        resolved_source: Path | None = None
        git_source: GitExtensionSource | None = None
        if local_source.exists():
            resolved_source = local_source.resolve()
            name = _local_install_name(resolved_source)
        else:
            git_source = parse_git_extension_source(source)
            name = git_source.name
        destination = destination_root / name

        # Reproduce the exact ~/.tau/extensions/<name> shape during validation.
        # This catches layouts that explicit -e discovery accepts but normal
        # user-directory discovery would skip, such as nested/extension.py.
        staging_root = Path(mkdtemp(prefix=".extension-install-", dir=destination_root))
        staging = staging_root / "extensions" / name
        staging.parent.mkdir()
        if resolved_source is not None and resolved_source.is_file():
            shutil.copy2(resolved_source, staging)
        elif resolved_source is not None and resolved_source.is_dir():
            shutil.copytree(resolved_source, staging, ignore=_local_copy_ignore)
        elif git_source is not None:
            _clone_git_source(git_source, staging, command_runner=command_runner)
        else:
            raise ExtensionInstallError(f"unsupported local extension source: {source}")

        _validate_staged_extension(staging_root)
        _publish_staged_extension(staging, destination, force=force)
        return destination
    except OSError as exc:
        raise ExtensionInstallError(f"extension installation failed: {exc}") from exc
    finally:
        if staging_root is not None:
            with contextlib.suppress(OSError):
                _remove_path(staging_root)


def parse_git_extension_source(source: str) -> GitExtensionSource:
    """Normalize a Pi-style Git source accepted by ``tau install``."""
    raw = source[4:] if source.startswith("git:") else source
    if not raw:
        raise ExtensionInstallError("Git extension source is empty")

    ref: str | None = None
    last_at = raw.rfind("@")
    last_separator = max(raw.rfind("/"), raw.rfind(":"))
    if last_at > last_separator:
        raw, ref = raw[:last_at], raw[last_at + 1 :]
        if not ref:
            raise ExtensionInstallError("Git extension ref is empty")

    if raw.startswith("github.com/"):
        url = f"https://{raw}"
    elif raw.startswith(("https://", "http://", "ssh://", "git://", "file://", "git@")):
        url = raw
    else:
        raise ExtensionInstallError(
            "extension source does not exist locally and is not a supported Git source; "
            "use a local path, git:github.com/owner/repo, or a Git URL"
        )

    repository_path = raw.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    name = repository_path.removesuffix(".git")
    _validate_install_name(name)
    return GitExtensionSource(url=url, ref=ref, name=name)


def _clone_git_source(
    source: GitExtensionSource,
    destination: Path,
    *,
    command_runner: CommandRunner,
) -> None:
    result = command_runner(
        ["git", "clone", "--", source.url, str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git clone failed").strip()
        raise ExtensionInstallError(f"could not clone {source.url}: {detail}")
    if source.ref is None:
        return
    result = command_runner(
        ["git", "-C", str(destination), "checkout", "--detach", source.ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git checkout failed").strip()
        raise ExtensionInstallError(f"could not check out ref {source.ref!r}: {detail}")


def _local_install_name(source: Path) -> str:
    if source.is_file() and source.suffix != ".py":
        raise ExtensionInstallError("a local extension file must have a .py suffix")
    name = source.name
    _validate_install_name(name)
    return name


def _validate_install_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or name in {".", ".."}:
        raise ExtensionInstallError(f"extension source has an unsupported install name: {name!r}")


def _validate_staged_extension(staging_root: Path) -> None:
    paths = TauResourcePaths(root=staging_root, cwd=Path.cwd())
    discovered, diagnostics = discover_extensions(paths)
    errors = [diagnostic.message for diagnostic in diagnostics if diagnostic.severity == "error"]
    if errors:
        raise ExtensionInstallError("invalid extension package: " + "; ".join(errors))
    if not discovered:
        raise ExtensionInstallError(
            "an extension directory must contain extension.py or declare "
            "[tool.tau].extensions in pyproject.toml"
        )


def _publish_staged_extension(staging: Path, destination: Path, *, force: bool) -> None:
    if destination.exists() or destination.is_symlink():
        if not force:
            raise ExtensionInstallError(
                f"extension is already installed at {destination}; pass --force to replace it"
            )
        backup = destination.with_name(f".{destination.name}.backup")
        _remove_path(backup)
        destination.replace(backup)
        try:
            staging.replace(destination)
        except BaseException:
            backup.replace(destination)
            raise
        _remove_path(backup)
        return
    staging.replace(destination)


def _local_copy_ignore(_directory: str, names: Sequence[str]) -> set[str]:
    ignored = {".git", ".hg", ".svn", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
    return ignored.intersection(names)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)
