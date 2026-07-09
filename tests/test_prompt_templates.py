from pathlib import Path

import pytest

from tau_coding import (
    TauResourcePaths,
    expand_prompt_template_command,
    load_prompt_templates,
    load_prompt_templates_with_diagnostics,
    render_prompt_template,
)
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.resources import ResourceError


def test_load_prompt_templates_missing_directory_returns_empty(tmp_path: Path) -> None:
    assert load_prompt_templates(TauResourcePaths(root=tmp_path, agents_root=None)) == []


def test_load_prompt_templates_from_markdown_files(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "review.md").write_text(
        "---\ndescription: Review code\n---\nReview {{ topic }}.",
        encoding="utf-8",
    )

    templates = load_prompt_templates(TauResourcePaths(root=tmp_path, agents_root=None))

    assert len(templates) == 1
    assert templates[0].name == "review"
    assert templates[0].description == "Review code"


def test_load_prompt_templates_includes_agents_directories(tmp_path: Path) -> None:
    tau_home = tmp_path / "home" / ".tau"
    agents_home = tmp_path / "home" / ".agents"
    cwd = tmp_path / "project"
    (agents_home / "prompts").mkdir(parents=True)
    (agents_home / "prompts" / "user.md").write_text("User prompt", encoding="utf-8")
    (cwd / ".agents" / "prompts").mkdir(parents=True)
    (cwd / ".agents" / "prompts" / "project.md").write_text("Project prompt", encoding="utf-8")

    templates = load_prompt_templates(
        TauResourcePaths(root=tau_home, agents_root=agents_home, cwd=cwd)
    )

    assert [template.name for template in templates] == ["project", "user"]


def test_project_prompt_template_overrides_user_template(tmp_path: Path) -> None:
    tau_home = tmp_path / "home" / ".tau"
    agents_home = tmp_path / "home" / ".agents"
    cwd = tmp_path / "project"
    (agents_home / "prompts").mkdir(parents=True)
    (agents_home / "prompts" / "review.md").write_text("User review", encoding="utf-8")
    (cwd / ".agents" / "prompts").mkdir(parents=True)
    (cwd / ".agents" / "prompts" / "review.md").write_text("Project review", encoding="utf-8")

    templates = load_prompt_templates(
        TauResourcePaths(root=tau_home, agents_root=agents_home, cwd=cwd)
    )

    assert len(templates) == 1
    assert templates[0].path == cwd / ".agents" / "prompts" / "review.md"
    assert templates[0].content == "Project review"


def test_load_prompt_templates_with_diagnostics_reports_overrides(tmp_path: Path) -> None:
    tau_home = tmp_path / "home" / ".tau"
    agents_home = tmp_path / "home" / ".agents"
    cwd = tmp_path / "project"
    (tau_home / "prompts").mkdir(parents=True)
    (tau_home / "prompts" / "review.md").write_text("User Tau review", encoding="utf-8")
    (cwd / ".tau" / "prompts").mkdir(parents=True)
    (cwd / ".tau" / "prompts" / "review.md").write_text(
        "Project Tau review",
        encoding="utf-8",
    )

    templates, diagnostics = load_prompt_templates_with_diagnostics(
        TauResourcePaths(root=tau_home, agents_root=agents_home, cwd=cwd)
    )

    assert [template.name for template in templates] == ["review"]
    assert templates[0].path == cwd / ".tau" / "prompts" / "review.md"
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == "prompt"
    assert diagnostics[0].name == "review"
    assert "overrides lower-precedence resource" in diagnostics[0].message


def test_render_prompt_template_replaces_variables() -> None:
    template = PromptTemplate(
        name="review",
        path=Path("review.md"),
        content="Review {{ topic }} for {{ focus }}.",
    )

    assert render_prompt_template(template, {"topic": "auth", "focus": "security"}) == (
        "Review auth for security."
    )


def test_render_prompt_template_rejects_missing_variables() -> None:
    template = PromptTemplate(name="review", path=Path("review.md"), content="Review {{ topic }}.")

    with pytest.raises(ResourceError, match="Missing prompt template variable"):
        render_prompt_template(template, {})


def test_expand_prompt_template_command_replaces_slash_command() -> None:
    template = PromptTemplate(
        name="example",
        path=Path("example.md"),
        content="Use these arguments: {{ arguments }}",
    )

    assert expand_prompt_template_command("/example src/app.py", [template]) == (
        "Use these arguments: src/app.py"
    )


def test_expand_prompt_template_command_blanks_missing_custom_variables() -> None:
    template = PromptTemplate(
        name="review",
        path=Path("review.md"),
        content="Base branch: {{ base_branch }}\nReview PR {{ arguments }}.",
    )

    assert expand_prompt_template_command("/review 168", [template]) == (
        "Base branch: \nReview PR 168."
    )


def test_expand_prompt_template_command_appends_arguments_without_argument_placeholder() -> None:
    template = PromptTemplate(
        name="review",
        path=Path("review.md"),
        content="Base branch: {{ base_branch }}\nReview this code.",
    )

    assert expand_prompt_template_command("/review src/app.py", [template]) == (
        "Base branch: \nReview this code.\n\nsrc/app.py"
    )


def test_expand_prompt_template_command_appends_arguments_without_placeholder() -> None:
    template = PromptTemplate(name="review", path=Path("review.md"), content="Review this code.")

    assert expand_prompt_template_command("/review src/app.py", [template]) == (
        "Review this code.\n\nsrc/app.py"
    )


def test_expand_prompt_template_command_ignores_unknown_commands() -> None:
    template = PromptTemplate(name="review", path=Path("review.md"), content="Review this code.")

    assert expand_prompt_template_command("/missing src/app.py", [template]) is None
