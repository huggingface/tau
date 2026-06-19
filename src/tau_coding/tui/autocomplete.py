"""Prompt autocomplete helpers for Tau's Textual TUI."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tau_coding.commands import CommandRegistry, SlashCommand
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.skills import Skill

IGNORED_FILE_COMPLETION_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tau",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)
MAX_FILE_COMPLETIONS = 50


@dataclass(frozen=True, slots=True)
class CompletionOption:
    """A possible argument completion value with optional picker metadata."""

    value: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CompletionItem:
    """One selectable prompt completion."""

    display: str
    replacement: str
    start: int
    end: int
    description: str | None = None

    def apply(self, text: str) -> str:
        """Apply this completion to input text."""
        return f"{text[: self.start]}{self.replacement}{text[self.end :]}"


@dataclass(frozen=True, slots=True)
class CompletionState:
    """Current autocomplete state for the prompt input."""

    items: tuple[CompletionItem, ...] = ()
    selected_index: int = 0

    @property
    def selected(self) -> CompletionItem | None:
        """Return the currently selected completion item."""
        if not self.items:
            return None
        return self.items[self.selected_index]

    def select_next(self) -> CompletionState:
        """Return a state with the next item selected."""
        if not self.items:
            return self
        return CompletionState(
            items=self.items,
            selected_index=(self.selected_index + 1) % len(self.items),
        )

    def select_previous(self) -> CompletionState:
        """Return a state with the previous item selected."""
        if not self.items:
            return self
        return CompletionState(
            items=self.items,
            selected_index=(self.selected_index - 1) % len(self.items),
        )


def build_completion_state(
    text: str,
    *,
    command_registry: CommandRegistry,
    skills: Sequence[Skill],
    prompt_templates: Sequence[PromptTemplate],
    model_names: Sequence[str] = (),
    provider_names: Sequence[str] = (),
    thinking_levels: Sequence[str] = (),
    theme_names: Sequence[str] = (),
    session_ids: Sequence[str] = (),
    session_options: Sequence[CompletionOption] = (),
    cwd: Path | None = None,
) -> CompletionState:
    """Build autocomplete suggestions for the current prompt text."""
    del prompt_templates
    if not text.startswith("/") or text.startswith("//"):
        if cwd is not None:
            return CompletionState(_file_reference_completions(text=text, cwd=cwd))
        return CompletionState()

    token_end = _first_token_end(text)
    token = text[:token_end]
    if token.startswith("/skill:"):
        return CompletionState(_skill_completions(token=token, token_end=token_end, skills=skills))

    if ":" in token:
        return CompletionState()

    argument_completions = _command_argument_completions(
        text=text,
        token_end=token_end,
        model_names=model_names,
        provider_names=provider_names,
        thinking_levels=thinking_levels,
        theme_names=theme_names,
        session_ids=session_ids,
        session_options=session_options,
    )
    if argument_completions is not None:
        return CompletionState(argument_completions)

    return CompletionState(
        _command_completions(token=token, token_end=token_end, registry=command_registry)
    )


def _file_reference_completions(*, text: str, cwd: Path) -> tuple[CompletionItem, ...]:
    token = _active_file_reference_token(text)
    if token is None:
        return ()
    start, end = token
    prefix = text[start + 1 : end]
    suggestions: list[CompletionItem] = []
    for path in _iter_file_reference_paths(cwd):
        relative = path.relative_to(cwd).as_posix()
        if prefix.lower() not in relative.lower():
            continue
        display = f"@{relative}{'/' if path.is_dir() else ''}"
        suggestions.append(
            CompletionItem(
                display=display,
                replacement=display,
                start=start,
                end=end,
                description="File reference",
            )
        )
        if len(suggestions) >= MAX_FILE_COMPLETIONS:
            break
    return tuple(suggestions)


def _active_file_reference_token(text: str) -> tuple[int, int] | None:
    cursor = len(text)
    token_start = max(text.rfind(" ", 0, cursor), text.rfind("\n", 0, cursor)) + 1
    at_index = text.rfind("@", token_start, cursor)
    if at_index == -1:
        return None
    return at_index, cursor


def _iter_file_reference_paths(cwd: Path) -> tuple[Path, ...]:
    if not cwd.exists() or not cwd.is_dir():
        return ()
    paths: list[Path] = []
    stack = [cwd]
    while stack:
        directory = stack.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            continue
        for child in children:
            if _is_ignored_file_completion_path(child, cwd=cwd):
                continue
            paths.append(child)
            if child.is_dir():
                stack.append(child)
    return tuple(paths)


def _is_ignored_file_completion_path(path: Path, *, cwd: Path) -> bool:
    try:
        relative_parts = path.relative_to(cwd).parts
    except ValueError:
        return True
    return any(
        part.startswith(".") or part in IGNORED_FILE_COMPLETION_DIRS for part in relative_parts
    )


def _command_completions(
    *, token: str, token_end: int, registry: CommandRegistry
) -> tuple[CompletionItem, ...]:
    prefix = token.removeprefix("/").lower()
    suggestions: list[CompletionItem] = []
    for command in registry.list_commands():
        suggestions.extend(_command_alias_completions(command, prefix=prefix, token_end=token_end))
    return tuple(sorted(suggestions, key=lambda item: item.display))


def _command_alias_completions(
    command: SlashCommand, *, prefix: str, token_end: int
) -> list[CompletionItem]:
    names = (
        (command.name,) if not prefix else (command.name, *command.aliases, *command.search_terms)
    )
    suggestions: list[CompletionItem] = []
    seen: set[str] = set()
    for name in names:
        if not name.startswith(prefix):
            continue
        replacement_name = name if name in (command.name, *command.aliases) else command.name
        display = f"/{replacement_name}"
        replacement = f"/{replacement_name}"
        if command.name == "skill" and replacement_name == command.name:
            display = "/skill:"
            replacement = "/skill:"
        if display in seen:
            continue
        seen.add(display)
        suggestions.append(
            CompletionItem(
                display=display,
                replacement=replacement,
                start=0,
                end=token_end,
                description=command.description,
            )
        )
    return suggestions


def _skill_completions(
    *, token: str, token_end: int, skills: Sequence[Skill]
) -> tuple[CompletionItem, ...]:
    prefix = token.removeprefix("/skill:").lower()
    suggestions = [
        CompletionItem(
            display=f"/skill:{skill.name}",
            replacement=f"/skill:{skill.name}",
            start=0,
            end=token_end,
            description=skill.description,
        )
        for skill in sorted(skills, key=lambda item: item.name)
        if skill.name.lower().startswith(prefix)
    ]
    return tuple(suggestions)


def _command_argument_completions(
    *,
    text: str,
    token_end: int,
    model_names: Sequence[str],
    provider_names: Sequence[str],
    thinking_levels: Sequence[str],
    theme_names: Sequence[str],
    session_ids: Sequence[str],
    session_options: Sequence[CompletionOption],
) -> tuple[CompletionItem, ...] | None:
    if token_end >= len(text):
        return None

    command_name = text[:token_end].removeprefix("/").lower()
    if command_name in {"model", "scoped-models"}:
        return _value_completions(
            text=text,
            start=token_end + 1,
            options=_completion_options(model_names, description="Switch model"),
            sort=True,
        )
    if command_name == "login":
        return _value_completions(
            text=text,
            start=token_end + 1,
            options=_completion_options(provider_names, description="Switch provider"),
            sort=True,
        )
    if command_name == "resume":
        return _value_completions(
            text=text,
            start=token_end + 1,
            options=(
                session_options
                if session_options
                else _completion_options(session_ids, description="Resume session")
            ),
            sort=False,
        )
    if command_name == "theme":
        return _value_completions(
            text=text,
            start=token_end + 1,
            options=_completion_options(theme_names, description="Set TUI theme"),
            sort=False,
        )
    return None


def _value_completions(
    *,
    text: str,
    start: int,
    options: Sequence[CompletionOption],
    sort: bool,
) -> tuple[CompletionItem, ...]:
    end = _argument_token_end(text, start)
    prefix = text[start:end].lower()
    ordered_options = sorted(options, key=lambda item: item.value) if sort else options
    return tuple(
        CompletionItem(
            display=option.value,
            replacement=option.value,
            start=start,
            end=end,
            description=option.description,
        )
        for option in ordered_options
        if option.value.lower().startswith(prefix)
    )


def _completion_options(
    values: Sequence[str],
    *,
    description: str,
) -> tuple[CompletionOption, ...]:
    return tuple(CompletionOption(value=value, description=description) for value in values)


def _first_token_end(text: str) -> int:
    separator = text.find(" ")
    return len(text) if separator == -1 else separator


def _argument_token_end(text: str, start: int) -> int:
    separator = text.find(" ", start)
    return len(text) if separator == -1 else separator
