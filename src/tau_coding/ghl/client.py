from __future__ import annotations

from typing import Any


class GhlClient:
    def __init__(self, api_key: str, *, location_id: str, base_url: str) -> None:
        self.api_key = api_key
        self.location_id = location_id
        self.base_url = base_url

    async def __aenter__(self) -> GhlClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        return None

    async def list_pipelines(self) -> list[dict[str, Any]]:
        return []

    async def list_opportunities(self, pipeline_id: str | None = None) -> list[dict[str, Any]]:
        return []

    async def enroll_workflow(self, contact_id: str, workflow_id: str) -> dict[str, Any]:
        return {"contact_id": contact_id, "workflow_id": workflow_id}
