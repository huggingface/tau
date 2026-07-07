# mypy: ignore-errors
from __future__ import annotations

from tau_coding.ghl.models import CrossAccountReport, PipelineSummary, StageMetrics


def generate_pipeline_report(
    account_slug: str, pipeline_id: str, opportunities: list[dict]
) -> PipelineSummary:
    stages: dict[str, dict[str, float | int]] = {}
    total = won = lost = 0.0
    for opp in opportunities:
        value = float(opp.get("value") or opp.get("monetaryValue") or 0)
        total += value
        status = str(opp.get("status", "")).lower()
        if status == "won":
            won += value
        if status == "lost":
            lost += value
        sid = str(opp.get("stage_id") or opp.get("stageId") or "unknown")
        entry = stages.setdefault(sid, {"count": 0, "value": 0.0})
        entry["count"] = int(entry["count"]) + 1
        entry["value"] = float(entry["value"]) + value
    metrics = tuple(
        StageMetrics(stage_id=k, opportunity_count=int(v["count"]), total_value=float(v["value"]))
        for k, v in stages.items()
    )
    return PipelineSummary(account_slug, pipeline_id, len(opportunities), total, won, lost, metrics)


def cross_account_summary(
    summaries: list[PipelineSummary],
    *,
    total_accounts: int | None = None,
    warnings: list[str] | None = None,
) -> CrossAccountReport:
    healthy = len({s.account_slug for s in summaries})
    return CrossAccountReport(
        tuple(summaries),
        total_accounts or healthy,
        healthy,
        sum(s.opportunity_count for s in summaries),
        sum(s.total_value for s in summaries),
        tuple(warnings or ()),
    )


def compute_velocity(deltas: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in deltas:
        counts[str(d.get("pipeline_id", "unknown"))] = (
            counts.get(str(d.get("pipeline_id", "unknown")), 0) + 1
        )
    return counts


def identify_bottlenecks(summary: PipelineSummary, *, threshold: int = 10) -> list[StageMetrics]:
    return [m for m in summary.stage_metrics if m.opportunity_count >= threshold]
