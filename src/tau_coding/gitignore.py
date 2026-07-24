"""Parse project .gitignore files for path filtering."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GitignoreRules:
    """Compiled ignore rules from a project root .gitignore file."""

    ignored_dirs: frozenset[str]
    ignored_ext: frozenset[str]
    patterns: tuple[tuple[str, bool], ...]

    def matches(self, relative_path: str, *, is_dir: bool) -> bool:
        """Return whether a cwd-relative path should be ignored."""
        parts = relative_path.split("/")
        for index in range(len(parts)):
            segment_path = "/".join(parts[: index + 1])
            segment_is_dir = index < len(parts) - 1 or is_dir
            basename = parts[index]
            if segment_is_dir and basename in self.ignored_dirs:
                return True
            if (
                not segment_is_dir
                and index == len(parts) - 1
                and any(basename.endswith(ext) for ext in self.ignored_ext)
            ):
                return True
            for pattern, dir_only in self.patterns:
                if dir_only and not segment_is_dir:
                    continue
                if _match_gitignore_pattern(pattern, segment_path, is_dir=segment_is_dir):
                    return True
        return False


def load_gitignore_rules(project_root: Path) -> GitignoreRules:
    """Load ignore rules from ``project_root/.gitignore``."""
    gitignore_path = project_root / ".gitignore"
    if not gitignore_path.is_file():
        return GitignoreRules(frozenset(), frozenset(), ())

    ignored_dirs: set[str] = set()
    ignored_ext: set[str] = set()
    patterns: list[tuple[str, bool]] = []
    for raw_line in gitignore_path.read_text(encoding="utf-8").splitlines():
        parsed = _classify_gitignore_line(raw_line)
        if parsed is None:
            continue
        pattern, dir_only = parsed
        patterns.append((pattern, dir_only))
        if dir_only and "/" not in pattern:
            ignored_dirs.add(pattern)
        if not dir_only and pattern.startswith("*.") and "/" not in pattern:
            ignored_ext.add(pattern[1:])

    return GitignoreRules(frozenset(ignored_dirs), frozenset(ignored_ext), tuple(patterns))


@lru_cache(maxsize=32)
def gitignore_rules_for(project_root: str) -> GitignoreRules:
    """Return cached gitignore rules for a project root path string."""
    return load_gitignore_rules(Path(project_root))


def _classify_gitignore_line(raw_line: str) -> tuple[str, bool] | None:
    line = raw_line.strip()
    if not line or line.startswith("#") or line.startswith("!"):
        return None
    if line.startswith("\\#"):
        line = line[1:]
    if line.startswith("\\!"):
        line = line[1:]
    if line.startswith("/"):
        line = line[1:]
    dir_only = line.endswith("/")
    if dir_only:
        line = line[:-1]
    if not line:
        return None
    return line, dir_only


def _match_gitignore_pattern(pattern: str, path: str, *, is_dir: bool) -> bool:
    normalized = path.replace("\\", "/")
    if "/" not in pattern:
        basename = normalized.rsplit("/", 1)[-1]
        if fnmatch.fnmatch(basename, pattern):
            return True
        if is_dir:
            return any(fnmatch.fnmatch(part, pattern) for part in normalized.split("/"))
        return False
    return fnmatch.fnmatch(normalized, pattern) or normalized.startswith(f"{pattern}/")
