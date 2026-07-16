from pathlib import Path

from tau_coding import TauPaths, TauResourcePaths
from tau_coding.resources import derive_description, parse_markdown_resource


def test_resource_paths_use_tau_subdirectories(tmp_path: Path) -> None:
    paths = TauResourcePaths(root=tmp_path, agents_root=None)

    assert paths.skills_dir == tmp_path / "skills"
    assert paths.prompts_dir == tmp_path / "prompts"
    assert paths.skills_dirs[1:] == (tmp_path / "skills",)
    assert paths.skills_dirs[0].name == "skills"
    assert paths.skills_dirs[0].parent.name == "data"
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

    assert paths.skills_dirs[1:] == (
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
