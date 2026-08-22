"""Tau extension that adds a run-scoped system-prompt instruction.

Install by copying into `~/.tau/extensions/`, or run:

    tau -e examples/extensions/prompt_customizer.py
"""

from typing import cast

from tau_coding.extensions import (
    BeforeAgentStartEvent,
    BeforeAgentStartHookResult,
    ExtensionAPI,
    ExtensionContext,
    ExtensionHandler,
)


def _customize_prompt(
    event: BeforeAgentStartEvent,
    context: ExtensionContext,
) -> BeforeAgentStartHookResult:
    del context
    tools = ", ".join(event.system_prompt_inputs.tools) or "none"
    return BeforeAgentStartHookResult(
        system_prompt=f"{event.system_prompt}\n\nActive tools for this run: {tools}."
    )


def setup(tau: ExtensionAPI) -> None:
    """Customize each agent run without changing the saved base prompt."""
    tau.on("before_agent_start", cast(ExtensionHandler, _customize_prompt))
