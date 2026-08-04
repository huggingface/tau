"""Usage analysis for exported Tau sessions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache

from tau_agent.messages import AssistantMessage, Usage
from tau_agent.session import CompactionEntry, LeafEntry, MessageEntry, SessionEntry
from tau_agent.session.tree import SessionTreeError, path_to_entry
from tau_coding.provider_catalog import (
    ProviderCatalogEntry,
    model_cost_for_input_tokens,
)
from tau_coding.session_stats import PricingResolver


@dataclass(frozen=True, slots=True)
class RequestUsage:
    """Token usage and cost information for one assistant response."""

    number: int
    provider: str
    model: str
    fresh: int
    cached: int
    cache_write: int
    output: int
    reasoning: int
    stop_reason: str
    estimated_cost: float | None

    @property
    def prompt(self) -> int:
        """Return all input tokens reported for the request."""
        return self.fresh + self.cached + self.cache_write

    @property
    def hit_rate(self) -> float:
        """Return the share of prompt tokens served from cache."""
        return self.cached / self.prompt if self.prompt else 0.0


@dataclass(frozen=True, slots=True)
class SessionAnalysis:
    """Detailed usage analysis for the active branch of a session."""

    requests: tuple[RequestUsage, ...]
    compaction_count: int
    tool_counts: tuple[tuple[str, int], ...]

    @property
    def total_fresh(self) -> int:
        return sum(request.fresh for request in self.requests)

    @property
    def total_cached(self) -> int:
        return sum(request.cached for request in self.requests)

    @property
    def total_cache_writes(self) -> int:
        return sum(request.cache_write for request in self.requests)

    @property
    def total_prompt(self) -> int:
        return sum(request.prompt for request in self.requests)

    @property
    def total_output(self) -> int:
        return sum(request.output for request in self.requests)

    @property
    def total_reasoning(self) -> int:
        return sum(request.reasoning for request in self.requests)

    @property
    def cache_hit_rate(self) -> float:
        """Return the cumulative cache hit rate for the active branch."""
        return self.total_cached / self.total_prompt if self.total_prompt else 0.0

    @property
    def estimated_cost(self) -> float | None:
        """Return the total cost, or ``None`` if any request is unpriced."""
        if not self.requests or any(request.estimated_cost is None for request in self.requests):
            return None
        return sum(request.estimated_cost or 0.0 for request in self.requests)


def analyze_session(
    entries: Sequence[SessionEntry],
    *,
    pricing: PricingResolver | None = None,
) -> SessionAnalysis:
    """Analyze assistant usage, tools, and compactions on the active branch.

    A session export contains the complete tree. Usage is intentionally scoped to
    the branch selected by the latest leaf pointer so abandoned branches do not
    inflate the dashboard totals.
    """
    active_entries = _active_branch_entries(entries)
    requests: list[RequestUsage] = []
    tools: Counter[str] = Counter()
    compactions = 0

    for entry in active_entries:
        if isinstance(entry, CompactionEntry):
            compactions += 1
        if not isinstance(entry, MessageEntry):
            continue
        message = entry.message
        if not isinstance(message, AssistantMessage):
            continue

        for tool_call in message.tool_calls:
            tools[tool_call.name] += 1
        usage = message.usage
        prompt_tokens = usage.input + usage.cache_read + usage.cache_write
        rates = pricing(message.provider, message.model, prompt_tokens) if pricing else None
        estimated_cost = (
            _response_cost(usage=usage, rates=rates)
            if rates is not None
            else usage.cost.total
            if usage.cost.total > 0
            else None
        )
        requests.append(
            RequestUsage(
                number=len(requests) + 1,
                provider=message.provider,
                model=message.model,
                fresh=usage.input,
                cached=usage.cache_read,
                cache_write=usage.cache_write,
                output=usage.output,
                reasoning=usage.reasoning or 0,
                stop_reason=message.stop_reason,
                estimated_cost=estimated_cost,
            )
        )

    return SessionAnalysis(
        requests=tuple(requests),
        compaction_count=compactions,
        tool_counts=tuple(sorted(tools.items(), key=lambda item: (-item[1], item[0]))),
    )


def _active_branch_entries(entries: Sequence[SessionEntry]) -> list[SessionEntry]:
    entry_list = list(entries)
    leaf_id = next(
        (
            entry.entry_id
            for entry in reversed(entry_list)
            if isinstance(entry, LeafEntry) and isinstance(entry.entry_id, str)
        ),
        None,
    )
    if leaf_id is None:
        return [entry for entry in entry_list if not isinstance(entry, LeafEntry)]
    try:
        return path_to_entry(entry_list, leaf_id)
    except SessionTreeError:
        # Keep exports useful for partially written or externally edited JSONL.
        return [entry for entry in entry_list if not isinstance(entry, LeafEntry)]


def _response_cost(*, usage: Usage, rates: Mapping[str, float]) -> float:
    return (
        usage.input * rates.get("input", 0.0)
        + usage.output * rates.get("output", 0.0)
        + usage.cache_read * rates.get("cacheRead", 0.0)
        + usage.cache_write * rates.get("cacheWrite", 0.0)
    ) / 1_000_000


@cache
def default_pricing() -> PricingResolver:
    """Return a resolver backed by Tau's packaged provider catalog."""
    from tau_coding.catalog_loader import builtin_catalog

    return catalog_pricing(builtin_catalog())


def catalog_pricing(catalog: Sequence[ProviderCatalogEntry]) -> PricingResolver:
    """Build a pricing resolver from provider catalog entries."""
    providers = {provider.name: provider for provider in catalog}

    def resolve(provider_name: str, model: str, input_tokens: int) -> dict[str, float] | None:
        provider = providers.get(provider_name)
        if provider is None:
            return None
        metadata = provider.model_metadata.get(model)
        if metadata is None:
            return None
        return model_cost_for_input_tokens(metadata, input_tokens)

    return resolve
