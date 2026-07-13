"""Layer-boundary regression tests for the tau_agent/tau_ai cycle (issue #317)."""

from __future__ import annotations

from pathlib import Path

import tau_agent
import tau_ai

TAU_AGENT_SRC = Path(tau_agent.__file__).resolve().parent


def test_tau_agent_does_not_import_tau_ai() -> None:
    offenders = [
        str(path)
        for path in sorted(TAU_AGENT_SRC.rglob("*.py"))
        if "from tau_ai" in path.read_text(encoding="utf-8")
        or "import tau_ai" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_tau_ai_reexports_are_the_same_objects() -> None:
    # Duplicated class definitions would silently break the isinstance
    # dispatch in the agent loop; re-exports must share identity.
    assert tau_ai.ModelProvider is tau_agent.ModelProvider
    assert tau_ai.CancellationToken is tau_agent.CancellationToken
    assert tau_ai.ProviderErrorEvent is tau_agent.ProviderErrorEvent
    assert tau_ai.ProviderResponseEndEvent is tau_agent.ProviderResponseEndEvent
    assert tau_ai.ProviderResponseStartEvent is tau_agent.ProviderResponseStartEvent
    assert tau_ai.ProviderRetryEvent is tau_agent.ProviderRetryEvent
    assert tau_ai.ProviderTextDeltaEvent is tau_agent.ProviderTextDeltaEvent
    assert tau_ai.ProviderThinkingDeltaEvent is tau_agent.ProviderThinkingDeltaEvent
    assert tau_ai.ProviderToolCallEvent is tau_agent.ProviderToolCallEvent
