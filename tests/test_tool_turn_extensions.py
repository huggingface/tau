"""Exercise terminating submissions through real extension loading and persistence."""

from pathlib import Path

import pytest

from pi_event_helpers import assistant_done
from tau_agent import AssistantMessage, ToolCall, ToolExecutionEndEvent, ToolResultMessage
from tau_agent.session import JsonlSessionStorage
from tau_ai import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig, TauResourcePaths
from tau_coding.extensions import ExtensionRuntime

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples/extensions/structured_output.py"
INSTALLED_EXAMPLE = ROOT / "src/tau_coding/data/examples/extensions/structured_output.py"
RESULT = {"headline": "Complete", "summary": "All checks passed.", "actionItems": ["Ship it"]}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _config(tmp_path: Path, provider: FakeProvider, *extra_paths: Path) -> CodingSessionConfig:
    cwd = tmp_path / "project"
    cwd.mkdir(exist_ok=True)
    return CodingSessionConfig(
        provider=provider,
        model="fake",
        storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
        cwd=cwd,
        resource_paths=TauResourcePaths(
            root=tmp_path / "home", cwd=cwd, agents_root=tmp_path / "agents"
        ),
        tools=[],
        extensions_enabled=False,
        extension_paths=(EXAMPLE, *extra_paths),
        auto_compact_enabled=False,
    )


def test_structured_output_example_matches_installed_copy() -> None:
    assert EXAMPLE.read_text() == INSTALLED_EXAMPLE.read_text()


async def test_submitted_result_ends_session_run_and_survives_resume(tmp_path: Path) -> None:
    call = ToolCall(id="submit-1", name="structured_output", arguments=RESULT)
    provider = FakeProvider(
        [[assistant_done(AssistantMessage(content=[call], stop_reason="toolUse"))]]
    )
    hook = tmp_path / "redact.py"
    hook.write_text(
        "from tau_coding.extensions import ToolResultHookResult\n"
        "def setup(tau):\n"
        "    tau.on('tool_result', lambda event, context: "
        "ToolResultHookResult(content='Submission accepted'))\n"
    )
    config = _config(tmp_path, provider, hook)
    session = await CodingSession.load(config)
    try:
        events = [event async for event in session.prompt("Submit the final result")]
        assert len(provider.calls) == 1
        execution = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
        assert execution.result.terminate is True
        assert execution.result.text == "Submission accepted"
        assert not execution.is_error
        submitted = session.messages[-1]
        assert isinstance(submitted, ToolResultMessage)
        assert submitted.details == RESULT
        assert submitted.text == "Submission accepted"
        assert "terminate" not in submitted.model_dump()
        assert events[-1].type == "agent_settled"
        assert not session.is_running
    finally:
        await session.aclose()

    restored = await CodingSession.load(config)
    try:
        assert restored.messages[-1] == submitted
        assert len(provider.calls) == 1
    finally:
        await restored.aclose()


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"headline": 42, "summary": "Summary", "actionItems": []},
        {"headline": "Title", "summary": "Summary", "actionItems": "invalid"},
        {"headline": "Title", "summary": "Summary", "actionItems": [42]},
    ],
)
async def test_invalid_submission_does_not_terminate(tmp_path: Path, arguments) -> None:  # noqa: ANN001
    call = ToolCall(id="invalid-1", name="structured_output", arguments=arguments)
    provider = FakeProvider(
        [
            [assistant_done(AssistantMessage(content=[call], stop_reason="toolUse"))],
            [assistant_done(AssistantMessage(content="I will correct the submission."))],
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider))
    try:
        events = [event async for event in session.prompt("Submit results")]
        execution = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
        assert execution.is_error
        assert execution.result.terminate is not True
        assert len(provider.calls) == 2
    finally:
        await session.aclose()


async def test_installed_example_loads_through_real_runtime(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    runtime.load(
        TauResourcePaths(root=tmp_path / "home", agents_root=tmp_path / "agents"),
        extra_paths=(INSTALLED_EXAMPLE,),
        include_resource_dirs=False,
    )
    assert not [diagnostic for diagnostic in runtime.diagnostics if diagnostic.severity == "error"]
    tool = next(tool for tool in runtime.compose_tools([]) if tool.name == "structured_output")
    result = await tool.execute("submit-1", RESULT)
    assert result.details == RESULT
    assert result.terminate is True
