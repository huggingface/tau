"""Minimal Tau extension that registers a custom tool."""

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


async def _run_hello(
    tool_call_id: str,
    arguments: Mapping[str, JSONValue],
    signal: ToolCancellationToken | None = None,
    on_update: ToolUpdateCallback | None = None,
) -> AgentToolResult:
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
                "properties": {"who": {"type": "string"}},
            },
            execute_fn=_run_hello,
            prompt_snippet="Greet someone by name.",
        )
    )
