"""Minimal Tau extension: one custom tool.

Install by copying into `~/.tau/extensions/`, or run:

    tau -x examples/extensions/hello_tool.py
"""

from tau_agent.tools import AgentTool, AgentToolResult
from tau_coding.extensions import ExtensionAPI


async def _run_hello(arguments, signal=None):  # noqa: ANN001, ANN202
    who = str(arguments.get("who", "world"))
    return AgentToolResult(
        tool_call_id="",
        name="hello",
        ok=True,
        content=f"Hello, {who}!",
    )


def setup(tau: ExtensionAPI) -> None:
    """Register the hello tool."""
    tau.register_tool(
        AgentTool(
            name="hello",
            description="Greet someone by name.",
            input_schema={
                "type": "object",
                "properties": {
                    "who": {"type": "string", "description": "Who to greet."},
                },
            },
            executor=_run_hello,
            prompt_snippet="Greet someone by name.",
        )
    )
