"""Tests for .gitignore parsing."""

from __future__ import annotations

from pathlib import Path

from tau_coding.gitignore import load_gitignore_rules


def test_load_gitignore_rules_matches_directory_and_extension(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "build/\n*.log\n",
        encoding="utf-8",
    )
    rules = load_gitignore_rules(tmp_path)

    assert rules.matches("build", is_dir=True)
    assert rules.matches("build/output.txt", is_dir=False)
    assert rules.matches("notes.log", is_dir=False)
    assert not rules.matches("src/main.py", is_dir=False)


def test_gitignore_rules_ignore_missing_file(tmp_path: Path) -> None:
    rules = load_gitignore_rules(tmp_path)
    assert not rules.matches("README.md", is_dir=False)
