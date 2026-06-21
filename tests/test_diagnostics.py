import json
from pathlib import Path

from tau_ai import LLMObservation
from tau_coding.diagnostics import (
    LLM_OBSERVABILITY_ENV,
    LLMObservationLogger,
    llm_observer_from_env,
)
from tau_coding.paths import TauPaths


def test_llm_observer_from_env_is_disabled_by_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(LLM_OBSERVABILITY_ENV, raising=False)

    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    assert llm_observer_from_env(paths) is None


def test_llm_observer_from_env_writes_jsonl_when_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(LLM_OBSERVABILITY_ENV, "1")
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    observer = llm_observer_from_env(paths)

    assert isinstance(observer, LLMObservationLogger)
    observer.record(
        LLMObservation(
            kind="request",
            provider="openai-compatible",
            model="test-model",
            method="POST",
            url="https://example.test/v1/chat/completions",
            attempt=1,
            stream=True,
            data={"request": {"headers": {"Authorization": "[REDACTED]"}}},
        )
    )

    lines = paths.llm_observations_log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "request"
    assert entry["provider"] == "openai-compatible"
    assert entry["model"] == "test-model"
    assert entry["data"]["request"]["headers"]["Authorization"] == "[REDACTED]"
