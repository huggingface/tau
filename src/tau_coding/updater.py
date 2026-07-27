"""Upgrade Tau with the package manager that owns its environment."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from subprocess import CompletedProcess, run
from typing import Literal

from tau_coding.update_check import PYPI_PACKAGE_NAME, fetch_latest_pypi_version

CommandRunner = Callable[..., CompletedProcess[str]]
DetachedLauncher = Callable[..., object]
ExecutableFinder = Callable[[str], str | None]
LatestVersionFetcher = Callable[[], str | None]
InstallMethod = Literal["uv-tool", "uv-pip", "pipx", "pip"]


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Result of trying to upgrade Tau."""

    command: tuple[str, ...] | None
    stdout: str = ""
    stderr: str = ""
    failures: tuple[str, ...] = ()
    deferred: bool = False

    @property
    def succeeded(self) -> bool:
        return self.command is not None


def update_tau(
    *,
    runner: CommandRunner = run,
    python_executable: str | None = None,
    environment_prefix: Path | None = None,
    direct_url: str | None = None,
    installer: str | None = None,
    inspect_distribution: bool = True,
    latest_version_fetcher: LatestVersionFetcher = fetch_latest_pypi_version,
    platform_name: str | None = None,
    detached_launcher: DetachedLauncher = subprocess.Popen,
    executable_finder: ExecutableFinder = shutil.which,
    parent_pid: int | None = None,
    handoff_directory: Path | None = None,
) -> UpdateResult:
    """Upgrade Tau with the installer that owns the active environment.

    Python distributions record their installer in ``INSTALLER``. uv and pipx
    tool environments also leave ownership receipts. Managed, editable,
    direct-URL, and unrecognized installations stop with instructions instead
    of trying unrelated package managers.
    """
    prefix = (environment_prefix or Path(sys.prefix)).resolve()
    if inspect_distribution:
        direct_url = _installed_direct_url()
        installer = _installed_installer()
    if direct_url:
        return _failure(
            "Tau was installed from a local or direct URL, so it cannot be safely "
            f"updated from PyPI. Reinstall it from its original source: {direct_url}"
        )

    method = detect_install_method(prefix, installer=installer)
    if method is None:
        if (prefix / "conda-meta").is_dir():
            return _failure(
                "Tau is installed in a Conda/Pixi-managed environment. "
                "Update it with the manager that created that environment."
            )
        installed_by = installer or "unknown"
        return _failure(
            "Could not identify a supported installer for this Tau environment "
            f"({prefix}). Package metadata reports: {installed_by}."
        )

    latest_version: str | None = None
    if method == "uv-tool":
        try:
            latest_version = latest_version_fetcher()
        except Exception as exc:  # noqa: BLE001 - report update lookup failures to the user
            return _failure(f"Could not determine the latest Tau version from PyPI: {exc}")
        if latest_version is None:
            return _failure("Could not determine the latest Tau version from PyPI.")

    executable = python_executable or sys.executable
    command = _update_command(method, executable, latest_version=latest_version)
    if (platform_name or sys.platform) == "win32" and method == "uv-tool":
        return _handoff_windows_update(
            command,
            launcher=detached_launcher,
            executable_finder=executable_finder,
            parent_pid=parent_pid or os.getpid(),
            handoff_directory=handoff_directory,
        )

    completed = _run(runner, command)
    if isinstance(completed, str):
        return _failure(f"{' '.join(command)}: {completed}")
    if completed.returncode != 0:
        return _failure(f"{' '.join(command)}: {_result_detail(completed)}")
    return UpdateResult(
        command=command,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )


def detect_install_method(prefix: Path, *, installer: str | None = None) -> InstallMethod | None:
    """Detect installer ownership from receipts and distribution metadata."""
    if (prefix / "uv-receipt.toml").is_file():
        return "uv-tool"
    if (prefix / "pipx_metadata.json").is_file():
        return "pipx"
    normalized_installer = installer.strip().lower() if installer else None
    if normalized_installer == "uv":
        return "uv-pip"
    if normalized_installer == "pip":
        return "pip"
    return None


def _update_command(
    method: InstallMethod,
    python_executable: str,
    *,
    latest_version: str | None = None,
) -> tuple[str, ...]:
    if method == "uv-tool":
        if latest_version is None:
            raise ValueError("latest_version is required for uv tool updates")
        return ("uv", "tool", "install", f"{PYPI_PACKAGE_NAME}@{latest_version}")
    if method == "uv-pip":
        return (
            "uv",
            "pip",
            "install",
            "--python",
            python_executable,
            "--upgrade",
            PYPI_PACKAGE_NAME,
        )
    if method == "pipx":
        return ("pipx", "upgrade", PYPI_PACKAGE_NAME)
    return (python_executable, "-m", "pip", "install", "--upgrade", PYPI_PACKAGE_NAME)


_WINDOWS_UPDATE_SCRIPT = """param(
    [Parameter(Mandatory=$true)][int]$ParentProcessId,
    [Parameter(Mandatory=$true)][string]$LogPath,
    [Parameter(Mandatory=$true)][string]$UpdateExecutable,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$UpdateArguments
)

$ScriptPath = $MyInvocation.MyCommand.Path
$UpdateExitCode = 1
try {
    "Waiting for Tau process $ParentProcessId to exit..." | Set-Content -LiteralPath $LogPath
    Wait-Process -Id $ParentProcessId -ErrorAction SilentlyContinue
    & $UpdateExecutable @UpdateArguments *>> $LogPath
    $UpdateExitCode = $LASTEXITCODE
    "Update command exited with code $UpdateExitCode." | Add-Content -LiteralPath $LogPath
} catch {
    $_ | Out-String | Add-Content -LiteralPath $LogPath
} finally {
    Remove-Item -LiteralPath $ScriptPath -Force -ErrorAction SilentlyContinue
}
exit $UpdateExitCode
"""


def _handoff_windows_update(
    command: tuple[str, ...],
    *,
    launcher: DetachedLauncher,
    executable_finder: ExecutableFinder,
    parent_pid: int,
    handoff_directory: Path | None,
) -> UpdateResult:
    powershell = executable_finder("powershell.exe") or executable_finder("pwsh.exe")
    if powershell is None:
        return _failure("Could not find PowerShell to safely finish the Windows update.")

    if handoff_directory is None:
        update_dir = Path(tempfile.mkdtemp(prefix="tau-update-"))
    else:
        update_dir = handoff_directory
        update_dir.mkdir(parents=True, exist_ok=True)
    script_path = update_dir / "update.ps1"
    log_path = update_dir / "update.log"
    script_path.write_text(_WINDOWS_UPDATE_SCRIPT, encoding="utf-8")
    powershell_command = (
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        str(parent_pid),
        str(log_path),
        *command,
    )
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    try:
        launcher(
            powershell_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        script_path.unlink(missing_ok=True)
        return _failure(f"Could not start the detached Windows updater: {exc}")
    return UpdateResult(
        command=command,
        stdout=(
            "Tau update is scheduled and will start after this process exits. "
            f"Progress will be written to {log_path}."
        ),
        deferred=True,
    )


def _installed_direct_url() -> str | None:
    raw = _distribution_file("direct_url.json")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "an unrecognized direct source"
    url = data.get("url") if isinstance(data, dict) else None
    return url if isinstance(url, str) and url else "an unrecognized direct source"


def _installed_installer() -> str | None:
    raw = _distribution_file("INSTALLER")
    if not raw:
        return None
    return raw.strip() or None


def _distribution_file(filename: str) -> str | None:
    try:
        return distribution(PYPI_PACKAGE_NAME).read_text(filename)
    except PackageNotFoundError:
        return None


def _run(runner: CommandRunner, command: tuple[str, ...]) -> CompletedProcess[str] | str:
    try:
        return runner(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return str(exc)


def _result_detail(result: CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"


def _failure(message: str) -> UpdateResult:
    return UpdateResult(command=None, failures=(message,))
