"""Tau coding-agent application package."""

from tau_coding.prompt_templates import (
    PromptTemplate,
    load_prompt_templates,
    render_prompt_template,
)
from tau_coding.rendering import (
    EventRenderer,
    FinalTextRenderer,
    JsonEventRenderer,
    PrintOutputMode,
    TranscriptRenderer,
    create_event_renderer,
)
from tau_coding.resources import ResourceError, TauResourcePaths
from tau_coding.session import (
    CodingSession,
    CodingSessionConfig,
    CommandResult,
    jsonl_session_storage,
)
from tau_coding.skills import Skill, build_skill_index, expand_skill_command, load_skills
from tau_coding.system_prompt import (
    BuildSystemPromptOptions,
    ProjectContextFile,
    build_system_prompt,
    collect_prompt_guidelines,
    format_available_tools,
    format_guidelines,
    format_project_context,
    format_skills_for_prompt,
)
from tau_coding.tools import (
    ToolDefinition,
    create_bash_tool,
    create_bash_tool_definition,
    create_coding_tools,
    create_edit_tool,
    create_edit_tool_definition,
    create_read_tool,
    create_read_tool_definition,
    create_write_tool,
    create_write_tool_definition,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "CodingSession",
    "CodingSessionConfig",
    "CommandResult",
    "BuildSystemPromptOptions",
    "EventRenderer",
    "FinalTextRenderer",
    "JsonEventRenderer",
    "PrintOutputMode",
    "ProjectContextFile",
    "PromptTemplate",
    "ResourceError",
    "Skill",
    "TauResourcePaths",
    "ToolDefinition",
    "TranscriptRenderer",
    "build_skill_index",
    "build_system_prompt",
    "collect_prompt_guidelines",
    "create_bash_tool",
    "create_bash_tool_definition",
    "create_coding_tools",
    "create_edit_tool",
    "create_edit_tool_definition",
    "create_event_renderer",
    "create_read_tool",
    "create_read_tool_definition",
    "create_write_tool",
    "create_write_tool_definition",
    "expand_skill_command",
    "format_available_tools",
    "format_guidelines",
    "format_project_context",
    "format_skills_for_prompt",
    "jsonl_session_storage",
    "load_prompt_templates",
    "load_skills",
    "render_prompt_template",
]
