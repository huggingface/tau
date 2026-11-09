"""Submit a final structured answer without an automatic follow-up model call.

Run: tau -e examples/extensions/structured_output.py
Then ask: "Summarize this repository and submit a structured output."
The result lives in the tool message's details; this example writes no external file.
"""

from collections.abc import Mapping

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue
from tau_coding.extensions import ExtensionAPI


async def _submit(
    tool_call_id: str,
    arguments: Mapping[str, JSONValue],
    signal: ToolCancellationToken | None = None,
    on_update: ToolUpdateCallback | None = None,
) -> AgentToolResult:
    del tool_call_id, signal, on_update
    # Tool parameters describe the model-facing schema. Validate inputs before
    # accepting a submission: failures must remain non-terminating tool errors.
    headline = arguments.get("headline")
    summary = arguments.get("summary")
    action_items = arguments.get("actionItems")
    if not isinstance(headline, str) or not isinstance(summary, str):
        raise ValueError("headline and summary must be strings")
    if not isinstance(action_items, list) or not all(
        isinstance(item, str) for item in action_items
    ):
        raise ValueError("actionItems must be a list of strings")
    return AgentToolResult(
        content=[
            TextContent(
                text=f"{headline}\n\n{summary}" + "".join(f"\n- {item}" for item in action_items)
            )
        ],
        details={"headline": headline, "summary": summary, "actionItems": action_items},
        terminate=True,
    )


def setup(tau: ExtensionAPI) -> None:
    """Register a final-answer tool; the name itself has no special meaning."""
    tau.register_tool(
        AgentTool(
            name="structured_output",
            label="Structured output",
            description="Submit a final structured answer. Call this alone as your last action.",
            parameters={
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "actionItems": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["headline", "summary", "actionItems"],
            },
            execute_fn=_submit,
            prompt_snippet="Submit a final structured answer.",
            prompt_guidelines=(
                "For structured output, call structured_output alone as your final action.",
            ),
        )
    )
