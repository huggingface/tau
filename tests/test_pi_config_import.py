import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.paths import TauPaths
from tau_coding.pi_config_import import PiConfigImportError, plan_pi_config_import
from tau_coding.provider_config import load_provider_settings


def test_plan_pi_config_import_maps_root_provider(tmp_path: Path) -> None:
    pi_config = tmp_path / "config.json"
    pi_config.write_text(
        json.dumps(
            {
                "provider": "anthropic",
                "model": "claude-3-5-sonnet-latest",
                "api_key": "secret",
                "unknown": True,
            }
        ),
        encoding="utf-8",
    )

    plan = plan_pi_config_import(
        pi_config,
        paths=TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"),
    )

    provider = plan.settings.get_provider("anthropic")
    assert plan.imported_providers == ("anthropic",)
    assert plan.settings.default_provider == "anthropic"
    assert provider.default_model == "claude-3-5-sonnet-latest"
    assert provider.api_key_env == "ANTHROPIC_API_KEY"
    assert any("raw API key" in warning for warning in plan.warnings)
    assert "Pi config field was not imported: unknown" in plan.warnings


def test_plan_pi_config_import_maps_provider_collection(tmp_path: Path) -> None:
    pi_config = tmp_path / "config.toml"
    pi_config.write_text(
        """
default_provider = "local"

[providers.local]
base_url = "http://localhost:11434/v1/"
api_key_env = "LOCAL_API_KEY"
default_model = "qwen-coder"
""",
        encoding="utf-8",
    )

    plan = plan_pi_config_import(
        pi_config,
        paths=TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"),
    )

    provider = plan.settings.get_provider("local")
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.api_key_env == "LOCAL_API_KEY"
    assert provider.default_model == "qwen-coder"
    assert plan.settings.default_provider == "local"


def test_plan_pi_config_import_rejects_missing_provider(tmp_path: Path) -> None:
    pi_config = tmp_path / "config.json"
    pi_config.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")

    with pytest.raises(PiConfigImportError, match="importable provider"):
        plan_pi_config_import(
            pi_config,
            paths=TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"),
        )


def test_config_import_pi_dry_run_does_not_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    pi_config = tmp_path / "pi.json"
    pi_config.write_text(
        json.dumps({"provider": "openai", "model": "gpt-4.1", "api_key_env": "OPENAI_KEY"}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["--dry-run", "config", "import-pi", str(pi_config)])

    assert result.exit_code == 0
    assert '"default_provider": "openai"' in result.stdout
    assert '"default_model": "gpt-4.1"' in result.stdout
    assert not (tmp_path / ".tau" / "providers.json").exists()


def test_config_import_pi_writes_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    pi_config = tmp_path / "pi.json"
    pi_config.write_text(
        json.dumps({"provider": "openai", "model": "gpt-4.1", "api_key_env": "OPENAI_KEY"}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["config", "import-pi", str(pi_config)])

    settings = load_provider_settings(TauPaths(home=tmp_path / ".tau"))
    assert result.exit_code == 0
    assert "Imported Pi providers openai" in result.stdout
    assert settings.get_provider("openai").default_model == "gpt-4.1"


def test_config_import_pi_requires_yes_for_existing_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    (tau_home / "providers.json").write_text(
        json.dumps({"default_provider": "openai", "provider_preferences": {"openai": {}}}),
        encoding="utf-8",
    )
    pi_config = tmp_path / "pi.json"
    pi_config.write_text(json.dumps({"provider": "openai", "model": "gpt-4.1"}), encoding="utf-8")

    result = CliRunner().invoke(app, ["config", "import-pi", str(pi_config)])

    assert result.exit_code == 2
    assert "Tau provider settings already exist" in result.output


def test_config_import_pi_yes_updates_existing_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    (tau_home / "providers.json").write_text(
        json.dumps({"default_provider": "openai", "provider_preferences": {"openai": {}}}),
        encoding="utf-8",
    )
    pi_config = tmp_path / "pi.json"
    pi_config.write_text(json.dumps({"provider": "openai", "model": "gpt-4.1"}), encoding="utf-8")

    result = CliRunner().invoke(app, ["--yes", "config", "import-pi", str(pi_config)])

    assert result.exit_code == 0
    assert (
        load_provider_settings(TauPaths(home=tau_home)).get_provider("openai").default_model
        == "gpt-4.1"
    )
