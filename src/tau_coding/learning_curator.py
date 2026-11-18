"""LLM curator pass for Tau's cross-session learning loop.

Runs one settled session transcript through the session's own provider and
model, then applies the validated result to the durable learning stores. This
module is the async bridge between `tau_coding.learning` (pure store logic)
and the live `ModelProvider` streaming protocol, mirroring how
`branch_summary.py` drives summarization requests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tau_agent.messages import UserMessage
from tau_agent.provider import ModelProvider
from tau_coding.learning import (
    REVIEW_SYSTEM_PROMPT,
    CuratorResult,
    MemoryStorePaths,
    apply_curator_result,
    build_curator_review_prompt,
    extract_curator_json,
    load_learned_lessons,
    load_memory_entries,
    parse_curator_result,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CuratorRunResult:
    """User-facing outcome of one `/learn` curation run."""

    memory_added: tuple[str, ...]
    lessons: tuple[tuple[str, bool], ...]
    skipped_reason: str | None = None

    def format_summary(self) -> str:
        """Render the post-run status message shown to the user."""
        lines: list[str] = ["Learning review complete."]
        if self.memory_added:
            lines.append("Memory added:")
            for entry in self.memory_added:
                lines.append(f"- {entry}")
        else:
            lines.append("Memory added: nothing new.")
        if self.lessons:
            lines.append("Lessons written:")
            for name, replaced in self.lessons:
                marker = "updated" if replaced else "new"
                lines.append(f"- {name} ({marker})")
        else:
            lines.append("Lessons written: none.")
        return "\n".join(lines)


async def curate_session(
    *,
    provider: ModelProvider,
    model: str,
    messages: tuple[object, ...],
    store: MemoryStorePaths,
    char_limit: int | None = None,
) -> CuratorRunResult:
    """Run the curator pass over a settled transcript and apply the result.

    Raises `ValueError` on provider/parse/apply failures so callers can render
    the reason; the durable stores are only touched when the full pipeline
    reaches the apply step.
    """
    if not messages:
        raise ValueError("Nothing to learn from yet: this session has no transcript.")

    store = MemoryStorePaths(
        memory_path=store.memory_path,
        lessons_dir=store.lessons_dir,
    )
    existing_memory = load_memory_entries(
        store.memory_path,
        char_limit=char_limit or 2200,
    )
    existing_lessons = load_learned_lessons(store.lessons_dir)
    review_prompt = build_curator_review_prompt(
        messages,  # type: ignore[arg-type]
        existing_memory=existing_memory,
        existing_lessons=existing_lessons,
    )

    text = await _collect_provider_text(
        provider=provider,
        model=model,
        review_prompt=review_prompt,
    )
    parsed = extract_curator_json(text)
    memory_entries, lessons = parse_curator_result(
        parsed,
        existing_lessons=existing_lessons,
    )
    result = CuratorResult(memory_entries=memory_entries, lessons=lessons)
    memory_added, lesson_results = apply_curator_result(
        result,
        store=store,
        char_limit=char_limit or 2200,
    )
    return CuratorRunResult(
        memory_added=memory_added,
        lessons=lesson_results,
    )


async def _collect_provider_text(
    provider: ModelProvider,
    model: str,
    review_prompt: str,
) -> str:
    """Stream one curator completion and return its final text."""
    text_parts: list[str] = []
    done_message = None
    async for event in provider.stream_response(
        model=model,
        system=REVIEW_SYSTEM_PROMPT,
        messages=[UserMessage(content=review_prompt)],
        tools=[],
    ):
        event_type = getattr(event, "type", None)
        if event_type == "error":
            error_message = getattr(event, "error", None)
            detail = getattr(error_message, "error_message", None) or "provider error"
            raise ValueError(f"Curator request failed: {detail}")
        if event_type == "done":
            done_message = getattr(event, "message", None)
        else:
            delta = getattr(event, "delta", None)
            if isinstance(delta, str):
                text_parts.append(delta)

    final_text = ""
    if done_message is not None:
        final_text = getattr(done_message, "text", "") or ""
    if not final_text:
        final_text = "".join(text_parts)
    final_text = final_text.strip()
    if not final_text:
        raise ValueError("Curator request returned an empty response.")
    return final_text


__all__ = [
    "CuratorRunResult",
    "curate_session",
]
