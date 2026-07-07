# mypy: ignore-errors
from __future__ import annotations

from tau_coding.ghl.models import OpportunityDelta, TriggerRule, WorkflowAction


def rule_matches(rule: TriggerRule, delta: OpportunityDelta) -> bool:
    c = rule.condition
    prev = delta.previous
    cur = delta.current
    return (
        rule.enabled
        and (not c.delta_types or delta.delta_type in c.delta_types)
        and (c.account_slug is None or c.account_slug == cur.account_slug)
        and (c.pipeline_id is None or c.pipeline_id == cur.pipeline_id)
        and (c.from_stage_id is None or (prev and c.from_stage_id == prev.stage_id))
        and (c.to_stage_id is None or c.to_stage_id == cur.stage_id)
        and (c.status is None or c.status == cur.status)
        and (c.min_value is None or cur.value >= c.min_value)
    )


def evaluate_rules(
    rules: list[TriggerRule], deltas: list[OpportunityDelta]
) -> list[tuple[OpportunityDelta, WorkflowAction]]:
    return [(d, r.action) for d in deltas for r in rules if rule_matches(r, d)]


async def dispatch_actions(
    client, queued: list[tuple[OpportunityDelta, WorkflowAction]]
) -> list[dict]:
    results = []
    for delta, action in queued:
        account = action.account_slug or delta.current.account_slug
        if delta.current.contact_id:
            results.append(
                await client.enroll_workflow(account, delta.current.contact_id, action.workflow_id)
            )
    return results
