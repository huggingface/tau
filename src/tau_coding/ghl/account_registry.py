# mypy: ignore-errors
from __future__ import annotations

import json
import os
from pathlib import Path

from tau_coding.ghl.models import AccountConfig, BrandInfo


class AccountRegistry:
    def __init__(self, accounts: dict[str, AccountConfig]) -> None:
        self.accounts = accounts

    def __iter__(self):
        return iter(self.accounts.values())

    def get(self, slug: str) -> AccountConfig:
        return self.accounts[slug]

    @classmethod
    def discover(cls, path: str | Path | None = None) -> AccountRegistry:
        accounts: dict[str, AccountConfig] = {}
        config_path = (
            Path(path or os.getenv("TAU_GHL_ACCOUNTS_FILE", ""))
            if (path or os.getenv("TAU_GHL_ACCOUNTS_FILE"))
            else None
        )
        if config_path and config_path.exists():
            data = (
                json.loads(config_path.read_text())
                if config_path.suffix == ".json"
                else _read_simple_yaml(config_path)
            )
            for item in data.get("accounts", []):
                accounts[item["brand_slug"]] = _account_from_mapping(item)
        prefix = "TAU_GHL_ACCOUNT_"
        grouped: dict[str, dict[str, str]] = {}
        for key, value in os.environ.items():
            if key.startswith(prefix):
                rest = key[len(prefix) :]
                slug, _, field = rest.partition("_")
                grouped.setdefault(slug.lower().replace("_", "-"), {})[field.lower()] = value
        for slug, values in grouped.items():
            if "api_key" in values and "location_id" in values:
                accounts.setdefault(
                    slug,
                    AccountConfig(
                        brand_slug=slug,
                        api_key=values["api_key"],
                        location_id=values["location_id"],
                        brand=BrandInfo(name=values.get("name", slug), slug=slug),
                    ),
                )
        return cls(accounts)


def _account_from_mapping(item: dict) -> AccountConfig:
    brand = item.get("brand") or {}
    return AccountConfig(
        brand_slug=item["brand_slug"],
        location_id=item["location_id"],
        api_key=item["api_key"],
        base_url=item.get("base_url", "https://services.leadconnectorhq.com"),
        brand=BrandInfo(
            name=brand.get("name", item["brand_slug"]),
            slug=brand.get("slug", item["brand_slug"]),
            color=brand.get("color"),
            domain=brand.get("domain"),
        ),
        pipelines=tuple(item.get("pipelines", ())),
        metadata=dict(item.get("metadata", {})),
    )


def _read_simple_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return json.loads(path.read_text())
