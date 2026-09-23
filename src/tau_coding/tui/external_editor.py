"""Resolve and invoke an external editor for the interactive prompt."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ExternalEditorError(RuntimeError):
    """Raised when the external editor cannot be resolved or fails."""


@dataclass(frozen=True, slots=True)
class ExternalEditorResult:
    """Outcome of one external-editor invocation."""

    text: str


def resolve_editor_command() -> list[str]:
    """Return the argv prefix for the user's editor.

    Checks ``$VISUAL`` then ``$EDITOR`` (the classic Unix convention used by
    git, crontab, etc.), then falls back to a platform default. Values may
    include arguments (for example ``"code --wait"``) and are split with
    shell rules so quoting still works.
    """
    for var in ("VISUAL", "EDITOR"):
        raw = os.environ.get(var, "").strip()
        if raw:
            parts = shlex.split(raw, posix=(os.name != "nt"))
            if parts:
                return parts
    return ["notepad"] if os.name == "nt" else ["vi"]


def edit_text_in_external_editor(initial_text: str) -> ExternalEditorResult:
    """Open *initial_text* in the resolved editor and return the saved text.

    Blocks until the editor process exits. Raises :class:`ExternalEditorError`
    if the editor cannot be started or exits with a nonzero status; the
    caller is responsible for suspending/resuming the terminal UI around this
    call, since this module has no Textual dependency.
    """
    editor_command = resolve_editor_command()
    with tempfile.TemporaryDirectory(prefix="tau-editor-") as tmp_dir:
        tmp_path = Path(tmp_dir) / "prompt.md"
        tmp_path.write_text(initial_text, encoding="utf-8")
        try:
            completed = subprocess.run([*editor_command, str(tmp_path)])
        except OSError as exc:
            raise ExternalEditorError(
                f"Could not start editor {editor_command[0]!r}: {exc}"
            ) from exc
        if completed.returncode != 0:
            raise ExternalEditorError(
                f"Editor {editor_command[0]!r} exited with status {completed.returncode}"
            )
        saved_text = tmp_path.read_text(encoding="utf-8")
    return ExternalEditorResult(text=saved_text.rstrip("\n"))
