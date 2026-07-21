"""Upgrade the installed Tau CLI with common Python tool managers."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from subprocess import CompletedProcess, run

from tau_coding.update_check import PYPI_PACKAGE_NAME

CommandRunner = Callable[..., CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Result of trying to upgrade Tau."""

    command: tuple[str, ...] | None
    stdout: str = ""
    stderr: str = ""
    failures: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.command is not None


def update_tau(
    *,
    runner: CommandRunner = run,
    python_executable: str | None = None,
    commands: Sequence[Sequence[str]] | None = None,
) -> UpdateResult:
    """Upgrade Tau, trying uv, pipx, then the current interpreter's pip.

    A failed or unavailable installer falls through to the next option. Output from
    unsuccessful attempts is kept for the final diagnostic instead of cluttering the
    successful update path.
    """
    candidates = commands or (
        ("uv", "tool", "upgrade", PYPI_PACKAGE_NAME),
        ("pipx", "upgrade", PYPI_PACKAGE_NAME),
        (
            python_executable or sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            PYPI_PACKAGE_NAME,
        ),
    )
    failures: list[str] = []
    for candidate in candidates:
        command = tuple(candidate)
        try:
            result = runner(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            failures.append(f"{' '.join(command)}: {exc}")
            continue
        if result.returncode == 0:
            return UpdateResult(
                command=command,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                failures=tuple(failures),
            )
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        failures.append(f"{' '.join(command)}: {detail}")
    return UpdateResult(command=None, failures=tuple(failures))
