from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BrandInfo:
    name: str
    slug: str
    color: str | None = None
    domain: str | None = None


@dataclass(frozen=True, slots=True)
class AccountConfig:
    brand_slug: str
    location_id: str
    api_key: str
    base_url: str = "https://services.leadconnectorhq.com"
    brand: BrandInfo | None = None
    pipelines: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
