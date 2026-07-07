from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class DeltaType(StrEnum):
    CREATED = "CREATED"
    STAGE_CHANGED = "STAGE_CHANGED"
    VALUE_CHANGED = "VALUE_CHANGED"
    STATUS_CHANGED = "STATUS_CHANGED"
    WON = "WON"
    LOST = "LOST"


@dataclass(frozen=True, slots=True)
class OpportunitySnapshot:
    account_slug: str
    opportunity_id: str
    pipeline_id: str
    stage_id: str | None = None
    value: float = 0.0
    status: str | None = None
    contact_id: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OpportunityDelta:
    delta_type: DeltaType
    current: OpportunitySnapshot
    previous: OpportunitySnapshot | None = None
    field: str | None = None
    old_value: str | float | None = None
    new_value: str | float | None = None
    detected_at: datetime = datetime.now(UTC)
