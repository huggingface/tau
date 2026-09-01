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
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    latest_prompt_tokens: int = 0
    latest_cached_input_tokens: int = 0
    timed_output_tokens: int = 0
    response_duration_ms: int = 0
    time_to_first_output_ms: int = 0
    timed_first_output_count: int = 0
    estimated_cost: float | None = None

    @property
    def cache_hit_rate(self) -> float | None:
        """Share of prompt tokens served from the provider's cache.

        None when no provider in the branch reported any cache activity, so that
        backends without prompt caching are not shown a permanent 0%.
        """
        if self.input_tokens <= 0:
            return None
        if self.cached_input_tokens == 0 and self.cache_write_tokens == 0:
            return None
        return self.cached_input_tokens / self.input_tokens

    @property
    def latest_cache_hit_rate(self) -> float | None:
        """Share of the latest request's prompt served from cache."""
        if self.latest_prompt_tokens <= 0:
            return None
        if self.cached_input_tokens == 0 and self.cache_write_tokens == 0:
            return None
        return self.latest_cached_input_tokens / self.latest_prompt_tokens

    @property
    def output_tokens_per_second(self) -> float | None:
        """Token-weighted effective output speed across timed responses."""
        if self.timed_output_tokens <= 0 or self.response_duration_ms <= 0:
            return None
        return self.timed_output_tokens * 1000 / self.response_duration_ms

    @property
    def average_time_to_first_output_ms(self) -> float | None:
        """Mean time from request start to the first output event."""
        if self.timed_first_output_count <= 0:
            return None
        return self.time_to_first_output_ms / self.timed_first_output_count


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
    cached_input_tokens = 0
    cache_write_tokens = 0
    latest_prompt_tokens = 0
    latest_cached_input_tokens = 0
    timed_output_tokens = 0
    response_duration_ms = 0
    time_to_first_output_ms = 0
    timed_first_output_count = 0
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
        latest_prompt_tokens = prompt_tokens
        latest_cached_input_tokens = usage.cache_read
        input_tokens += prompt_tokens
        cached_input_tokens += usage.cache_read
        cache_write_tokens += usage.cache_write
        output_tokens += usage.output
        timing = message.timing
        if timing is not None:
            # TPS is token-weighted and requires usable output usage. TTFT is a
            # per-call arithmetic mean whenever output was observed, even if a
            # later error left that response without billed output tokens.
            if usage.output > 0 and timing.total_duration_ms > 0:
                timed_output_tokens += usage.output
                response_duration_ms += timing.total_duration_ms
            if timing.time_to_first_output_ms is not None:
                time_to_first_output_ms += timing.time_to_first_output_ms
                timed_first_output_count += 1
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
            cache_write_1h_tokens=usage.cache_write_1h or 0,
            rates=rates,
        )

    return SessionStats(
        turn_count=turn_count,
        tool_call_count=tool_call_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        latest_prompt_tokens=latest_prompt_tokens,
        latest_cached_input_tokens=latest_cached_input_tokens,
        timed_output_tokens=timed_output_tokens,
        response_duration_ms=response_duration_ms,
        time_to_first_output_ms=time_to_first_output_ms,
        timed_first_output_count=timed_first_output_count,
        estimated_cost=(estimated_cost if has_billable_usage and has_complete_pricing else None),
    )


def _response_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    cache_write_1h_tokens: int,
    rates: Mapping[str, float],
) -> float:
    """Calculate one response's estimated USD cost from per-million-token rates.

    ``cache_write_tokens`` is the provider-reported total, which already
    includes any 1-hour TTL writes. Anthropic bills those at a higher rate, so
    they are priced at ``cacheWrite1h`` when the catalog provides it, falling
    back to the 5-minute ``cacheWrite`` rate otherwise.
    """
    write_1h = min(cache_write_1h_tokens, cache_write_tokens)
    return (
        input_tokens * rates.get("input", 0.0)
        + output_tokens * rates.get("output", 0.0)
        + cache_read_tokens * rates.get("cacheRead", 0.0)
        + (cache_write_tokens - write_1h) * rates.get("cacheWrite", 0.0)
        + write_1h * rates.get("cacheWrite1h", rates.get("cacheWrite", 0.0))
    ) / _TOKENS_PER_MILLION
