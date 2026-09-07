from pathlib import Path

from tau_ai.model_catalog import RuntimeModel, RuntimeModelCatalog
from tau_ai.model_limits import RuntimeModelLimits
from tau_coding.codex_model_store import (
    cached_codex_model_catalog,
    save_codex_model_catalog,
)
from tau_coding.paths import TauPaths


def test_codex_model_catalog_round_trips_with_account_scope(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau")
    catalog = RuntimeModelCatalog(
        (
            RuntimeModel(
                id="gpt-live",
                name="GPT Live",
                limits=RuntimeModelLimits(
                    context_window=400_000,
                    max_output_tokens=100_000,
                    effective_context_window_percent=95,
                    auto_compact_token_limit=350_000,
                ),
                input_modalities=("text", "image"),
                thinking_levels=("low", "high"),
                default_thinking_level="high",
            ),
        )
    )

    path = save_codex_model_catalog(catalog, account_id="account-1", paths=paths, now=123.0)

    assert path == paths.codex_models_store_path
    assert cached_codex_model_catalog(paths, account_id="other-account") is None
    assert cached_codex_model_catalog(paths, account_id="account-1") == catalog


def test_codex_model_catalog_cache_rejects_invalid_documents(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau")
    paths.codex_models_store_path.parent.mkdir(parents=True)
    paths.codex_models_store_path.write_text(
        '{"schema_version": 1, "account_id": "account-1", "catalog": {"models": []}}',
        encoding="utf-8",
    )

    assert cached_codex_model_catalog(paths, account_id="account-1") is None
