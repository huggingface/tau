from __future__ import annotations

import sqlite3
from pathlib import Path

from tau_coding.ghl.models import DeltaType, OpportunityDelta, OpportunitySnapshot


class MemoryDeltaStore:
    def __init__(self) -> None:
        self.snapshots: dict[tuple[str, str], OpportunitySnapshot] = {}

    def get(self, account_slug: str, opportunity_id: str) -> OpportunitySnapshot | None:
        return self.snapshots.get((account_slug, opportunity_id))

    def put(self, snapshot: OpportunitySnapshot) -> None:
        self.snapshots[(snapshot.account_slug, snapshot.opportunity_id)] = snapshot

    def all(self) -> list[OpportunitySnapshot]:
        return list(self.snapshots.values())


class SqliteDeltaStore(MemoryDeltaStore):
    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            "create table if not exists snapshots("
            "account text,id text,pipeline text,stage text,value real,"
            "status text,contact text,primary key(account,id))"
        )

    def get(self, account_slug: str, opportunity_id: str) -> OpportunitySnapshot | None:
        row = self.conn.execute(
            "select account,id,pipeline,stage,value,status,contact "
            "from snapshots where account=? and id=?",
            (account_slug, opportunity_id),
        ).fetchone()
        return OpportunitySnapshot(*row) if row else None

    def put(self, snapshot: OpportunitySnapshot) -> None:
        self.conn.execute(
            "insert or replace into snapshots values(?,?,?,?,?,?,?)",
            (
                snapshot.account_slug,
                snapshot.opportunity_id,
                snapshot.pipeline_id,
                snapshot.stage_id,
                snapshot.value,
                snapshot.status,
                snapshot.contact_id,
            ),
        )
        self.conn.commit()


def detect_delta(
    previous: OpportunitySnapshot | None, current: OpportunitySnapshot
) -> list[OpportunityDelta]:
    if previous is None:
        return [OpportunityDelta(DeltaType.CREATED, current)]
    deltas: list[OpportunityDelta] = []
    if previous.stage_id != current.stage_id:
        deltas.append(
            OpportunityDelta(
                DeltaType.STAGE_CHANGED,
                current,
                previous,
                "stage_id",
                previous.stage_id,
                current.stage_id,
            )
        )
    if previous.value != current.value:
        deltas.append(
            OpportunityDelta(
                DeltaType.VALUE_CHANGED, current, previous, "value", previous.value, current.value
            )
        )
    if previous.status != current.status:
        dtype = (
            DeltaType.WON
            if str(current.status).lower() == "won"
            else DeltaType.LOST
            if str(current.status).lower() == "lost"
            else DeltaType.STATUS_CHANGED
        )
        deltas.append(
            OpportunityDelta(dtype, current, previous, "status", previous.status, current.status)
        )
    return deltas


def sync_opportunities(
    store: MemoryDeltaStore, snapshots: list[OpportunitySnapshot]
) -> list[OpportunityDelta]:
    out: list[OpportunityDelta] = []
    for snap in snapshots:
        out.extend(detect_delta(store.get(snap.account_slug, snap.opportunity_id), snap))
        store.put(snap)
    return out
