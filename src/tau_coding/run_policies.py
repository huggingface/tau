"""Opt-in, per-run limits implemented at the coding-session event boundary."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from tau_agent.events import AgentEvent, MessageEndEvent, TurnEndEvent
from tau_agent.messages import AssistantMessage, Usage, sum_usage
from tau_coding.session_stats import PricingResolver, _response_cost


@dataclass(frozen=True, slots=True)
class RunPolicyLimits:
    """Disabled by default; positive thresholds are checked after each turn."""

    max_cost_usd: float | None = None
    max_tokens: int | None = None
    max_consecutive_error_turns: int | None = None
    action: Literal["steer", "cancel"] = "steer"

    def __post_init__(self) -> None:
        for name in ("max_tokens", "max_consecutive_error_turns"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{name} must be a positive integer or null")
        cost = self.max_cost_usd
        if cost is not None and (
            type(cost) not in (int, float) or not math.isfinite(cost) or cost <= 0
        ):
            raise ValueError("max_cost_usd must be a finite positive number or null")
        if self.action not in ("steer", "cancel"):
            raise ValueError("action must be steer or cancel")


class RunPolicyMonitor:
    """Observe completed responses/turns without owning the agent loop.

    Steering is advisory. Cancellation stops subsequent work at the existing
    cancellation boundary; neither action can undo a completed request/tool.
    """

    def __init__(
        self,
        limits: RunPolicyLimits,
        *,
        queue_steering: Callable[[str], None],
        cancel: Callable[[], None],
        price_response: PricingResolver,
    ) -> None:
        self.limits = limits
        self._queue_steering = queue_steering
        self._cancel = cancel
        self._price_response = price_response
        self.reset()

    def reset(self) -> None:
        """Start a new public prompt/continue run, not an internal retry."""
        self.usage = Usage()
        self.cost_usd = 0.0
        self.consecutive_error_turns = 0
        self.fired: set[str] = set()
        self.cancelled = False

    async def on_event(self, event: AgentEvent) -> None:
        if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
            usage = event.message.usage
            # Some providers supply only components. Reasoning and 1h cache
            # writes are already included in output/cache_write respectively.
            tokens = usage.total_tokens or (
                usage.input + usage.output + usage.cache_read + usage.cache_write
            )
            self.usage = sum_usage((self.usage, usage.model_copy(update={"total_tokens": tokens})))
            if self.limits.max_cost_usd is not None:
                rates = self._price_response(
                    event.message.provider,
                    event.message.model,
                    usage.input + usage.cache_read + usage.cache_write,
                )
                if rates is not None:
                    self.cost_usd += _response_cost(
                        input_tokens=usage.input,
                        output_tokens=usage.output,
                        cache_read_tokens=usage.cache_read,
                        cache_write_tokens=usage.cache_write,
                        cache_write_1h_tokens=usage.cache_write_1h or 0,
                        rates=rates,
                    )
                elif usage.cost.total > 0:
                    self.cost_usd += usage.cost.total
        if not isinstance(event, TurnEndEvent) or not isinstance(event.message, AssistantMessage):
            return
        if event.message.stop_reason == "aborted":
            return
        failed = event.message.stop_reason == "error" or any(
            result.is_error for result in event.tool_results
        )
        self.consecutive_error_turns = self.consecutive_error_turns + 1 if failed else 0
        reasons = []
        for key, value, limit, label in (
            ("cost", self.cost_usd, self.limits.max_cost_usd, "cost (USD)"),
            ("tokens", self.usage.total_tokens, self.limits.max_tokens, "tokens"),
            (
                "errors",
                self.consecutive_error_turns,
                self.limits.max_consecutive_error_turns,
                "consecutive error turns",
            ),
        ):
            if limit is not None and value >= limit and key not in self.fired:
                self.fired.add(key)
                reasons.append(f"{label} {value:g} reached limit {limit:g}")
        if not reasons or self.cancelled:
            return
        if self.limits.action == "cancel":
            self.cancelled = True
            self._cancel()
        else:
            self._queue_steering(
                f"Run policy exceeded: {'; '.join(reasons)}. Please stop working on this task, "
                "summarize the progress made so far, and list any remaining work for the user."
            )
