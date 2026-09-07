"""Session-tree value objects and traversal helpers.

``CodingSession`` retains the state-changing branch operation.  This module
contains the durable-entry traversal and presentation logic it delegates to.
"""

from __future__ import annotations

from dataclasses import dataclass

from tau_agent.messages import AgentMessage, AssistantMessage, UserMessage, message_text
from tau_agent.session import LeafEntry, SessionState
from tau_agent.session.entries import SessionEntry
from tau_agent.session.tree import SessionTreeError, path_to_entry


@dataclass(frozen=True, slots=True)
class SessionTreeChoice:
    """One branchable entry in the active session tree."""

    entry_id: str
    label: str
    active: bool = False
    is_tool_call: bool = False


@dataclass(frozen=True, slots=True)
class SessionTreeBranchResult:
    """Result of moving the active session tree leaf."""

    message: str
    input_prefill: str | None = None


def detach_missing_parents(entries: list[SessionEntry]) -> list[SessionEntry]:
    """Detach parent pointers that reference entries outside this session."""
    entry_ids = {entry.id for entry in entries}
    return [
        entry.model_copy(update={"parent_id": None})
        if entry.parent_id is not None and entry.parent_id not in entry_ids
        else entry
        for entry in entries
    ]


def last_parent_id_from_state(state: SessionState) -> str | None:
    """Return the active leaf, or the last durable entry when no leaf exists."""
    if state.active_leaf_id is not None:
        return state.active_leaf_id
    if state.entries:
        return state.entries[-1].id
    return None


def latest_leaf_entry(entries: list[SessionEntry]) -> LeafEntry | None:
    """Return the newest leaf entry, if the session has one."""
    for entry in reversed(entries):
        if isinstance(entry, LeafEntry):
            return entry
    return None


def is_branchable_tree_entry(entry: SessionEntry) -> bool:
    """Return whether an entry can be selected as a branch point."""
    if entry.type in {"compaction", "branch_summary"}:
        return True
    if entry.type != "message":
        return False
    return isinstance(entry.message, UserMessage | AssistantMessage)


def tree_choice_label(entry: SessionEntry, *, branch_indent: int = 0) -> str:
    """Create the indented label used by the tree picker."""
    prefix = "  " * branch_indent
    return f"{prefix}{tree_entry_title(entry)}"


def tree_branch_indents(entries: list[SessionEntry]) -> dict[str, int]:
    """Calculate visual indentation for each persisted tree entry."""
    children_by_parent: dict[str | None, list[str]] = {}
    for entry in entries:
        if entry.type != "leaf":
            children_by_parent.setdefault(entry.parent_id, []).append(entry.id)

    sibling_indexes = {
        child_id: index
        for children in children_by_parent.values()
        for index, child_id in enumerate(children)
    }
    indents: dict[str, int] = {}
    for entry in entries:
        if entry.type == "leaf":
            continue
        parent_indent = indents.get(entry.parent_id, 0) if entry.parent_id is not None else 0
        sibling_index = sibling_indexes.get(entry.id, 0)
        indents[entry.id] = parent_indent + (1 if sibling_index > 0 else 0)
    return indents


def ordered_tree_entries(entries: list[SessionEntry]) -> tuple[SessionEntry, ...]:
    """Return entries in visual tree order while tolerating malformed cycles."""
    children_by_parent: dict[str | None, list[SessionEntry]] = {}
    for entry in entries:
        if entry.type != "leaf":
            children_by_parent.setdefault(entry.parent_id, []).append(entry)

    ordered: list[SessionEntry] = []
    seen: set[str] = set()
    expanded: set[str | None] = set()

    def append_descendants(root_parent_id: str | None) -> None:
        # Iterative depth-first walk rather than recursion so a long session
        # cannot exceed Python's recursion limit. ``expanded`` also terminates
        # malformed parent cycles while preserving original traversal order.
        stack: list[str | None] = [root_parent_id]
        while stack:
            parent_id = stack.pop()
            if parent_id in expanded:
                continue
            expanded.add(parent_id)
            children = children_by_parent.get(parent_id, [])
            for child in children:
                if child.id not in seen:
                    ordered.append(child)
                    seen.add(child.id)
            for child in reversed(children):
                stack.append(child.id)

    append_descendants(None)
    for entry in entries:
        if entry.type != "leaf" and entry.id not in seen:
            ordered.append(entry)
            seen.add(entry.id)
            append_descendants(entry.id)
    return tuple(ordered)


def is_tool_call_tree_entry(entry: SessionEntry) -> bool:
    """Return whether a tree entry represents an assistant tool call."""
    return (
        entry.type == "message"
        and isinstance(entry.message, AssistantMessage)
        and bool(entry.message.tool_calls)
    )


def tree_entry_title(entry: SessionEntry) -> str:
    """Render the concise title shown for one tree entry."""
    match entry.type:
        case "message":
            message = entry.message
            if (
                isinstance(message, AssistantMessage)
                and message.tool_calls
                and not message.text.strip()
            ):
                tool_names = ", ".join(call.name for call in message.tool_calls)
                return f"tool call: {tool_names}"
            return f"{message.role}: {message_text_preview(message)}"
        case "compaction":
            return f"compaction summary: {short_preview(entry.summary)}"
        case "branch_summary":
            return f"branch summary: {short_preview(entry.summary)}"
        case _:
            return entry.type


def message_text_preview(message: AgentMessage) -> str:
    """Return a compact preview of an agent message."""
    return short_preview(message_text(message))


def short_preview(text: str, *, limit: int = 72) -> str:
    """Normalize and truncate a single-line preview."""
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized or "(empty)"
    return f"{normalized[: limit - 1]}..."


def messages_after_entry_on_active_path(
    entries: list[SessionEntry],
    entry_id: str,
    active_leaf_id: str | None,
) -> tuple[AgentMessage, ...]:
    """Return messages after an entry on the active path, if it is valid."""
    if active_leaf_id is None:
        return ()
    try:
        active_path = path_to_entry(entries, active_leaf_id)
    except SessionTreeError:
        return ()
    try:
        target_index = next(
            index for index, entry in enumerate(active_path) if entry.id == entry_id
        )
    except StopIteration:
        return ()
    return tuple(
        entry.message for entry in active_path[target_index + 1 :] if entry.type == "message"
    )
