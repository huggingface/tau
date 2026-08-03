from pathlib import Path

import pytest

from tau_coding import TauPaths, TauResourcePaths
from tau_coding.resources import (
    ResourceError,
    derive_description,
    discover_system_prompt_resources,
    parse_markdown_resource,
)


def test_resource_paths_use_tau_subdirectories(tmp_path: Path) -> None:
    paths = TauResourcePaths(root=tmp_path, agents_root=None)

    assert paths.skills_dir == tmp_path / "skills"
    assert paths.prompts_dir == tmp_path / "prompts"
    assert paths.skills_dirs == (tmp_path / "skills",)
    assert paths.prompts_dirs == (tmp_path / "prompts",)


def test_resource_paths_include_agents_and_project_directories(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    tau_home = tmp_path / "home" / ".tau"
    agents_home = tmp_path / "home" / ".agents"
    paths = TauResourcePaths(
        root=tau_home,
        agents_root=agents_home,
        cwd=cwd,
        paths=TauPaths(home=tau_home, agents_home=agents_home),
    )

    assert paths.skills_dirs == (
        tau_home / "skills",
        agents_home / "skills",
        cwd / ".tau" / "skills",
        cwd / ".agents" / "skills",
    )
    assert paths.prompts_dirs == (
        tau_home / "prompts",
        agents_home / "prompts",
        cwd / ".tau" / "prompts",
        cwd / ".agents" / "prompts",
    )
    assert paths.system_prompt_path == tau_home / "SYSTEM.md"
    assert paths.append_system_prompt_path == tau_home / "APPEND_SYSTEM.md"


def test_system_prompt_files_use_project_over_user_precedence(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    tau_home = tmp_path / "home" / ".tau"
    agents_home = tmp_path / "home" / ".agents"
    (cwd / ".tau").mkdir(parents=True)
    tau_home.mkdir(parents=True)
    agents_home.mkdir(parents=True)
    (tau_home / "SYSTEM.md").write_text("User base", encoding="utf-8")
    (cwd / ".tau" / "SYSTEM.md").write_text("Project base", encoding="utf-8")
    (tau_home / "APPEND_SYSTEM.md").write_text("User append", encoding="utf-8")
    # `.agents` is not a system-prompt configuration location.
    (agents_home / "SYSTEM.md").write_text("Agents base", encoding="utf-8")

    resources = discover_system_prompt_resources(
        TauResourcePaths(root=tau_home, agents_root=agents_home, cwd=cwd)
    )

    assert resources.custom_prompt == "Project base"
    assert resources.custom_prompt_path == cwd / ".tau" / "SYSTEM.md"
    assert resources.append_prompt == "User append"
    assert resources.append_prompt_path == tau_home / "APPEND_SYSTEM.md"
    assert [(item.severity, item.path) for item in resources.diagnostics] == [
        ("info", cwd / ".tau" / "SYSTEM.md"),
        ("warning", tau_home / "SYSTEM.md"),
        ("info", tau_home / "APPEND_SYSTEM.md"),
    ]


def test_explicit_prompt_values_shadow_files_without_reading_them(tmp_path: Path) -> None:
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    (tau_home / "SYSTEM.md").write_bytes(b"\xff")
    (tau_home / "APPEND_SYSTEM.md").write_bytes(b"\xff")

    resources = discover_system_prompt_resources(
        TauResourcePaths(root=tau_home, agents_root=None),
        custom_prompt_explicit=True,
        append_prompt_explicit=True,
    )

    assert resources.custom_prompt is None
    assert resources.append_prompt is None
    assert len(resources.diagnostics) == 2
    assert all("explicit startup value" in item.message for item in resources.diagnostics)


def test_selected_system_prompt_file_must_be_readable_utf8(tmp_path: Path) -> None:
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    prompt_path = tau_home / "SYSTEM.md"
    prompt_path.write_bytes(b"\xff")

    with pytest.raises(ResourceError, match="Could not read replacement system prompt file"):
        discover_system_prompt_resources(TauResourcePaths(root=tau_home, agents_root=None))


def test_parse_frontmatter_description() -> None:
    metadata, body = parse_markdown_resource(
        "---\ndescription: Write tests\n---\n# Testing\nUse pytest."
    )

    assert metadata == {"description": "Write tests"}
    assert body == "# Testing\nUse pytest."


def test_parse_frontmatter_normalizes_crlf_line_endings() -> None:
    metadata, body = parse_markdown_resource(
        "---\r\ndescription: Write tests\r\n---\r\n# Testing\r\nUse pytest."
    )

    assert metadata == {"description": "Write tests"}
    assert body == "# Testing\nUse pytest."


def test_derive_description_uses_first_heading_or_paragraph() -> None:
    assert derive_description("\n# Title\nBody") == "Title"
    assert derive_description("\nFirst paragraph\nMore") == "First paragraph"
