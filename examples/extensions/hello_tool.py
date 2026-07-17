"""Minimal Tau extension: one custom tool.

Install by copying into `~/.tau/extensions/`, or run:

    tau -x examples/extensions/hello_tool.py
"""

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult
from tau_coding.extensions import ExtensionAPI


async def _run_hello(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
    del tool_call_id, signal, on_update
    who = str(arguments.get("who", "world"))
    return AgentToolResult(content=[TextContent(text=f"Hello, {who}!")])


def setup(tau: ExtensionAPI) -> None:
    """Register the hello tool."""
    tau.register_tool(
        AgentTool(
            name="hello",
            label="hello",
            description="Greet someone by name.",
            parameters={
                "type": "object",
                "properties": {
                    "who": {"type": "string", "description": "Who to greet."},
                },
            },
            execute_fn=_run_hello,
            prompt_snippet="Greet someone by name.",
        )
    )
