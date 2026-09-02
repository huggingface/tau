from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tau_coding.extension_installer import (
    ExtensionInstallError,
    install_extension,
    parse_git_extension_source,
)
from tau_coding.extensions import discover_extensions
from tau_coding.resources import TauResourcePaths


def test_install_local_extension_file_is_discovered(tmp_path: Path) -> None:
    source = tmp_path / "hello.py"
    source.write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    extensions_dir = tmp_path / "home" / "extensions"

    destination = install_extension(str(source), extensions_dir=extensions_dir)

    assert destination == extensions_dir / "hello.py"
    paths = TauResourcePaths(root=tmp_path / "home", cwd=tmp_path)
    discovered, diagnostics = discover_extensions(paths)
    assert [entry.name for entry in discovered] == ["hello"]
    assert diagnostics == ()


def test_install_local_package_copies_manifest_layout(tmp_path: Path) -> None:
    source = tmp_path / "my-extension"
    entry = source / "src" / "my_extension" / "extension.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("from . import helper\n\ndef setup(tau):\n    pass\n", encoding="utf-8")
    (entry.parent / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        '[tool.tau]\nextensions = ["src/my_extension/extension.py"]\n',
        encoding="utf-8",
    )
    extensions_dir = tmp_path / "home" / "extensions"

    destination = install_extension(str(source), extensions_dir=extensions_dir)

    assert (destination / "src" / "my_extension" / "helper.py").is_file()
    paths = TauResourcePaths(root=tmp_path / "home", cwd=tmp_path)
    discovered, diagnostics = discover_extensions(paths)
    assert [item.name for item in discovered] == ["my_extension"]
    assert diagnostics == ()


@pytest.mark.parametrize("nested_entry", ["one.py", "nested/extension.py"])
def test_install_rejects_directory_that_would_not_be_auto_discovered(
    tmp_path: Path, nested_entry: str
) -> None:
    source = tmp_path / "nested-only"
    entry = source / nested_entry
    entry.parent.mkdir(parents=True)
    entry.write_text("def setup(tau):\n    pass\n", encoding="utf-8")

    with pytest.raises(ExtensionInstallError, match="extension.py"):
        install_extension(str(source), extensions_dir=tmp_path / "extensions")


def test_install_preserves_existing_extension_without_force(tmp_path: Path) -> None:
    source = tmp_path / "hello.py"
    source.write_text("new\n", encoding="utf-8")
    extensions_dir = tmp_path / "extensions"
    extensions_dir.mkdir()
    installed = extensions_dir / "hello.py"
    installed.write_text("old\n", encoding="utf-8")

    with pytest.raises(ExtensionInstallError, match="already installed"):
        install_extension(str(source), extensions_dir=extensions_dir)

    assert installed.read_text(encoding="utf-8") == "old\n"


def test_install_force_replaces_existing_extension(tmp_path: Path) -> None:
    source = tmp_path / "hello.py"
    source.write_text("new\n", encoding="utf-8")
    extensions_dir = tmp_path / "extensions"
    extensions_dir.mkdir()
    installed = extensions_dir / "hello.py"
    installed.write_text("old\n", encoding="utf-8")

    destination = install_extension(str(source), extensions_dir=extensions_dir, force=True)

    assert destination.read_text(encoding="utf-8") == "new\n"
    assert not (extensions_dir / ".hello.py.backup").exists()


@pytest.mark.parametrize(
    ("source", "url", "ref", "name"),
    [
        (
            "git:github.com/rian-dolphin/tau-subagents@v1.2.0",
            "https://github.com/rian-dolphin/tau-subagents",
            "v1.2.0",
            "tau-subagents",
        ),
        (
            "https://github.com/rian-dolphin/tau-subagents.git",
            "https://github.com/rian-dolphin/tau-subagents.git",
            None,
            "tau-subagents",
        ),
        (
            "git:git@github.com:rian-dolphin/tau-subagents.git@abc123",
            "git@github.com:rian-dolphin/tau-subagents.git",
            "abc123",
            "tau-subagents",
        ),
    ],
)
def test_parse_git_extension_source(source: str, url: str, ref: str | None, name: str) -> None:
    parsed = parse_git_extension_source(source)

    assert (parsed.url, parsed.ref, parsed.name) == (url, ref, name)


def test_missing_git_is_reported_as_install_error(tmp_path: Path) -> None:
    def missing_git(
        command: list[str], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        del command, capture_output, text, check
        raise FileNotFoundError("git executable not found")

    with pytest.raises(ExtensionInstallError, match="git executable not found"):
        install_extension(
            "git:github.com/example/tau-demo",
            extensions_dir=tmp_path / "extensions",
            command_runner=missing_git,
        )


def test_failed_git_install_removes_partial_clone(tmp_path: Path) -> None:
    extensions_dir = tmp_path / "extensions"

    def fake_run(
        command: list[str], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check
        Path(command[-1]).mkdir()
        return subprocess.CompletedProcess(command, 1, "", "network failed")

    with pytest.raises(ExtensionInstallError, match="network failed"):
        install_extension(
            "git:github.com/example/tau-demo",
            extensions_dir=extensions_dir,
            command_runner=fake_run,
        )

    assert list(extensions_dir.iterdir()) == []


def test_install_git_source_clones_and_checks_out_ref(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check
        calls.append(command)
        if command[1] == "clone":
            destination = Path(command[-1])
            destination.mkdir()
            (destination / "extension.py").write_text(
                "def setup(tau):\n    pass\n", encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    destination = install_extension(
        "git:github.com/example/tau-demo@v1",
        extensions_dir=tmp_path / "extensions",
        command_runner=fake_run,
    )

    assert destination == tmp_path / "extensions" / "tau-demo"
    assert calls[0][:4] == ["git", "clone", "--", "https://github.com/example/tau-demo"]
    assert calls[1][0:2] == ["git", "-C"]
    assert Path(calls[1][2]).name == destination.name
    assert calls[1][3:] == ["checkout", "--detach", "v1"]
