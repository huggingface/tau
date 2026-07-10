"""Provider protocol, re-exported from the agent core.

The canonical definitions live in ``tau_agent.provider`` so the portable
agent core never imports from the provider layer. This module re-exports
them unchanged for backwards compatibility.
"""

from __future__ import annotations

from tau_agent.provider import CancellationToken, ModelProvider

__all__ = [
    "CancellationToken",
    "ModelProvider",
]
