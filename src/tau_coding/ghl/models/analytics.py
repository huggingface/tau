from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StageMetrics:
    stage_id: str
    stage_name: str | None = None
    opportunity_count: int = 0
    total_value: float = 0.0
    average_age_days: float | None = None


@dataclass(frozen=True, slots=True)
class PipelineSummary:
    account_slug: str
    pipeline_id: str
    opportunity_count: int = 0
    total_value: float = 0.0
    won_value: float = 0.0
    lost_value: float = 0.0
    stage_metrics: tuple[StageMetrics, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CrossAccountReport:
    summaries: tuple[PipelineSummary, ...]
    total_accounts: int
    healthy_accounts: int
    total_opportunities: int
    total_value: float
    warnings: tuple[str, ...] = field(default_factory=tuple)
