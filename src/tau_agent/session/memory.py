"""In-memory session state reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

from tau_agent.messages import AgentMessage, UserMessage
from tau_agent.session.entries import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    SessionEntry,
    SessionInfoEntry,
)
from tau_agent.session.tree import path_to_entry

_UNSET_LEAF_ID: Final[object] = object()


@dataclass(frozen=True, slots=True)
class SessionState:
    """Current session state derived from append-only entries."""

    messages: tuple[AgentMessage, ...]
    model: str | None
    thinking_level: str | None
    label: str | None
    active_leaf_id: str | None
    session_info: SessionInfoEntry | None
    custom_entries: tuple[CustomEntry, ...]
    compaction_entries: tuple[CompactionEntry, ...]
    context_entry_ids: tuple[str, ...]
    entries: tuple[SessionEntry, ...]

    @classmethod
    def from_entries(
        cls,
        entries: list[SessionEntry],
        *,
        leaf_id: str | None | object = _UNSET_LEAF_ID,
    ) -> SessionState:
        """Replay entries into state.

        When `leaf_id` is provided, only the root-to-leaf path is replayed. Passing
        ``None`` explicitly replays the empty path before the first root entry.
        Without it, entries are replayed linearly in storage order.
        """
        replay_all = leaf_id is _UNSET_LEAF_ID
        resolved_leaf_id = None if replay_all else cast(str | None, leaf_id)
        replay_entries = (
            entries
            if replay_all
            else path_to_entry(entries, resolved_leaf_id)
            if resolved_leaf_id is not None
            else []
        )

        message_rows: list[tuple[str, AgentMessage]] = []
        model: str | None = None
        thinking_level: str | None = None
        label: str | None = None
        active_leaf_id: str | None = resolved_leaf_id
        session_info: SessionInfoEntry | None = None
        custom_entries: list[CustomEntry] = []
        compaction_entries: list[CompactionEntry] = []

        latest_branch_summary_index = _latest_branch_summary_index(replay_entries)
        if latest_branch_summary_index is not None:
            replay_entries = replay_entries[latest_branch_summary_index:]

        for entry in replay_entries:
            match entry.type:
                case "message":
                    message_rows.append((entry.id, entry.message))
                case "model_change":
                    model = entry.model
                case "thinking_level_change":
                    thinking_level = entry.thinking_level
                case "label":
                    label = entry.label
                case "leaf":
                    active_leaf_id = entry.entry_id
                case "session_info":
                    session_info = entry
                case "custom":
                    custom_entries.append(entry)
                case "compaction":
                    compaction_entries.append(entry)
                    message_rows = _apply_compaction(message_rows, entry)
                case "branch_summary":
                    message_rows.append(
                        (entry.id, UserMessage(content=_format_branch_summary(entry)))
                    )

        return cls(
            messages=tuple(message for _entry_id, message in message_rows),
            model=model,
            thinking_level=thinking_level,
            label=label,
            active_leaf_id=active_leaf_id,
            session_info=session_info,
            custom_entries=tuple(custom_entries),
            compaction_entries=tuple(compaction_entries),
            context_entry_ids=tuple(entry_id for entry_id, _message in message_rows),
            entries=tuple(replay_entries),
        )


def _latest_branch_summary_index(entries: list[SessionEntry]) -> int | None:
    """Return the index of the most recent branch summary on a replay path."""
    for index in range(len(entries) - 1, -1, -1):
        if entries[index].type == "branch_summary":
            return index
    return None


def _apply_compaction(
    message_rows: list[tuple[str, AgentMessage]],
    entry: CompactionEntry,
) -> list[tuple[str, AgentMessage]]:
    replaced_ids = set(entry.replaces_entry_ids)
    retained: list[tuple[str, AgentMessage]] = []
    inserted_summary = False
    for entry_id, message in message_rows:
        if entry_id not in replaced_ids:
            retained.append((entry_id, message))
            continue
        if not inserted_summary:
            retained.append(
                (entry.id, UserMessage(content=_format_compaction_summary(entry.summary)))
            )
            inserted_summary = True

    if not inserted_summary:
        retained.append((entry.id, UserMessage(content=_format_compaction_summary(entry.summary))))
    return retained


def _format_compaction_summary(summary: str) -> str:
    return f"Previous conversation summary:\n{summary}"


def _format_branch_summary(entry: BranchSummaryEntry) -> str:
    return (
        "The following is a summary of a branch that this conversation came back from:\n"
        f"<summary>\n{entry.summary}\n</summary>"
    )
