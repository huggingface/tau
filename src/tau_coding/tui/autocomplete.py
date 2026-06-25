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
    category: str | None = None

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
    if not text.startswith("/") or text.startswith("//"):
        if cwd is not None:
            shell_completions = _shell_path_completions(text=text, cwd=cwd)
            if shell_completions is not None:
                return CompletionState(shell_completions)
            return CompletionState(_file_reference_completions(text=text, cwd=cwd))
        return CompletionState()

    token_end = _first_token_end(text)
    token = text[:token_end]
    has_argument_text = token_end < len(text)
    if token.startswith("/skill:"):
        if has_argument_text and _matches_skill_command(token, skills):
            return CompletionState()
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

    if has_argument_text and (
        _matches_prompt_template_command(token, prompt_templates)
        or _matches_registered_command(token, command_registry)
    ):
        return CompletionState()

    return CompletionState(
        _command_completions(
            token=token,
            token_end=token_end,
            registry=command_registry,
            prompt_templates=prompt_templates,
        )
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


def _shell_path_completions(*, text: str, cwd: Path) -> tuple[CompletionItem, ...] | None:
    prefix_span = _shell_command_prefix_span(text)
    if prefix_span is None:
        return None

    start, end = _active_shell_path_token(text=text, command_start=prefix_span[1])
    token = text[start:end]
    if not token:
        return ()

    shell_path = _parse_shell_path_token(token)
    if shell_path is None:
        return ()
    parent_text, name_prefix, replacement_prefix = shell_path

    parent_dir = cwd / parent_text if parent_text else cwd
    if not parent_dir.exists() or not parent_dir.is_dir():
        return ()
    if parent_dir != cwd and _is_ignored_file_completion_path(parent_dir, cwd=cwd):
        return ()

    try:
        children = sorted(parent_dir.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return ()

    suggestions: list[CompletionItem] = []
    for child in children:
        if _is_ignored_file_completion_path(child, cwd=cwd):
            continue
        if not child.name.lower().startswith(name_prefix.lower()):
            continue
        relative = child.relative_to(cwd).as_posix()
        replacement = f"{replacement_prefix}{relative}{'/' if child.is_dir() else ''}"
        if replacement == token:
            continue
        suggestions.append(
            CompletionItem(
                display=replacement,
                replacement=replacement,
                start=start,
                end=end,
                description="Directory" if child.is_dir() else "File",
            )
        )
        if len(suggestions) >= MAX_FILE_COMPLETIONS:
            break
    return tuple(suggestions)


def _shell_command_prefix_span(text: str) -> tuple[int, int] | None:
    leading_whitespace = len(text) - len(text.lstrip())
    stripped = text[leading_whitespace:]
    if stripped.startswith("!!"):
        return (leading_whitespace, leading_whitespace + 2)
    if stripped.startswith("!"):
        return (leading_whitespace, leading_whitespace + 1)
    return None


def _active_shell_path_token(*, text: str, command_start: int) -> tuple[int, int]:
    cursor = len(text)
    token_start = command_start
    escaped = False
    for index in range(cursor - 1, command_start - 1, -1):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char.isspace():
            token_start = index + 1
            break
    return token_start, cursor


def _parse_shell_path_token(token: str) -> tuple[str, str, str] | None:
    replacement_prefix = ""
    path_text = token
    if path_text.startswith("./"):
        replacement_prefix = "./"
        path_text = path_text[2:]
    if path_text.startswith(("/", "~")):
        return None
    if any(char in path_text for char in "\"'`$*?[{"):
        return None

    parent_text, separator, name_prefix = path_text.rpartition("/")
    if separator and not parent_text:
        return None

    parent_parts = parent_text.split("/") if parent_text else []
    if any(part in {"", ".", ".."} for part in parent_parts):
        return None
    return parent_text, name_prefix, replacement_prefix


def _matches_skill_command(token: str, skills: Sequence[Skill]) -> bool:
    command_name = token.removeprefix("/skill:").lower()
    return any(skill.name.lower() == command_name for skill in skills)


def _matches_prompt_template_command(
    token: str, prompt_templates: Sequence[PromptTemplate]
) -> bool:
    command_name = token.removeprefix("/").lower()
    return any(template.name.lower() == command_name for template in prompt_templates)


def _matches_registered_command(token: str, registry: CommandRegistry) -> bool:
    command_name = token.removeprefix("/").lower()
    return registry.get(command_name) is not None


def _command_completions(
    *,
    token: str,
    token_end: int,
    registry: CommandRegistry,
    prompt_templates: Sequence[PromptTemplate],
) -> tuple[CompletionItem, ...]:
    prefix = token.removeprefix("/").lower()
    command_suggestions: list[CompletionItem] = []
    for command in registry.list_commands():
        command_suggestions.extend(
            _command_alias_completions(command, prefix=prefix, token_end=token_end)
        )
    prompt_suggestions = [
        CompletionItem(
            display=f"/{template.name}",
            replacement=f"/{template.name}",
            start=0,
            end=token_end,
            description=template.description or "Prompt template",
            category="Custom prompts",
        )
        for template in prompt_templates
        if template.name.lower().startswith(prefix)
    ]
    return (
        *sorted(command_suggestions, key=lambda item: _command_completion_sort_key(item, prefix)),
        *sorted(prompt_suggestions, key=lambda item: _command_completion_sort_key(item, prefix)),
    )


def _command_completion_sort_key(item: CompletionItem, prefix: str) -> tuple[int, str]:
    if not prefix:
        return (0, item.display)
    display_name = item.display.removeprefix("/").removesuffix(":").lower()
    direct_match_rank = 0 if display_name.startswith(prefix) else 1
    return (direct_match_rank, item.display)


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
                category="Commands",
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
    if command_name in {"login", "logout"}:
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
