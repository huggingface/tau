from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest
from textual.app import App
from textual.widgets import Button, Static
from typer.testing import CliRunner

from tau_agent.provider import ModelProvider
from tau_agent.session import SessionEntry
from tau_coding.cli import app
from tau_coding.paths import TauPaths
from tau_coding.project_trust import (
    ExtensionTrustResult,
    ProjectTrustCoordinator,
    ProjectTrustError,
    ProjectTrustRequest,
    ProjectTrustStore,
    ProtectedResourceDetector,
    canonicalize_project_path,
    format_trust_diagnostic,
)
from tau_coding.resources import TauResourcePaths, resource_paths_with_project_trust
from tau_coding.session import CodingSession, CodingSessionConfig
from tau_coding.tui.project_trust import ProjectTrustScreen


def _paths(tmp_path: Path) -> TauPaths:
    return TauPaths(home=tmp_path / "home" / ".tau", agents_home=tmp_path / "home" / ".agents")


def test_canonical_project_path_requires_existing_directory_and_resolves_alias(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(project, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")

    assert canonicalize_project_path(alias).value == project.resolve()
    with pytest.raises(ProjectTrustError, match="canonicalize"):
        canonicalize_project_path(tmp_path / "missing")


def test_detector_covers_protected_matrix_without_reading_contents(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    candidates = (
        project / ".tau/settings.json",
        project / ".tau/skills/tau/SKILL.md",
        project / ".agents/skills/agents/SKILL.md",
        project / ".tau/prompts/tau.md",
        project / ".agents/prompts/agents.md",
        project / ".tau/themes/theme.json",
        project / ".tau/SYSTEM.md",
        project / ".tau/APPEND_SYSTEM.md",
        project / "AGENTS.md",
        project / ".tau/AGENTS.md",
        project / ".agents/AGENTS.md",
        project / ".tau/extensions/simple.py",
        project / ".tau/extensions/package/extension.py",
        project / ".tau/extensions/manifest/pyproject.toml",
    )
    for candidate in candidates:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("protected content", encoding="utf-8")

    summary = ProtectedResourceDetector().detect(canonicalize_project_path(project))

    assert summary.categories == (
        "context",
        "extensions",
        "prompts",
        "settings",
        "skills",
        "system-prompts",
        "themes",
    )
    assert summary.counts == {
        "context": 3,
        "extensions": 3,
        "prompts": 2,
        "settings": 1,
        "skills": 2,
        "system-prompts": 2,
        "themes": 1,
    }
    assert len(summary.sample_paths) <= 12


def test_detector_ignores_empty_and_unsupported_resources(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".tau/skills").mkdir(parents=True)
    (project / ".agents/prompts").mkdir(parents=True)
    (project / "CLAUDE.md").write_text("unsupported", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]", encoding="utf-8")

    summary = ProtectedResourceDetector().detect(canonicalize_project_path(project))

    assert summary.categories == ()


def test_store_round_trip_is_sorted_and_nearest_decision_wins(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = ProjectTrustStore(paths)
    parent = tmp_path / "projects"
    child = parent / "app"
    child.mkdir(parents=True)
    parent_key = canonicalize_project_path(parent)
    child_key = canonicalize_project_path(child)

    store.set(child_key, "untrusted")
    store.set(parent_key, "trusted")

    assert store.nearest(child_key) is not None
    assert store.nearest(child_key).decision == "untrusted"  # type: ignore[union-attr]
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload == {
        "version": 1,
        "decisions": [
            {"path": str(parent.resolve()), "decision": "trusted"},
            {"path": str(child.resolve()), "decision": "untrusted"},
        ],
    }
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_parent_trust_removes_exact_child_for_inheritance(tmp_path: Path) -> None:
    store = ProjectTrustStore(_paths(tmp_path))
    child = tmp_path / "parent" / "child"
    child.mkdir(parents=True)
    child_key = canonicalize_project_path(child)
    store.set(child_key, "untrusted")

    saved_parent = store.trust_parent(child_key)

    assert store.nearest(child_key).path == saved_parent  # type: ignore[union-attr]
    assert store.nearest(child_key).decision == "trusted"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"version": 2, "decisions": []}',
        '{"version": 1, "decisions": [{"path": "relative", "decision": "trusted"}]}',
        (
            '{"version": 1, "decisions": ['
            '{"path": "/tmp/a", "decision": "trusted"},'
            '{"path": "/tmp/a", "decision": "untrusted"}]}'
        ),
    ],
)
def test_store_rejects_malformed_data(tmp_path: Path, payload: str) -> None:
    store = ProjectTrustStore(_paths(tmp_path))
    store.path.parent.mkdir(parents=True)
    store.path.write_text(payload, encoding="utf-8")

    with pytest.raises(ProjectTrustError):
        store.read()


def test_concurrent_store_updates_do_not_lose_decisions(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    projects = [tmp_path / f"project-{index}" for index in range(8)]
    for project in projects:
        project.mkdir()

    def save(project: Path) -> None:
        ProjectTrustStore(paths).set(canonicalize_project_path(project), "trusted")

    with ThreadPoolExecutor(max_workers=8) as pool:
        tuple(pool.map(save, projects))

    assert len(ProjectTrustStore(paths).read()) == len(projects)


@pytest.mark.anyio
async def test_policy_precedence_extension_before_saved_and_default(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    store = ProjectTrustStore(_paths(tmp_path))
    store.set(canonicalize_project_path(project), "untrusted")
    coordinator = ProjectTrustCoordinator(store)

    async def approve(_event: object) -> ExtensionTrustResult:
        return ExtensionTrustResult("approve")

    _summary, resolution = await coordinator.resolve(
        project,
        default="never",
        extension_deciders=(approve,),
    )

    assert resolution.trusted is True
    assert resolution.source == "extension"


@pytest.mark.anyio
async def test_malformed_store_fails_closed_but_run_override_still_works(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    store = ProjectTrustStore(_paths(tmp_path))
    store.path.parent.mkdir(parents=True)
    store.path.write_text("bad", encoding="utf-8")

    summary, declined = await ProjectTrustCoordinator(store).resolve(project)
    _summary, default_always = await ProjectTrustCoordinator(store).resolve(
        project, default="always"
    )
    _summary, approved = await ProjectTrustCoordinator(store).resolve(project, override="approve")

    assert declined.trusted is False
    assert default_always.trusted is False
    assert declined.diagnostics
    assert approved.trusted is True
    assert approved.source == "override"
    assert "not a sandbox" in format_trust_diagnostic(summary, declined)


@pytest.mark.anyio
async def test_reload_rechecks_empty_result_but_reuses_nonempty_run_decision(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    coordinator = ProjectTrustCoordinator(ProjectTrustStore(_paths(tmp_path)))

    _summary, empty = await coordinator.resolve(project)
    (project / "AGENTS.md").write_text("new rules", encoding="utf-8")
    _summary, declined = await coordinator.resolve(project, refresh=True)
    _summary, still_declined = await coordinator.resolve(
        project,
        default="always",
        refresh=True,
    )

    assert empty.source == "empty"
    assert declined.trusted is False
    assert still_declined == declined


def test_untrusted_resource_plan_keeps_user_resources_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    paths = TauResourcePaths(root=tmp_path / "home/.tau", cwd=project, agents_root=None)

    untrusted = resource_paths_with_project_trust(paths, trusted=False)

    assert untrusted.skills_dirs == (paths.root / "skills",)
    assert untrusted.prompts_dirs == (paths.root / "prompts",)
    assert untrusted.themes_dirs == (paths.root / "themes",)


@pytest.mark.anyio
async def test_tui_trust_modal_shows_boundary_parent_and_keyboard_cancel(
    tmp_path: Path,
) -> None:
    project = tmp_path / "parent/project"
    project.mkdir(parents=True)
    summary = ProtectedResourceDetector().detect(canonicalize_project_path(project))
    request = ProjectTrustRequest(canonicalize_project_path(project), summary, None)
    results: list[object | None] = []

    class Host(App[None]):
        def on_mount(self) -> None:
            self.push_screen(ProjectTrustScreen(request), results.append)

    host = Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        copy = str(host.screen.query_one("#project-trust-copy", Static).content)
        parent_button = host.screen.query_one("#trust-trust-parent", Button)
        assert "not a sandbox" in copy
        assert str(project.parent.resolve()) in str(parent_button.label)
        await pilot.press("escape")
        await pilot.pause()

    assert results == [None]


class _Storage:
    def __init__(self) -> None:
        self.entries: list[SessionEntry] = []

    async def append(self, entry: SessionEntry) -> None:
        self.entries.append(entry)

    async def read_all(self) -> list[SessionEntry]:
        return list(self.entries)


@pytest.mark.anyio
async def test_project_extensions_need_trust_and_additional_opt_in(tmp_path: Path) -> None:
    project = tmp_path / "project"
    extension_dir = project / ".tau/extensions"
    extension_dir.mkdir(parents=True)
    marker = tmp_path / "imported"
    (extension_dir / "project.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
        "def setup(tau):\n    pass\n",
        encoding="utf-8",
    )
    resources = TauResourcePaths(
        root=tmp_path / "home/.tau",
        agents_root=tmp_path / "home/.agents",
    )
    common = {
        "provider": cast(ModelProvider, object()),
        "model": "fake",
        "cwd": project,
        "resource_paths": resources,
    }

    declined = await CodingSession.load(
        CodingSessionConfig(
            **common,
            storage=_Storage(),
            trust_override="decline",
            project_extensions_enabled=True,
        )
    )
    assert not marker.exists()
    await declined.aclose()

    trusted_without_opt_in = await CodingSession.load(
        CodingSessionConfig(
            **common,
            storage=_Storage(),
            trust_override="approve",
            project_extensions_enabled=False,
        )
    )
    assert not marker.exists()
    await trusted_without_opt_in.aclose()

    trusted = await CodingSession.load(
        CodingSessionConfig(
            **common,
            storage=_Storage(),
            trust_override="approve",
            project_extensions_enabled=True,
        )
    )
    assert marker.read_text(encoding="utf-8") == "imported"
    await trusted.aclose()


def test_cli_rejects_conflicting_overrides() -> None:
    result = CliRunner().invoke(app, ["--approve", "--no-approve", "--print", "hello"])

    assert result.exit_code != 0
    assert "cannot be used together" in result.output
