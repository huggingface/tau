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
from tau_coding import project_trust as project_trust_module
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
from tau_coding.resources import (
    TauResourcePaths,
    resource_paths_with_project_trust,
)
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
        "skills",
        "system-prompts",
        "themes",
    )
    assert summary.counts == {
        "context": 3,
        "extensions": 3,
        "prompts": 2,
        "skills": 2,
        "system-prompts": 2,
        "themes": 1,
    }
    assert len(summary.sample_paths) <= 12


def test_detector_ignores_empty_and_unsupported_resources(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".tau/skills").mkdir(parents=True)
    (project / ".agents/prompts").mkdir(parents=True)
    (project / ".tau/settings.json").write_text("{}", encoding="utf-8")
    (project / ".agents/prompts/reload.md").write_text("reserved", encoding="utf-8")
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


def test_store_failures_preserve_prior_non_granting_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    key = canonicalize_project_path(project)

    operations = ("chmod", "fsync", "replace", "directory-fsync")
    for operation in operations:
        store = ProjectTrustStore(paths)
        monkeypatch.undo()
        store.set(key, "untrusted")
        before = store.path.read_bytes()

        if operation == "chmod":
            monkeypatch.setattr(
                project_trust_module.os,
                "chmod",
                lambda *_args: (_ for _ in ()).throw(OSError("chmod")),
            )
        elif operation == "fsync":
            monkeypatch.setattr(
                project_trust_module.os,
                "fsync",
                lambda *_args: (_ for _ in ()).throw(OSError("fsync")),
            )
        elif operation == "replace":
            monkeypatch.setattr(
                project_trust_module.os,
                "replace",
                lambda *_args: (_ for _ in ()).throw(OSError("replace")),
            )
        else:
            monkeypatch.setattr(
                project_trust_module,
                "_fsync_directory",
                lambda *_args: (_ for _ in ()).throw(OSError("directory fsync")),
            )

        with pytest.raises(ProjectTrustError):
            store.set(key, "trusted")
        assert store.path.read_bytes() == before
        assert json.loads(before)["decisions"][0]["decision"] == "untrusted"


@pytest.mark.anyio
async def test_session_default_declines_project_input_and_explicit_approval_loads_it(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("protected-default-probe", encoding="utf-8")
    resources = TauResourcePaths(root=tmp_path / "home/.tau", agents_root=None)
    common = {
        "provider": cast(ModelProvider, object()),
        "model": "fake",
        "cwd": project,
        "resource_paths": resources,
    }

    declined = await CodingSession.load(CodingSessionConfig(**common, storage=_Storage()))
    approved = await CodingSession.load(
        CodingSessionConfig(**common, storage=_Storage(), trust_override="approve")
    )

    assert declined.project_trust_resolution is not None
    assert declined.project_trust_resolution.trusted is False
    assert "protected-default-probe" not in declined.system_prompt
    assert "protected-default-probe" in approved.system_prompt


@pytest.mark.anyio
async def test_destination_rebuild_drops_source_project_extensions(tmp_path: Path) -> None:
    home_extensions = tmp_path / "home/.tau/extensions"
    source_extensions = tmp_path / "source/.tau/extensions"
    destination = tmp_path / "destination"
    home_extensions.mkdir(parents=True)
    source_extensions.mkdir(parents=True)
    destination.mkdir()
    (destination / "AGENTS.md").write_text("destination protected", encoding="utf-8")
    (home_extensions / "global.py").write_text(
        "def setup(tau):\n    tau.add_prompt_guideline('GLOBAL-GUIDELINE')\n",
        encoding="utf-8",
    )
    (source_extensions / "source.py").write_text(
        "def setup(tau):\n    tau.add_prompt_guideline('SOURCE-PROJECT-GUIDELINE')\n",
        encoding="utf-8",
    )
    resources = TauResourcePaths(root=tmp_path / "home/.tau", agents_root=None)
    source = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=tmp_path / "source",
            resource_paths=resources,
            trust_override="approve",
            project_extensions_enabled=True,
        )
    )
    replacement = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=destination,
            resource_paths=resources,
            trust_override="decline",
            project_extensions_enabled=True,
            extension_runtime=source._extension_runtime,
        )
    )

    assert "GLOBAL-GUIDELINE" in replacement.system_prompt
    assert "SOURCE-PROJECT-GUIDELINE" not in replacement.system_prompt
    assert "destination protected" not in replacement.system_prompt


@pytest.mark.anyio
async def test_failed_reload_preserves_complete_live_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.write_text("stable snapshot", encoding="utf-8")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=project,
            resource_paths=TauResourcePaths(root=tmp_path / "home/.tau", agents_root=None),
            trust_override="approve",
        )
    )
    old_prompt = session.system_prompt
    old_runtime = session._extension_runtime
    old_tools = tuple(tool.name for tool in session._harness.config.tools)
    old_resolution = session.project_trust_resolution
    agents.write_bytes(b"\xff")

    with pytest.raises(UnicodeDecodeError):
        await session.reload()

    assert session.system_prompt == old_prompt
    assert session._extension_runtime is old_runtime
    assert tuple(tool.name for tool in session._harness.config.tools) == old_tools
    assert session.project_trust_resolution == old_resolution


@pytest.mark.parametrize(
    ("button_id", "expected"),
    [
        ("#trust-trust-exact", "trust-exact"),
        ("#trust-trust-parent", "trust-parent"),
        ("#trust-trust-run", "trust-run"),
        ("#trust-decline-exact", "decline-exact"),
        ("#trust-decline-run", "decline-run"),
    ],
)
@pytest.mark.anyio
async def test_tui_trust_modal_all_choices_are_keyboard_focusable(
    tmp_path: Path, button_id: str, expected: str
) -> None:
    project = tmp_path / "parent/project"
    project.mkdir(parents=True)
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    summary = ProtectedResourceDetector().detect(canonicalize_project_path(project))
    request = ProjectTrustRequest(canonicalize_project_path(project), summary, None)
    results: list[object | None] = []

    class Host(App[None]):
        def on_mount(self) -> None:
            self.push_screen(ProjectTrustScreen(request), results.append)

    host = Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        assert isinstance(host.screen.focused, Button)
        host.screen.query_one(button_id, Button).focus()
        await pilot.press("enter")
        await pilot.pause()

    assert results == [expected]


@pytest.mark.anyio
async def test_extension_order_errors_and_remember_failure_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    calls: list[str] = []

    async def broken(_event: object) -> ExtensionTrustResult:
        calls.append("broken")
        raise RuntimeError("handler failure")

    async def approve(_event: object) -> ExtensionTrustResult:
        calls.append("approve")
        return ExtensionTrustResult("approve", remember=True)

    async def never_reached(_event: object) -> ExtensionTrustResult:
        calls.append("late")
        return ExtensionTrustResult("decline")

    store = ProjectTrustStore(_paths(tmp_path))
    monkeypatch.setattr(
        store, "set", lambda *_args: (_ for _ in ()).throw(ProjectTrustError("write failed"))
    )
    _summary, resolution = await ProjectTrustCoordinator(store).resolve(
        project, extension_deciders=(broken, approve, never_reached)
    )

    assert calls == ["broken", "approve"]
    assert resolution.trusted is False
    assert resolution.source == "extension"
    assert any("handler failure" in item for item in resolution.diagnostics)
    assert any("write failed" in item for item in resolution.diagnostics)


def test_store_read_lock_and_write_permission_failures_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    key = canonicalize_project_path(project)
    store = ProjectTrustStore(paths)
    store.set(key, "untrusted")
    before = store.path.read_bytes()

    original_read_text = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *args, **kwargs: (
            (_ for _ in ()).throw(PermissionError("read denied"))
            if self == store.path
            else original_read_text(self, *args, **kwargs)
        ),
    )
    with pytest.raises(ProjectTrustError, match="read"):
        store.read()
    monkeypatch.undo()

    monkeypatch.setattr(
        project_trust_module,
        "_lock",
        lambda _handle: (_ for _ in ()).throw(ProjectTrustError("lock denied")),
    )
    with pytest.raises(ProjectTrustError, match="lock"):
        store.read()
    monkeypatch.undo()

    monkeypatch.setattr(
        project_trust_module.tempfile,
        "mkstemp",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("write denied")),
    )
    with pytest.raises(ProjectTrustError, match="write"):
        store.set(key, "trusted")
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    ("choice", "trusted", "saved_decision"),
    [
        ("trust-exact", True, "trusted"),
        ("trust-run", True, None),
        ("decline-exact", False, "untrusted"),
        ("decline-run", False, None),
        (None, False, None),
    ],
)
@pytest.mark.anyio
async def test_interactive_choices_have_exact_persistence_semantics(
    tmp_path: Path, choice: object, trusted: bool, saved_decision: str | None
) -> None:
    project = tmp_path / "parent/project"
    project.mkdir(parents=True)
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    store = ProjectTrustStore(_paths(tmp_path))

    async def prompt(_request: ProjectTrustRequest) -> object:
        return choice

    _summary, resolution = await ProjectTrustCoordinator(store).resolve(
        project,
        interactive=True,
        prompt=prompt,  # type: ignore[arg-type]
    )

    assert resolution.trusted is trusted
    saved = store.nearest(canonicalize_project_path(project))
    assert (saved.decision if saved else None) == saved_decision
