"""External-editor integration for the Tau prompt composer."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path


class ExternalEditorError(RuntimeError):
    """Raised when the configured external editor cannot update the prompt."""


def resolve_external_editor_command(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the editor using Pi-compatible environment fallbacks."""
    environment = os.environ if environ is None else environ
    for variable in ("VISUAL", "EDITOR"):
        configured = environment.get(variable)
        if configured and configured.strip():
            return configured.strip()
    return "notepad" if os.name == "nt" else "nano"


def edit_prompt_in_external_editor(
    text: str,
    *,
    editor_command: str | None = None,
) -> str:
    """Edit *text* in a temporary Markdown file and return the saved content.

    The editor receives the temporary path as its final argument. A non-zero
    exit or launch failure raises :class:`ExternalEditorError`, allowing the
    caller to preserve the original prompt.
    """
    command = editor_command or resolve_external_editor_command()
    try:
        arguments = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise ExternalEditorError(f"Invalid external editor command: {exc}") from exc
    if not arguments:
        raise ExternalEditorError("External editor command is empty")

    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="tau-editor-",
            suffix=".tau.md",
            delete=False,
        ) as temporary:
            temporary.write(text)
            path = Path(temporary.name)

        try:
            result = subprocess.run([*arguments, str(path)], check=False)
        except OSError as exc:
            raise ExternalEditorError(
                f"Could not launch external editor {arguments[0]!r}: {exc}"
            ) from exc
        if result.returncode != 0:
            raise ExternalEditorError(f"External editor exited with code {result.returncode}")

        edited = path.read_text(encoding="utf-8")
        if edited.endswith("\r\n"):
            return edited[:-2]
        if edited.endswith("\n"):
            return edited[:-1]
        return edited
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
