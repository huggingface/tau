"""Approximate context-size estimation for Tau coding sessions."""

from __future__ import annotations

from dataclasses import dataclass

from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
    message_text,
)
from tau_agent.tools import AgentTool

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
TOOL_OVERHEAD_TOKENS = 16
SUMMARY_MESSAGE_CHAR_LIMIT = 500
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_COMPACTION_RESERVE_TOKENS = 16_384
DEFAULT_COMPACTION_KEEP_RECENT_TOKENS = 20_000
COMPACTION_SUMMARY_PREFIX = "Previous conversation summary:\n"

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a conversation "
    "between a user and an AI coding assistant, then produce a structured summary "
    "following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the "
    "conversation. ONLY output the structured summary."
)

SUMMARIZATION_PROMPT = (
    "The messages above are a conversation to summarize. Create a structured context "
    "checkpoint summary that another LLM will use to continue the work.\n\n"
    "Use this EXACT format:\n\n"
    "## Goal\n"
    "[What is the user trying to accomplish? Can be multiple items if the session "
    "covers different tasks.]\n\n"
    "## Constraints & Preferences\n"
    "- [Any constraints, preferences, or requirements mentioned by user]\n"
    '- [Or "(none)" if none were mentioned]\n\n'
    "## Progress\n"
    "### Done\n"
    "- [x] [Completed tasks/changes]\n\n"
    "### In Progress\n"
    "- [ ] [Current work]\n\n"
    "### Blocked\n"
    "- [Issues preventing progress, if any]\n\n"
    "## Key Decisions\n"
    "- **[Decision]**: [Brief rationale]\n\n"
    "## Next Steps\n"
    "1. [Ordered list of what should happen next]\n\n"
    "## Critical Context\n"
    "- [Any data, examples, or references needed to continue]\n"
    '- [Or "(none)" if not applicable]\n\n'
    "Keep each section concise. Preserve exact file paths, function names, and error "
    "messages."
)

UPDATE_SUMMARIZATION_PROMPT = (
    "The messages above are NEW conversation messages to incorporate into the existing "
    "summary provided in <previous-summary> tags.\n\n"
    "Update the existing structured summary with new information. RULES:\n"
    "- PRESERVE all existing information from the previous summary\n"
    "- ADD new progress, decisions, and context from the new messages\n"
    '- UPDATE the Progress section: move items from "In Progress" to "Done" when '
    "completed\n"
    '- UPDATE "Next Steps" based on what was accomplished\n'
    "- PRESERVE exact file paths, function names, and error messages\n"
    "- If something is no longer relevant, you may remove it\n\n"
    "Use this EXACT format:\n\n"
    "## Goal\n"
    "[Preserve existing goals, add new ones if the task expanded]\n\n"
    "## Constraints & Preferences\n"
    "- [Preserve existing, add new ones discovered]\n\n"
    "## Progress\n"
    "### Done\n"
    "- [x] [Include previously done items AND newly completed items]\n\n"
    "### In Progress\n"
    "- [ ] [Current work - update based on progress]\n\n"
    "### Blocked\n"
    "- [Current blockers - remove if resolved]\n\n"
    "## Key Decisions\n"
    "- **[Decision]**: [Brief rationale] (preserve all previous, add new)\n\n"
    "## Next Steps\n"
    "1. [Update based on current state]\n\n"
    "## Critical Context\n"
    "- [Preserve important context, add new if needed]\n\n"
    "Keep each section concise. Preserve exact file paths, function names, and error "
    "messages."
)

TURN_PREFIX_SUMMARIZATION_PROMPT = (
    "This is the PREFIX of a turn that was too large to keep. The SUFFIX (recent work) "
    "is retained.\n\n"
    "Summarize the prefix to provide context for the retained suffix:\n\n"
    "## Original Request\n"
    "[What did the user ask for in this turn?]\n\n"
    "## Early Progress\n"
    "- [Key decisions and work done in the prefix]\n\n"
    "## Context for Suffix\n"
    "- [Information needed to understand the retained recent work]\n\n"
    "Be concise. Focus on what's needed to understand the kept suffix."
)


@dataclass(frozen=True, slots=True)
class ContextUsageEstimate:
    """Deterministic context-size accounting for one provider request."""

    total_tokens: int
    system_tokens: int
    message_tokens: int
    tool_tokens: int
    message_count: int
    tool_count: int


def estimate_text_tokens(text: str) -> int:
    """Return a deterministic rough token estimate for text."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_message_tokens(message: AgentMessage) -> int:
    """Return a rough token estimate for one provider-neutral message."""
    tokens = MESSAGE_OVERHEAD_TOKENS + estimate_text_tokens(message_text(message))
    if isinstance(message, AssistantMessage):
        tokens += sum(
            estimate_text_tokens(call.name) + estimate_text_tokens(str(call.arguments))
            for call in message.tool_calls
        )
    elif isinstance(message, ToolResultMessage):
        tokens += estimate_text_tokens(message.tool_name)
    return tokens


def estimate_tool_tokens(tool: AgentTool) -> int:
    """Return a rough token estimate for one tool definition."""
    return (
        TOOL_OVERHEAD_TOKENS
        + estimate_text_tokens(tool.name)
        + estimate_text_tokens(tool.description)
        + estimate_text_tokens(str(tool.input_schema))
    )


def estimate_context_tokens(
    *,
    system: str,
    messages: tuple[AgentMessage, ...],
    tools: tuple[AgentTool, ...],
) -> int:
    """Return a rough estimate of the active provider context size."""
    return estimate_context_usage(system=system, messages=messages, tools=tools).total_tokens


def auto_compaction_threshold_for_context_window(context_window_tokens: int) -> int | None:
    """Return Pi-style automatic compaction threshold for a model context window."""
    if context_window_tokens <= 0:
        return None
    return max(1, context_window_tokens - DEFAULT_COMPACTION_RESERVE_TOKENS)


def estimate_context_usage(
    *,
    system: str,
    messages: tuple[AgentMessage, ...],
    tools: tuple[AgentTool, ...],
) -> ContextUsageEstimate:
    """Return deterministic context accounting for the active provider request."""
    system_tokens = estimate_text_tokens(system)
    message_tokens = sum(estimate_message_tokens(message) for message in messages)
    tool_tokens = sum(estimate_tool_tokens(tool) for tool in tools)
    return ContextUsageEstimate(
        total_tokens=system_tokens + message_tokens + tool_tokens,
        system_tokens=system_tokens,
        message_tokens=message_tokens,
        tool_tokens=tool_tokens,
        message_count=len(messages),
        tool_count=len(tools),
    )


def summarize_messages_for_compaction(messages: tuple[AgentMessage, ...]) -> str:
    """Build a deterministic compact summary from provider-neutral messages."""
    if not messages:
        return "No prior messages."
    lines = [f"Automatically compacted {len(messages)} prior message(s)."]
    for index, message in enumerate(messages, start=1):
        role = "tool" if isinstance(message, ToolResultMessage) else message.role
        lines.append(f"{index}. {role}: {_message_text(message)}")
    return "\n".join(lines)


def build_compaction_summary_prompt(
    messages: tuple[AgentMessage, ...],
    *,
    custom_instructions: str | None = None,
) -> str:
    """Build the model prompt Tau uses to summarize compacted history."""
    previous_summary, new_messages = _split_previous_compaction_summary(messages)
    conversation = serialize_messages_for_compaction(new_messages)
    prompt = f"<conversation>\n{conversation}\n</conversation>\n\n"
    base_prompt = (
        UPDATE_SUMMARIZATION_PROMPT if previous_summary is not None else SUMMARIZATION_PROMPT
    )

    if previous_summary is not None:
        prompt += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"

    instructions = custom_instructions.strip() if custom_instructions is not None else ""
    if instructions:
        base_prompt = f"{base_prompt}\n\nAdditional focus: {instructions}"

    return f"{prompt}{base_prompt}"


def serialize_messages_for_compaction(messages: tuple[AgentMessage, ...]) -> str:
    """Serialize provider-neutral messages for the compaction summarizer."""
    if not messages:
        return "(no new messages)"

    lines: list[str] = []
    for index, message in enumerate(messages, start=1):
        attributes = f"index={index} role={message.role}"
        if isinstance(message, ToolResultMessage):
            attributes += f" name={message.tool_name} error={str(message.is_error).lower()}"
        lines.append(f"<message {attributes}>")
        text = message_text(message)
        if text:
            lines.append(text)
        if isinstance(message, AssistantMessage) and message.tool_calls:
            lines.append("<tool-calls>")
            for call in message.tool_calls:
                lines.append(f"- {call.name}: {call.arguments}")
            lines.append("</tool-calls>")
        lines.append("</message>")
    return "\n".join(lines)


def _message_text(message: AgentMessage) -> str:
    text = message_text(message)
    if isinstance(message, AssistantMessage) and message.tool_calls:
        names = ", ".join(call.name for call in message.tool_calls)
        text = f"{text} [tool calls: {names}]"
    elif isinstance(message, ToolResultMessage):
        status = "failed" if message.is_error else "ok"
        text = f"{message.tool_name} {status}: {text}"
    return _truncate_summary_text(text)


def _truncate_summary_text(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= SUMMARY_MESSAGE_CHAR_LIMIT:
        return collapsed
    return collapsed[: SUMMARY_MESSAGE_CHAR_LIMIT - 3].rstrip() + "..."


def _split_previous_compaction_summary(
    messages: tuple[AgentMessage, ...],
) -> tuple[str | None, tuple[AgentMessage, ...]]:
    if not messages:
        return None, messages

    first = messages[0]
    if not isinstance(first, UserMessage):
        return None, messages
    text = message_text(first)
    if not text.startswith(COMPACTION_SUMMARY_PREFIX):
        return None, messages

    return text.removeprefix(COMPACTION_SUMMARY_PREFIX), messages[1:]
