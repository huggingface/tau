from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from tau_coding.ghl.client import GhlClient
from tau_coding.ghl.models import AccountConfig


class MultiAccountClient:
    def __init__(
        self,
        accounts: dict[str, AccountConfig],
        client_factory: Callable[[AccountConfig], Any] | None = None,
    ) -> None:
        self.configs = accounts
        self._factory = client_factory or (
            lambda c: GhlClient(c.api_key, location_id=c.location_id, base_url=c.base_url)
        )
        self.clients: dict[str, Any] = {}

    async def __aenter__(self) -> MultiAccountClient:
        await self.open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def open(self) -> None:
        self.clients = {s: self._factory(c) for s, c in self.configs.items()}

    async def aclose(self) -> None:
        for client in self.clients.values():
            close = getattr(client, "aclose", None)
            if close:
                await close()

    async def for_each_account(
        self, operation: Callable[[str, Any, AccountConfig], Awaitable[Any]]
    ) -> dict[str, Any]:
        if not self.clients:
            await self.open()
        results: dict[str, Any] = {}
        for slug, client in self.clients.items():
            try:
                results[slug] = {
                    "ok": True,
                    "data": await operation(slug, client, self.configs[slug]),
                }
            except Exception as exc:
                results[slug] = {"ok": False, "error": str(exc)}
        return results

    async def list_opportunities(self, pipeline_id: str | None = None) -> dict[str, Any]:
        return await self.for_each_account(lambda _s, c, _cfg: c.list_opportunities(pipeline_id))

    async def enroll_workflow(self, account_slug: str, contact_id: str, workflow_id: str) -> Any:
        if not self.clients:
            await self.open()
        return await self.clients[account_slug].enroll_workflow(contact_id, workflow_id)
