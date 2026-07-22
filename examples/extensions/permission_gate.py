"""Tau extension that blocks dangerous bash commands before they run.

Demonstrates the `tool_call` hook: return a blocking result and the tool
never executes; the model sees the block reason instead.

Install by copying into `~/.tau/extensions/`, or run:

    tau -e examples/extensions/permission_gate.py
"""

import re

from tau_coding.extensions import ExtensionAPI, ToolCallHookEvent, ToolCallHookResult

DANGEROUS_PATTERNS = (
    # rm with a flag cluster containing both r and f, in either order
    re.compile(r"\brm\s+-(?=[a-zA-Z]*r)(?=[a-zA-Z]*f)[a-zA-Z]+"),
    re.compile(r"\bgit\s+push\s+--force"),
    re.compile(r"\bgit\s+reset\s+--hard"),
    re.compile(r"\bchmod\s+-R\s+777\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r"\bmkfs\b"),
)


def _gate_tool_call(event: ToolCallHookEvent, context) -> ToolCallHookResult | None:  # noqa: ANN001
    del context
    if event.tool_name != "bash":
        return None
    command = str(event.arguments.get("command", ""))
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return ToolCallHookResult(
                block=True,
                reason=(
                    f"command matches guarded pattern `{pattern.pattern}`; "
                    "ask the user to run it manually if it is intended"
                ),
            )
    return None


def setup(tau: ExtensionAPI) -> None:
    """Subscribe the bash guard."""
    tau.on("tool_call", _gate_tool_call)
