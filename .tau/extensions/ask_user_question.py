"""Let the agent ask the user a multiple-choice question.

Install by copying into `~/.tau/extensions/`, or run:

    tau -e examples/extensions/ask_user_question.py
"""

from collections.abc import Mapping

from tau_agent.messages import TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue
from tau_coding.extensions import ExtensionAPI


def _result(text: str, *, selected: str | None = None) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"selected": selected},
    )


def setup(tau: ExtensionAPI) -> None:
    """Register the ask_user_question tool."""

    async def ask_user_question(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip():
            return _result("The question must be a non-empty string.")

        raw_options = arguments.get("options")
        if not isinstance(raw_options, list) or len(raw_options) < 2:
            return _result("At least two options are required.")

        options: list[str] = []
        for option in raw_options:
            if not isinstance(option, str) or not option.strip():
                return _result("Every option must be a non-empty string.")
            options.append(option.strip())
        if len(set(options)) != len(options):
            return _result("Options must be unique.")

        selected = await tau.context.ui.select(question.strip(), options)
        if selected is None:
            return _result("The user did not select an option.")
        return _result(f"The user selected: {selected}", selected=selected)

    tau.register_tool(
        AgentTool(
            name="ask_user_question",
            label="ask user question",
            description=(
                "Ask the user one multiple-choice question and wait for their selection. "
                "Use this when user input is needed before proceeding."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "description": "The question to show the user.",
                    },
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                        "description": "The available answers.",
                    },
                },
                "required": ["question", "options"],
                "additionalProperties": False,
            },
            execute_fn=ask_user_question,
            execution_mode="sequential",
            prompt_snippet="Ask the user a multiple-choice question when their input is needed.",
            prompt_guidelines=(
                "When ask_user_question reports no selection, do not assume an answer; "
                "ask how the user wants to proceed.",
            ),
        )
    )
