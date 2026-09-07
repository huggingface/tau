from __future__ import annotations

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest
from textual.app import App
from textual.color import Color
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Static
from typer.testing import CliRunner

from tau_agent.provider import ModelProvider
from tau_agent.session import SessionEntry
from tau_coding import SessionManager, jsonl_session_storage
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
    resource_paths_with_cwd,
    resource_paths_with_project_trust,
)
from tau_coding.session import CodingSession, CodingSessionConfig
from tau_coding.tui.project_trust import ProjectTrustScreen, _ProjectTrustApp
from tau_coding.tui.themes import TAU_DARK_THEME


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


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS filesystem casing regression")
def test_macos_alternate_case_preserves_exact_child_decline(tmp_path: Path) -> None:
    parent = tmp_path / "Parent"
    child = parent / "MixedCase"
    child.mkdir(parents=True)
    alternate = parent / "mixedcase"
    if not alternate.is_dir():
        pytest.skip("temporary filesystem is case-sensitive")

    store = ProjectTrustStore(_paths(tmp_path))
    parent_key = canonicalize_project_path(parent)
    child_key = canonicalize_project_path(child)
    alternate_key = canonicalize_project_path(alternate)
    assert alternate_key == child_key

    store.set(parent_key, "trusted")
    store.set(child_key, "untrusted")

    saved = store.nearest(alternate_key)
    assert saved is not None
    assert saved.path == child_key
    assert saved.decision == "untrusted"


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
        boundary = str(host.screen.query_one("#project-trust-boundary", Static).content)
        help_text = str(host.screen.query_one("#project-trust-help", Static).content)
        displayed_path = str(host.screen.query_one("#project-trust-path", Static).content)
        summary_copy = str(host.screen.query_one("#project-trust-summary", Static).content)
        parent_choice = host.screen.query_one("#trust-trust-parent", ListItem)
        assert "not a sandbox" in boundary
        assert "Escape cancels" in help_text
        assert displayed_path == str(project.resolve())
        assert "context (1)" in summary_copy
        assert str(project.parent.resolve()) in str(parent_choice.query_one(Static).content)
        assert parent_choice.has_class("project-trust-choice")
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
    ("choice_id", "index", "expected"),
    [
        ("#trust-trust-exact", 0, "trust-exact"),
        ("#trust-trust-parent", 1, "trust-parent"),
        ("#trust-trust-run", 2, "trust-run"),
        ("#trust-decline-exact", 3, "decline-exact"),
        ("#trust-decline-run", 4, "decline-run"),
    ],
)
@pytest.mark.anyio
async def test_tui_trust_modal_all_choices_support_arrow_navigation(
    tmp_path: Path, choice_id: str, index: int, expected: str
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
        choice_list = host.screen.query_one("#project-trust-list", ListView)
        assert host.screen.focused is choice_list
        assert host.screen.query_one(choice_id, ListItem).has_class("project-trust-choice")
        for _ in range(index):
            await pilot.press("down")
        assert choice_list.index == index
        await pilot.press("enter")
        await pilot.pause()

    assert results == [expected]


@pytest.mark.anyio
async def test_standalone_trust_modal_uses_tau_dark_palette(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    summary = ProtectedResourceDetector().detect(canonicalize_project_path(project))
    request = ProjectTrustRequest(canonicalize_project_path(project), summary, None)
    host = _ProjectTrustApp(request)

    async with host.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        dialog = host.screen.query_one("#project-trust-dialog", Vertical)
        choice_list = host.screen.query_one("#project-trust-list", ListView)
        highlighted_label = choice_list.query_one(ListItem).query_one(Label)
        help_text = str(host.screen.query_one("#project-trust-help", Static).content)

        assert host.theme == TAU_DARK_THEME.name
        assert "Escape exits Tau" in help_text
        assert dialog.styles.background == Color.parse(TAU_DARK_THEME.chrome_background)
        assert dialog.styles.color == Color.parse(TAU_DARK_THEME.chrome_text)
        assert choice_list.styles.background == Color.parse(TAU_DARK_THEME.transcript_background)
        assert highlighted_label.styles.background == Color.parse(
            TAU_DARK_THEME.highlight_background
        )
        assert highlighted_label.styles.color == Color.parse(TAU_DARK_THEME.highlight_text)


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


@pytest.mark.parametrize(
    "recovery_operation",
    ["create", "write", "fsync", "chmod", "replace", "unlink"],
)
def test_combined_commit_and_recovery_failures_remain_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovery_operation: str
) -> None:
    store = ProjectTrustStore(_paths(tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    key = canonicalize_project_path(project)
    store.set(key, "untrusted")
    prior = store.path.read_bytes()

    real_fsync_directory = project_trust_module._fsync_directory
    directory_syncs = 0

    def fail_target_directory_sync(directory: Path) -> None:
        nonlocal directory_syncs
        directory_syncs += 1
        if directory_syncs == 2:
            raise OSError("post-replace directory fsync")
        real_fsync_directory(directory)

    monkeypatch.setattr(project_trust_module, "_fsync_directory", fail_target_directory_sync)
    if recovery_operation == "unlink":
        real_unlink = Path.unlink

        def fail_pending_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == store.pending_path:
                raise OSError("rollback unlink")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_pending_unlink)
    else:
        real_atomic_replace = store._atomic_replace

        def fail_rollback(destination: Path, data: bytes, *, prefix: str) -> None:
            if prefix == ".trust-rollback-":
                raise OSError(f"rollback {recovery_operation}")
            real_atomic_replace(destination, data, prefix=prefix)

        monkeypatch.setattr(store, "_atomic_replace", fail_rollback)

    with pytest.raises(ProjectTrustError, match="recovery failed"):
        store.set(key, "trusted")

    assert store.pending_path.read_bytes() == b"present\n" + prior
    with pytest.raises(ProjectTrustError, match="incomplete update"):
        store.nearest(key)


def test_pending_journal_after_revocation_never_restores_prior_trust(tmp_path: Path) -> None:
    store = ProjectTrustStore(_paths(tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    key = canonicalize_project_path(project)
    store.set(key, "trusted")
    prior_trusted = store.path.read_bytes()
    store.set(key, "untrusted")
    revoked = store.path.read_bytes()

    # Simulate a crash after trust.json was replaced with the revocation but
    # before the writer removed its undo journal.
    store.pending_path.write_bytes(b"present\n" + prior_trusted)

    with pytest.raises(ProjectTrustError, match="explicit recovery"):
        store.nearest(key)
    assert store.path.read_bytes() == revoked
    assert store.pending_path.read_bytes() == b"present\n" + prior_trusted


def test_resource_plan_always_rebinds_to_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    original = TauResourcePaths(
        root=tmp_path / "home/.tau",
        cwd=source,
        agents_root=tmp_path / "home/.agents",
        paths=_paths(tmp_path),
        project_resources_enabled=False,
    )

    rebound = resource_paths_with_cwd(original, destination.resolve())

    assert rebound.cwd == destination.resolve()
    assert rebound.root == original.root
    assert rebound.agents_root == original.agents_root
    assert rebound.paths == original.paths
    assert rebound.project_resources_enabled is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("choice", "expected_text"),
    [("trust-run", "DESTINATION-CONTEXT"), ("decline-run", None)],
)
async def test_source_bound_plan_uses_destination_resources_for_trust_choice(
    tmp_path: Path, choice: str, expected_text: str | None
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "AGENTS.md").write_text("SOURCE-CONTEXT", encoding="utf-8")
    (destination / "AGENTS.md").write_text("DESTINATION-CONTEXT", encoding="utf-8")
    source_extension = source / ".tau/extensions/source.py"
    destination_extension = destination / ".tau/extensions/destination.py"
    source_extension.parent.mkdir(parents=True)
    destination_extension.parent.mkdir(parents=True)
    source_extension.write_text(
        "def setup(tau):\n    tau.add_prompt_guideline('SOURCE-EXTENSION')\n",
        encoding="utf-8",
    )
    destination_extension.write_text(
        "def setup(tau):\n    tau.add_prompt_guideline('DESTINATION-EXTENSION')\n",
        encoding="utf-8",
    )
    observed: list[Path] = []

    async def prompt(request: ProjectTrustRequest) -> str:
        observed.append(request.cwd.value)
        return choice

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=destination,
            resource_paths=TauResourcePaths(
                root=tmp_path / "home/.tau", agents_root=None, cwd=source
            ),
            trust_interactive=True,
            trust_prompt=prompt,  # type: ignore[arg-type]
            project_extensions_enabled=True,
        )
    )

    assert observed == [destination.resolve()]
    assert session._resource_paths.cwd == destination.resolve()
    assert "SOURCE-CONTEXT" not in session.system_prompt
    assert "SOURCE-EXTENSION" not in session.system_prompt
    assert ("DESTINATION-CONTEXT" in session.system_prompt) is (expected_text is not None)
    assert ("DESTINATION-EXTENSION" in session.system_prompt) is (expected_text is not None)


@pytest.mark.anyio
@pytest.mark.parametrize("choice", ["trust-run", "decline-run"])
async def test_failed_reload_does_not_commit_run_choice_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    coordinator = ProjectTrustCoordinator(ProjectTrustStore(_paths(tmp_path)))
    prompts = 0

    async def prompt(_request: ProjectTrustRequest) -> str:
        nonlocal prompts
        prompts += 1
        return choice

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=project,
            resource_paths=TauResourcePaths(root=tmp_path / "home/.tau", agents_root=None),
            project_trust_coordinator=coordinator,
            trust_interactive=True,
            trust_prompt=prompt,  # type: ignore[arg-type]
        )
    )
    (project / "AGENTS.md").write_text("NEW-CONTEXT", encoding="utf-8")

    from tau_coding import session as session_module

    real_load = session_module._load_session_resources
    attempts = 0

    def fail_once(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("resource preparation failed")
        return real_load(*args, **kwargs)

    monkeypatch.setattr(session_module, "_load_session_resources", fail_once)
    with pytest.raises(ValueError, match="resource preparation failed"):
        await session.reload()
    assert prompts == 1
    assert session.project_trust_resolution is not None
    assert session.project_trust_resolution.source == "empty"

    await session.reload()
    assert prompts == 2
    assert ("NEW-CONTEXT" in session.system_prompt) is (choice == "trust-run")


@pytest.mark.anyio
async def test_failed_project_extension_reload_re_resolves_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    coordinator = ProjectTrustCoordinator(ProjectTrustStore(_paths(tmp_path)))
    prompts = 0

    async def prompt(_request: ProjectTrustRequest) -> str:
        nonlocal prompts
        prompts += 1
        return "trust-run"

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=project,
            resource_paths=TauResourcePaths(root=tmp_path / "home/.tau", agents_root=None),
            project_trust_coordinator=coordinator,
            project_extensions_enabled=True,
            trust_interactive=True,
            trust_prompt=prompt,
        )
    )
    extension = project / ".tau/extensions/project.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(
        "def setup(tau):\n    tau.add_prompt_guideline('DESTINATION-EXTENSION')\n",
        encoding="utf-8",
    )
    from tau_coding.extensions.runtime import ExtensionRuntime

    real_load = ExtensionRuntime.load

    def fail_project_setup(self: ExtensionRuntime, *args: object, **kwargs: object) -> None:
        if kwargs.get("include_project_dir") is True:
            raise RuntimeError("setup failed")
        real_load(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ExtensionRuntime, "load", fail_project_setup)
    with pytest.raises(RuntimeError, match="setup failed"):
        await session.reload()
    assert prompts == 1
    assert session.project_trust_resolution is not None
    assert session.project_trust_resolution.source == "empty"

    monkeypatch.setattr(ExtensionRuntime, "load", real_load)
    await session.reload()
    assert prompts == 2
    assert "DESTINATION-EXTENSION" in session.system_prompt


@pytest.mark.anyio
async def test_cancelled_destination_staging_leaves_source_session_and_cache_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "AGENTS.md").write_text("SOURCE-ACTIVE", encoding="utf-8")
    (destination / "AGENTS.md").write_text("DESTINATION-CANCELLED", encoding="utf-8")
    coordinator = ProjectTrustCoordinator(ProjectTrustStore(_paths(tmp_path)))
    resources = TauResourcePaths(root=tmp_path / "home/.tau", agents_root=None)
    active = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=source,
            resource_paths=resources,
            project_trust_coordinator=coordinator,
            trust_override="approve",
        )
    )
    source_prompt = active.system_prompt
    source_runtime = active._extension_runtime

    async def cancel(_request: ProjectTrustRequest) -> None:
        return None

    staged = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=destination,
            resource_paths=active._resource_paths,
            project_trust_coordinator=coordinator,
            trust_interactive=True,
            trust_prompt=cancel,
        )
    )

    assert staged.project_trust_resolution is not None
    assert staged.project_trust_resolution.cancelled is True
    assert active.cwd == source.resolve()
    assert active.system_prompt == source_prompt
    assert active._extension_runtime is source_runtime
    assert destination.resolve() not in coordinator._cache


@pytest.mark.anyio
async def test_new_session_trust_cancellation_preserves_active_session(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    paths = _paths(tmp_path)
    manager = SessionManager(paths)
    record = manager.create_session(cwd=project, model="fake")
    coordinator = ProjectTrustCoordinator(ProjectTrustStore(paths))
    prompts = 0

    async def cancel(_request: ProjectTrustRequest) -> None:
        nonlocal prompts
        prompts += 1
        return None

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=jsonl_session_storage(record.path),
            cwd=project,
            session_id=record.id,
            session_manager=manager,
            resource_paths=TauResourcePaths(
                root=paths.home,
                agents_root=paths.agents_home,
                paths=paths,
            ),
            project_trust_coordinator=coordinator,
            trust_interactive=True,
            trust_prompt=cancel,
        )
    )
    await session.emit_pending_session_start()
    old_id = session.session_id
    old_runtime = session._extension_runtime
    old_prompt = session.system_prompt
    old_resolution = session.project_trust_resolution
    (project / "AGENTS.md").write_text("NEW-PROTECTED-CONTEXT", encoding="utf-8")

    with pytest.raises(ValueError, match="current session unchanged"):
        await session.new_session()

    assert prompts == 1
    assert session.session_id == old_id
    assert session._extension_runtime is old_runtime
    assert session.system_prompt == old_prompt
    assert session.project_trust_resolution == old_resolution
    assert project.resolve() in coordinator._cache
    assert coordinator._cache[project.resolve()].source == "empty"


@pytest.mark.anyio
async def test_reload_cancellation_during_shutdown_preserves_snapshot_and_re_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    coordinator = ProjectTrustCoordinator(ProjectTrustStore(_paths(tmp_path)))
    prompts = 0

    async def prompt(_request: ProjectTrustRequest) -> str:
        nonlocal prompts
        prompts += 1
        return "trust-run"

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=project,
            resource_paths=TauResourcePaths(root=tmp_path / "home/.tau", agents_root=None),
            project_trust_coordinator=coordinator,
            trust_interactive=True,
            trust_prompt=prompt,
        )
    )
    await session.emit_pending_session_start()
    old_runtime = session._extension_runtime
    old_prompt = session.system_prompt
    (project / "AGENTS.md").write_text("NEW-CONTEXT", encoding="utf-8")

    async def cancel_shutdown(_reason: str) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(old_runtime, "emit_session_shutdown", cancel_shutdown)
    with pytest.raises(asyncio.CancelledError):
        await session.reload()

    assert prompts == 1
    assert session._extension_runtime is old_runtime
    assert session.system_prompt == old_prompt
    assert coordinator._cache[project.resolve()].source == "empty"

    monkeypatch.undo()
    await session.reload()
    assert prompts == 2
    assert "NEW-CONTEXT" in session.system_prompt


@pytest.mark.anyio
async def test_reload_cancellation_during_staged_start_preserves_snapshot_and_re_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tau_coding.extensions.runtime import ExtensionRuntime

    project = tmp_path / "project"
    project.mkdir()
    coordinator = ProjectTrustCoordinator(ProjectTrustStore(_paths(tmp_path)))
    prompts = 0

    async def prompt(_request: ProjectTrustRequest) -> str:
        nonlocal prompts
        prompts += 1
        return "trust-run"

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=project,
            resource_paths=TauResourcePaths(root=tmp_path / "home/.tau", agents_root=None),
            project_trust_coordinator=coordinator,
            trust_interactive=True,
            trust_prompt=prompt,
        )
    )
    await session.emit_pending_session_start()
    old_runtime = session._extension_runtime
    old_prompt = session.system_prompt
    (project / "AGENTS.md").write_text("NEW-CONTEXT", encoding="utf-8")
    real_start = ExtensionRuntime.emit_session_start
    cancelled = False

    async def cancel_staged_start(self: ExtensionRuntime, reason: str) -> None:
        nonlocal cancelled
        if reason == "reload" and self is not old_runtime and not cancelled:
            cancelled = True
            raise asyncio.CancelledError
        await real_start(self, reason)  # type: ignore[arg-type]

    monkeypatch.setattr(ExtensionRuntime, "emit_session_start", cancel_staged_start)
    with pytest.raises(asyncio.CancelledError):
        await session.reload()

    assert prompts == 1
    assert session._extension_runtime is old_runtime
    assert session.system_prompt == old_prompt
    assert coordinator._cache[project.resolve()].source == "empty"

    await session.reload()
    assert prompts == 2
    assert "NEW-CONTEXT" in session.system_prompt


@pytest.mark.anyio
async def test_destination_adoption_cancellation_keeps_source_and_retry_resolves_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tau_coding.extensions.runtime import ExtensionRuntime

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "AGENTS.md").write_text("SOURCE-CONTEXT", encoding="utf-8")
    (destination / "AGENTS.md").write_text("DESTINATION-CONTEXT", encoding="utf-8")
    coordinator = ProjectTrustCoordinator(ProjectTrustStore(_paths(tmp_path)))
    resources = TauResourcePaths(root=tmp_path / "home/.tau", agents_root=None)
    active = await CodingSession.load(
        CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=source,
            resource_paths=resources,
            project_trust_coordinator=coordinator,
            trust_override="approve",
        )
    )
    await active.emit_pending_session_start()
    source_runtime = active._extension_runtime
    source_prompt = active.system_prompt
    prompts = 0

    async def prompt(_request: ProjectTrustRequest) -> str:
        nonlocal prompts
        prompts += 1
        return "trust-run"

    def replacement_config() -> CodingSessionConfig:
        return CodingSessionConfig(
            provider=cast(ModelProvider, object()),
            model="fake",
            storage=_Storage(),
            cwd=destination,
            resource_paths=resources,
            project_trust_coordinator=coordinator,
            trust_interactive=True,
            trust_prompt=prompt,
            extension_runtime=active._extension_runtime,
        )

    replacement = await CodingSession.load(replacement_config())
    real_start = ExtensionRuntime.emit_session_start
    cancelled_runtime = replacement._extension_runtime

    async def cancel_adoption(self: ExtensionRuntime, reason: str) -> None:
        if self is cancelled_runtime:
            raise asyncio.CancelledError
        await real_start(self, reason)  # type: ignore[arg-type]

    monkeypatch.setattr(ExtensionRuntime, "emit_session_start", cancel_adoption)
    with pytest.raises(asyncio.CancelledError):
        await active._adopt_replacement(replacement, reason="resume")

    assert prompts == 1
    assert active.cwd == source.resolve()
    assert active.system_prompt == source_prompt
    assert active._extension_runtime is source_runtime
    assert destination.resolve() not in coordinator._cache

    monkeypatch.setattr(ExtensionRuntime, "emit_session_start", real_start)
    retry = await CodingSession.load(replacement_config())
    await active._adopt_replacement(retry, reason="resume")
    assert prompts == 2
    assert active.cwd == destination.resolve()
    assert "DESTINATION-CONTEXT" in active.system_prompt
    assert coordinator._cache[destination.resolve()].source == "ui"
