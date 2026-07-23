from pathlib import Path

import pytest

from tau_coding.paths import TauPaths, _default_agents_home, _default_tau_home


def test_tau_paths_user_locations(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    assert paths.sessions_dir == tmp_path / ".tau" / "sessions"
    assert paths.user_skills_dir == tmp_path / ".tau" / "skills"
    assert paths.user_prompts_dir == tmp_path / ".tau" / "prompts"
    assert paths.user_agents_skills_dir == tmp_path / ".agents" / "skills"
    assert paths.user_agents_prompts_dir == tmp_path / ".agents" / "prompts"


def test_tau_paths_project_locations(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / "home", agents_home=tmp_path / "agents")
    cwd = tmp_path / "project"

    assert paths.project_tau_dir(cwd) == cwd / ".tau"
    assert paths.project_agents_dir(cwd) == cwd / ".agents"
    assert paths.project_skills_dir(cwd) == cwd / ".tau" / "skills"
    assert paths.project_prompts_dir(cwd) == cwd / ".tau" / "prompts"
    assert paths.project_agents_skills_dir(cwd) == cwd / ".agents" / "skills"
    assert paths.project_agents_prompts_dir(cwd) == cwd / ".agents" / "prompts"


def test_default_session_path_uses_home_sessions_and_readable_project_path(
    tmp_path: Path,
) -> None:
    paths = TauPaths(home=tmp_path / "home", agents_home=tmp_path / "agents")
    cwd = tmp_path / "repos" / "exploration" / "tau"
    cwd.mkdir(parents=True)

    session_path = paths.default_session_path(cwd)

    assert session_path.name == "default.jsonl"
    assert session_path.parent.parent == tmp_path / "home" / "sessions"
    assert "repos-exploration-tau-" in session_path.parent.name
    assert len(session_path.parent.name.rsplit("-", maxsplit=1)[-1]) == 6
    assert session_path.parent.exists()


def test_tau_home_defaults_to_dot_tau(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAU_HOME", raising=False)
    home = _default_tau_home()
    assert home == Path.home() / ".tau"


def test_tau_home_respects_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom_home = tmp_path / "custom-tau"
    monkeypatch.setenv("TAU_HOME", str(custom_home))
    home = _default_tau_home()
    assert home == custom_home


def test_tau_home_expands_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAU_HOME", "~/my-tau")
    home = _default_tau_home()
    assert home == Path.home() / "my-tau"


def test_agents_home_defaults_to_dot_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAU_AGENTS_HOME", raising=False)
    home = _default_agents_home()
    assert home == Path.home() / ".agents"


def test_agents_home_respects_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom_home = tmp_path / "custom-agents"
    monkeypatch.setenv("TAU_AGENTS_HOME", str(custom_home))
    home = _default_agents_home()
    assert home == custom_home


def test_agents_home_expands_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAU_AGENTS_HOME", "~/my-agents")
    home = _default_agents_home()
    assert home == Path.home() / "my-agents"


def test_taupaths_uses_tau_home_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom_home = tmp_path / "custom-tau"
    monkeypatch.setenv("TAU_HOME", str(custom_home))
    paths = TauPaths()
    assert paths.home == custom_home
    assert paths.sessions_dir == custom_home / "sessions"
    assert paths.user_skills_dir == custom_home / "skills"


def test_taupaths_uses_tau_agents_home_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    custom_agents = tmp_path / "custom-agents"
    monkeypatch.setenv("TAU_AGENTS_HOME", str(custom_agents))
    paths = TauPaths()
    assert paths.agents_home == custom_agents
    assert paths.user_agents_skills_dir == custom_agents / "skills"
