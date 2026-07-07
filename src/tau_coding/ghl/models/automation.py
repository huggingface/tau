from __future__ import annotations

from dataclasses import dataclass, field

from tau_coding.ghl.models.delta import DeltaType


@dataclass(frozen=True, slots=True)
class TriggerCondition:
    delta_types: tuple[DeltaType, ...] = ()
    account_slug: str | None = None
    pipeline_id: str | None = None
    from_stage_id: str | None = None
    to_stage_id: str | None = None
    status: str | None = None
    min_value: float | None = None


@dataclass(frozen=True, slots=True)
class WorkflowAction:
    workflow_id: str
    account_slug: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TriggerRule:
    name: str
    condition: TriggerCondition
    action: WorkflowAction
    enabled: bool = True
