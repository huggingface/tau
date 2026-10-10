"""Cross-session learning loop for Tau coding sessions.

A compact port of Hermes Agent's learning loop:

- ``~/.tau/MEMORY.md`` — a small, durable, declarative memory file. Entries are
  joined by a ``§`` delimiter and injected into the system prompt as a frozen
  snapshot per session (budgeted, so it stays cheap).
- ``~/.tau/lessons/`` — durable lesson files written after productive runs,
  loaded like skills via the existing skill loader.
- An LLM "curator" pass that reviews a settled session transcript and writes
  both, using the existing skill directory format so ``/reload`` picks new
  lessons up without any extra plumbing.

Writes are always additive-or-replace; the curator never deletes existing
lessons or memory entries, mirroring Hermes' "never delete, only archive"
invariant. All paths resolve through ``TauPaths`` so tests redirect home.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_agent.messages import AgentMessage
from tau_coding.paths import TauPaths

logger = logging.getLogger(__name__)

ENTRY_DELIMITER = "\n§\n"
DEFAULT_MEMORY_CHAR_LIMIT = 2200
DEFAULT_MAX_LESSONS_PER_RUN = 3
LESSONS_DIRNAME = "lessons"  # ~/.tau/lessons/ — top-level, NOT under skills/
LEARNED_PROMPT_SECTION_TITLE = "Learned memory"
LEARNED_PROMPT_CHAR_LIMIT = 2600
MAX_LESSON_BODY_CHARS = 4000
MAX_MEMORY_ENTRY_CHARS = 400
MAX_TRANSCRIPT_CHARS = 24_000

REVIEW_SYSTEM_PROMPT = """\
You are the background learning curator for Tau, a terminal coding agent.
You review one settled session transcript and decide what is worth remembering
for FUTURE sessions in this same project. You write files; you do not chat.

Output ONLY one JSON object, no prose, no code fences:
{
  "memory_entries": ["<declarative fact, <= 200 chars, imperative-free>"],
  "lessons": [
    {
      "name": "<kebab-or-snake-case, <= 48 chars>",
      "description": "<one sentence, <= 80 chars, what+when>",
      "body": "<markdown lesson, <= 3000 chars>"
    }
  ]
}

MEMORY ENTRIES — add ONLY facts that apply to EVERY future session with this
user or project, regardless of task. Good: environment quirks ("the repo's test
command is uv run pytest -q"), stable user preferences ("user prefers concise
responses"), durable project conventions. Bad: task-specific findings, anything
already in AGENTS.md or project docs, conversation narration, anything stale
within days. Write declarative statements, never instructions to yourself.
Omit memory_entries entirely (or []) when nothing qualifies — that is the
normal outcome. Never exceed ~5 entries; if the store is near its topic limit,
return only genuinely new or corrective facts.

LESSONS — capture a durable procedure, pitfall, or correction only when the
session hit it non-trivially (a non-obvious root cause, a workaround that
worked, a repeated edit pattern, a maintainer-preferred approach). Write
lessons, not logs: imperative rules plus one clause of why; drop dates, issue
numbers, and quoted chatter — the rule must stand without the story. One rule
per lesson. If nothing clears that bar, return no lessons. At most 3.
When a lesson clearly extends an existing lesson file, prefer returning its
UPDATED full body in `body` with the same `name`, so the curator rewrites that
file instead of creating a near-duplicate.
"""


@dataclass(frozen=True, slots=True)
class MemoryStorePaths:
    """Resolved durable-store paths for one session."""

    memory_path: Path
    lessons_dir: Path


@dataclass(frozen=True, slots=True)
class CuratorSuggestion:
    """A lesson (or lesson update) proposed by the curator pass."""

    name: str
    description: str
    body: str
    replaces_existing: bool = False


@dataclass(frozen=True, slots=True)
class CuratorResult:
    """Outcome of one curator review run."""

    memory_entries: tuple[str, ...] = ()
    lessons: tuple[CuratorSuggestion, ...] = ()
    error: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Lesson:
    """One durable lesson file discovered under the lessons directory."""

    name: str
    description: str
    path: Path


@dataclass(frozen=True, slots=True)
class LearnedContext:
    """Frozen prompt snapshot of durable learned state for one session."""

    memory_text: str | None
    lessons: tuple[Lesson, ...]

    def render(self) -> str | None:
        """Render the learned-context prompt section, or None when empty."""
        if not self.memory_text and not self.lessons:
            return None
        lines: list[str] = []
        if self.memory_text:
            lines.append(
                "Persistent memory about this user and project, carried across "
                "sessions (apply it; do not repeat it back):"
            )
            lines.append(self.memory_text)
        if self.lessons:
            lines.append(
                "\nLessons learned in previous sessions for this project. When a "
                "task matches one, read the lesson file at its listed path "
                "before proceeding:"
            )
            for lesson in self.lessons:
                lines.append(f"- {lesson.name}: {lesson.description} ({lesson.path})")
        text = "\n".join(lines)
        if len(text) > LEARNED_PROMPT_CHAR_LIMIT:
            text = text[: LEARNED_PROMPT_CHAR_LIMIT - 3].rstrip() + "..."
        return text


def resolve_store_paths(paths: TauPaths | None = None) -> MemoryStorePaths:
    """Resolve the durable memory/lesson paths under the Tau home."""
    tau_paths = paths or TauPaths()
    home = tau_paths.home
    return MemoryStorePaths(
        memory_path=home / "MEMORIES.md",
        lessons_dir=home / LESSONS_DIRNAME,
    )


def load_memory_entries(
    memory_path: Path,
    char_limit: int = DEFAULT_MEMORY_CHAR_LIMIT,
) -> tuple[str, ...]:
    """Load §-delimited memory entries, most recent first, within budget."""
    if not memory_path.exists():
        return ()
    try:
        raw = memory_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Could not read memory file %s: %s", memory_path, exc)
        return ()
    entries = [entry.strip() for entry in raw.split(ENTRY_DELIMITER) if entry.strip()]
    budgeted: list[str] = []
    remaining = char_limit
    for entry in reversed(entries):
        if remaining <= 0:
            break
        if len(entry) > remaining:
            break
        budgeted.insert(0, entry)
        remaining -= len(entry) + len(ENTRY_DELIMITER)
    return tuple(budgeted)


def memory_char_usage(memory_path: Path) -> int:
    """Return the current memory store size in characters (0 without a file)."""
    if not memory_path.exists():
        return 0
    try:
        return len(memory_path.read_text(encoding="utf-8"))
    except OSError:
        return 0


def append_memory_entries(
    memory_path: Path,
    entries: tuple[str, ...],
    char_limit: int = DEFAULT_MEMORY_CHAR_LIMIT,
) -> tuple[str, ...]:
    """Append memory entries atomically; return the entries actually appended.

    Existing entries are matched case-insensitively so a rephrased duplicate
    does not double in. When the store is full, the call is rejected with a
    message showing current usage — Hermes' reject-and-show semantics.
    """
    existing = load_memory_entries(memory_path, char_limit=char_limit)
    existing_keys = {entry.casefold() for entry in existing}
    accepted: list[str] = []
    for entry in entries:
        normalized = entry.strip()
        if not normalized or normalized.casefold() in existing_keys:
            continue
        if len(normalized) > MAX_MEMORY_ENTRY_CHARS:
            normalized = normalized[: MAX_MEMORY_ENTRY_CHARS - 3].rstrip() + "..."
        accepted.append(normalized)
        existing_keys.add(normalized.casefold())
    if not accepted:
        return ()

    current = memory_char_usage(memory_path)
    addition = sum(len(entry) + len(ENTRY_DELIMITER) for entry in accepted)
    if current + addition > char_limit:
        raise ValueError(
            "Memory store is full "
            f"({current}/{char_limit} chars); consolidate MEMORY.md first. "
            f"Rejected entries: {accepted}"
        )

    prefix = ""
    if memory_path.exists():
        previous = memory_path.read_text(encoding="utf-8")
        if previous and not previous.endswith("\n"):
            prefix = "\n"
        if previous.strip():
            prefix = prefix + ENTRY_DELIMITER
    _atomic_write(memory_path, prefix + ENTRY_DELIMITER.join(accepted))
    return tuple(accepted)


def load_learned_lessons(
    lessons_dir: Path,
) -> tuple[Lesson, ...]:
    """Load lessons from the lessons directory.

    Only ``<dir>/<name>/SKILL.md`` files are lessons (one level deep,
    mirroring the skill loader's layout). Returns ``Lesson`` records with
    their absolute paths so the prompt can point the agent at the exact
    file; missing descriptions fall back to the first non-empty body line.
    """
    if not lessons_dir.is_dir():
        return ()
    lessons: list[Lesson] = []
    for path in sorted(lessons_dir.iterdir(), key=lambda item: item.name):
        if not path.is_dir():
            continue
        skill_path = path / "SKILL.md"
        if not skill_path.exists():
            continue
        try:
            raw = skill_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Could not read lesson %s: %s", skill_path, exc)
            continue
        name = path.name
        description = _frontmatter_description(raw) or _first_body_line(raw)
        lessons.append(Lesson(name=name, description=description, path=skill_path))
    return tuple(lessons)


def snapshot_learned_context(
    paths: TauPaths | None = None,
    char_limit: int = DEFAULT_MEMORY_CHAR_LIMIT,
) -> LearnedContext:
    """Snapshot the durable learned state for prompt assembly.

    The snapshot is taken once at session load and never re-rendered
    mid-session, mirroring Hermes' frozen-snapshot cache invariant.
    """
    store = resolve_store_paths(paths)
    memory_entries = load_memory_entries(store.memory_path, char_limit=char_limit)
    return LearnedContext(
        memory_text=ENTRY_DELIMITER.join(memory_entries) if memory_entries else None,
        lessons=load_learned_lessons(store.lessons_dir),
    )


def compose_learned_section(learned: LearnedContext | None) -> str | None:
    """Compose the frozen learned-context section text for the system prompt."""
    if learned is None:
        return None
    return learned.render()


def sanitize_lesson_name(name: str) -> str:
    """Normalize a curator-provided lesson name to a safe directory name."""
    cleaned = "".join(char if char.isalnum() or char in "-_" else "-" for char in name.strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part).lower()
    return cleaned[:48] or "lesson"


def write_lesson(
    lessons_dir: Path,
    name: str,
    description: str,
    body: str,
) -> Path:
    """Write or update one lesson file, returning the SKILL.md path."""
    safe_name = sanitize_lesson_name(name)
    skill_dir = lessons_dir / safe_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    description = description.strip() or "Lesson learned in a previous Tau session."
    if len(body) > MAX_LESSON_BODY_CHARS:
        body = body[: MAX_LESSON_BODY_CHARS - 3].rstrip() + "..."
    content = f"---\ndescription: {description.strip()}\n---\n\n{body.strip()}\n"
    path = skill_dir / "SKILL.md"
    _atomic_write(path, content)
    return path


def _curator_review_transcript(messages: tuple[AgentMessage, ...]) -> str:
    """Flatten a settled session transcript into curator-readable text."""
    lines: list[str] = []
    for message in messages:
        text = getattr(message, "text", None)
        if isinstance(text, str) and text.strip():
            lines.append(f"[{type(message).__name__}] {text.strip()}")
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            for call in tool_calls:
                try:
                    rendered = json.dumps(call.arguments, sort_keys=True, default=str)
                except (TypeError, ValueError):
                    rendered = str(call.arguments)
                lines.append(f"[tool_call:{call.name}] {rendered[:500]}")
    return "\n\n".join(lines).strip()


def build_curator_review_prompt(
    messages: tuple[AgentMessage, ...],
    *,
    existing_memory: tuple[str, ...],
    existing_lessons: tuple[Lesson, ...],
) -> str:
    """Build the user prompt for the curator review of one settled run."""
    existing_lines: list[str] = []
    for entry in existing_memory:
        existing_lines.append(f"- {entry}")
    memory_block = "\n".join(existing_lines) if existing_lines else "(memory store is empty)"
    lesson_lines = [f"- {lesson.name}: {lesson.description}" for lesson in existing_lessons]
    lessons_block = "\n".join(lesson_lines) if lesson_lines else "(no lessons yet)"
    transcript = _curator_review_transcript(messages)
    if not transcript:
        transcript = "(empty transcript)"
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = (
            transcript[: MAX_TRANSCRIPT_CHARS // 2]
            + ("\n[...transcript truncated...]\n")
            + transcript[-MAX_TRANSCRIPT_CHARS // 2 :]
        )
    return (
        "Existing memory entries:\n"
        f"{memory_block}\n\n"
        "Existing lesson files:\n"
        f"{lessons_block}\n\n"
        "Session transcript to review:\n\n"
        f"{transcript}\n\n"
        "Return the JSON object now."
    )


def extract_curator_json(text: str) -> dict[str, Any]:
    """Extract the curator JSON object from model output, tolerating fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("curator response contained no JSON object")
    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("curator response is not a JSON object")
    return parsed


def parse_curator_result(
    parsed: dict[str, Any],
    *,
    existing_lessons: tuple[Lesson, ...],
) -> tuple[tuple[str, ...], tuple[CuratorSuggestion, ...]]:
    """Validate curator output into memory entries and lesson suggestions."""
    raw_entries = parsed.get("memory_entries") or []
    if not isinstance(raw_entries, list):
        raise ValueError("memory_entries must be a list of strings")
    memory: list[str] = []
    for entry in raw_entries[:5]:
        if isinstance(entry, str) and entry.strip():
            memory.append(entry.strip())

    raw_lessons = parsed.get("lessons") or []
    if not isinstance(raw_lessons, list):
        raise ValueError("lessons must be a list of objects")
    existing_names = {lesson.name for lesson in existing_lessons}
    lessons: list[CuratorSuggestion] = []
    for raw in raw_lessons[:DEFAULT_MAX_LESSONS_PER_RUN]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        description = str(raw.get("description", "")).strip()
        body = str(raw.get("body", "")).strip()
        if not name or not body:
            continue
        lessons.append(
            CuratorSuggestion(
                name=name,
                description=description,
                body=body,
                replaces_existing=sanitize_lesson_name(name) in existing_names,
            )
        )
    return tuple(memory), tuple(lessons)


def apply_curator_result(
    result: CuratorResult,
    *,
    store: MemoryStorePaths,
    char_limit: int = DEFAULT_MEMORY_CHAR_LIMIT,
) -> tuple[tuple[str, ...], tuple[tuple[str, bool], ...]]:
    """Apply a curator result to the durable stores.

    Memory entries append (rejecting the whole batch on overflow), lessons
    write-or-update. Returns (memory_added, (lesson_name, replaced)) pairs.
    """
    memory_added: tuple[str, ...] = ()
    if result.memory_entries:
        memory_added = append_memory_entries(
            store.memory_path,
            result.memory_entries,
            char_limit=char_limit,
        )
    lesson_results: list[tuple[str, bool]] = []
    for lesson in result.lessons:
        write_lesson(
            store.lessons_dir,
            lesson.name,
            lesson.description,
            lesson.body,
        )
        lesson_results.append((lesson.name, lesson.replaces_existing))
    return memory_added, tuple(lesson_results)


def _frontmatter_description(raw: str) -> str | None:
    """Extract the ``description`` key from minimal SKILL.md frontmatter."""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("description:"):
            value = stripped.removeprefix("description:").strip()
            return value or None
    return None


def _first_body_line(raw: str) -> str:
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:100]
        if stripped.startswith("#") and len(stripped) > 1:
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading[:100]
    return "No description"


def _atomic_write(path: Path, content: str) -> None:
    """Write file content atomically (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)
