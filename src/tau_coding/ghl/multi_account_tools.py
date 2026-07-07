# mypy: ignore-errors
from __future__ import annotations

from collections.abc import Mapping

from tau_agent.tools import AgentTool, AgentToolResult
from tau_agent.types import JSONValue
from tau_coding.ghl.account_registry import AccountRegistry
from tau_coding.ghl.models import OpportunitySnapshot
from tau_coding.ghl.multi_account import MultiAccountClient
from tau_coding.ghl.opportunity_sync import MemoryDeltaStore, sync_opportunities
from tau_coding.ghl.pipeline_analytics import cross_account_summary, generate_pipeline_report

_STORE = MemoryDeltaStore()


def create_multi_account_tools(registry: AccountRegistry | None = None) -> list[AgentTool]:
    reg = registry or AccountRegistry.discover()

    async def summary(args: Mapping[str, JSONValue], signal=None) -> AgentToolResult:
        async with MultiAccountClient(reg.accounts) as client:
            results = await client.list_opportunities(
                str(args.get("pipeline_id")) if args.get("pipeline_id") else None
            )
        reports = []
        warnings = []
        for slug, res in results.items():
            if res["ok"]:
                reports.append(
                    generate_pipeline_report(
                        slug, str(args.get("pipeline_id") or "all"), res["data"]
                    )
                )
            else:
                warnings.append(f"{slug}: {res['error']}")
        report = cross_account_summary(reports, total_accounts=len(reg.accounts), warnings=warnings)
        return AgentToolResult(
            tool_call_id=str(args.get("tool_call_id", "")),
            name="ghl_cross_account_summary",
            ok=True,
            content=(
                f"{report.total_opportunities} opportunities across "
                f"{report.healthy_accounts}/{report.total_accounts} accounts"
            ),
            data={
                "total_opportunities": report.total_opportunities,
                "total_value": report.total_value,
                "warnings": list(report.warnings),
            },
        )

    async def pipeline(args: Mapping[str, JSONValue], signal=None) -> AgentToolResult:
        tools = await summary(args, signal)
        return tools.model_copy(update={"name": "ghl_pipeline_report"})

    async def deltas(args: Mapping[str, JSONValue], signal=None) -> AgentToolResult:
        snapshots = (
            [OpportunitySnapshot(**item) for item in args.get("snapshots", [])]
            if isinstance(args.get("snapshots", []), list)
            else []
        )
        found = sync_opportunities(_STORE, snapshots)
        return AgentToolResult(
            tool_call_id=str(args.get("tool_call_id", "")),
            name="ghl_opportunity_deltas",
            ok=True,
            content=f"Detected {len(found)} opportunity deltas",
            data={"deltas": [d.delta_type.value for d in found]},
        )

    schema = {"type": "object", "properties": {}}
    return [
        AgentTool(
            "ghl_cross_account_summary",
            "Summarize GHL opportunities across accounts.",
            schema,
            summary,
        ),
        AgentTool(
            "ghl_pipeline_report", "Build a cross-account pipeline report.", schema, pipeline
        ),
        AgentTool(
            "ghl_opportunity_deltas", "Detect opportunity deltas from snapshots.", schema, deltas
        ),
    ]
