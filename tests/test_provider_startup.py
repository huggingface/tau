"""Tests for shared dynamic-provider startup ordering."""

from pathlib import Path

import pytest

from tau_coding.provider_startup import prepare_provider_startup

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _extension(tmp_path: Path) -> Path:
    path = tmp_path / "dynamic_provider.py"
    path.write_text(
        """
from tau_coding.extensions import (
    DynamicProvider, NoAuth, OpenAICompatibleTransport, ProviderModel,
)

CALLS = []

async def discover(context):
    CALLS.append((context.network_allowed, context.source))
    return (ProviderModel(id="fresh", display_name="Fresh model"),)


def setup(tau):
    tau.register_provider(DynamicProvider(
        id="local-test",
        display_name="Local test",
        transport=OpenAICompatibleTransport("http://127.0.0.1:8080/v1"),
        auth=NoAuth(),
        models=(ProviderModel(id="cached"),),
        refresh_models=discover,
    ))
""",
        encoding="utf-8",
    )
    return path


async def test_startup_refreshes_only_explicitly_requested_provider(tmp_path: Path) -> None:
    extension = _extension(tmp_path)

    unrelated = await prepare_provider_startup(
        cwd=tmp_path,
        requested_provider="openai",
        extension_paths=(extension,),
        extensions_enabled=False,
    )
    assert unrelated.settings.get_provider("local-test").models == ("cached",)

    targeted = await prepare_provider_startup(
        cwd=tmp_path,
        requested_provider="local-test",
        extension_paths=(extension,),
        extensions_enabled=False,
    )
    provider = targeted.settings.get_provider("local-test")
    assert provider.models == ("fresh",)
    assert provider.model_metadata["fresh"].name == "Fresh model"  # type: ignore[union-attr]
