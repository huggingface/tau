from pathlib import Path

from tau_coding.paths import TauPaths


def test_tau_paths_user_locations(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    assert paths.sessions_dir == tmp_path / ".tau" / "sessions"
    assert paths.agent_calls_log_path == tmp_path / ".tau" / "logs" / "agent-calls.jsonl"
    assert paths.llm_observations_log_path == tmp_path / ".tau" / "logs" / "llm-observations.jsonl"
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
