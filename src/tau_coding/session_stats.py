"""Lifetime activity and usage totals for an active session branch."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from tau_agent.messages import AssistantMessage, CustomMessage, UserMessage
from tau_agent.session import MessageEntry
from tau_agent.session.entries import SessionEntry

PricingResolver = Callable[[str, str, int], Mapping[str, float] | None]
_TOKENS_PER_MILLION = 1_000_000


@dataclass(frozen=True, slots=True)
class SessionStats:
    """Cumulative activity and billed usage for one active branch."""

    turn_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float | None = None


def calculate_session_stats(
    entries: Sequence[SessionEntry],
    *,
    pricing: PricingResolver,
) -> SessionStats:
    """Aggregate original branch messages, including messages replaced by compaction."""
    turn_count = 0
    tool_call_count = 0
    input_tokens = 0
    output_tokens = 0
    estimated_cost = 0.0
    has_billable_usage = False
    has_complete_pricing = True

    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue
        message = entry.message
        if isinstance(message, (UserMessage, CustomMessage)):
            turn_count += 1
            continue
        if not isinstance(message, AssistantMessage):
            continue

        tool_call_count += len(message.tool_calls)
        usage = message.usage
        prompt_tokens = usage.input + usage.cache_read + usage.cache_write
        input_tokens += prompt_tokens
        output_tokens += usage.output
        if prompt_tokens == 0 and usage.output == 0:
            continue

        has_billable_usage = True
        rates = pricing(message.provider, message.model, prompt_tokens)
        if rates is None:
            if usage.cost.total > 0:
                estimated_cost += usage.cost.total
            else:
                has_complete_pricing = False
            continue
        estimated_cost += _response_cost(
            input_tokens=usage.input,
            output_tokens=usage.output,
            cache_read_tokens=usage.cache_read,
            cache_write_tokens=usage.cache_write,
            rates=rates,
        )

    return SessionStats(
        turn_count=turn_count,
        tool_call_count=tool_call_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=(estimated_cost if has_billable_usage and has_complete_pricing else None),
    )


def _response_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    rates: Mapping[str, float],
) -> float:
    """Calculate one response's estimated USD cost from per-million-token rates."""
    return (
        input_tokens * rates.get("input", 0.0)
        + output_tokens * rates.get("output", 0.0)
        + cache_read_tokens * rates.get("cacheRead", 0.0)
        + cache_write_tokens * rates.get("cacheWrite", 0.0)
    ) / _TOKENS_PER_MILLION
