import asyncio
import json
import sys
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from conftest import isolate_home
from pi_event_helpers import assistant_done, assistant_error, assistant_start
from tau_agent import (
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    ImageContent,
    MessageEndEvent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from tau_agent.messages import AssistantMessageDiagnostic, assistant_content
from tau_agent.provider_events import AssistantErrorEvent
from tau_agent.session import (
    CompactionEntry,
    CustomEntry,
    JsonlSessionStorage,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionEntry,
    SessionInfoEntry,
    ThinkingLevelChangeEntry,
)
from tau_ai import CancellationToken, FakeProvider, ModelProvider, RuntimeModelLimits
from tau_ai.events import AssistantMessageEvent
from tau_coding import (
    CodingSession,
    CodingSessionConfig,
    FileCredentialStore,
    ModelChoice,
    OpenAICodexProviderConfig,
    OpenAICompatibleProviderConfig,
    ProviderConfigError,
    ProviderSettings,
    ResourceError,
    ScopedModelConfig,
    SessionManager,
    SessionTreeBranchResult,
    TauPaths,
    TauResourcePaths,
    load_provider_settings,
    save_provider_settings,
)
from tau_coding import session as coding_session_module
from tau_coding.events import AgentSettledEvent, QueueUpdateEvent
from tau_coding.extensions.runtime import InputHookOutcome
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.provider_config import ProviderModelMetadata
from tau_coding.session import _ordered_tree_entries, parse_terminal_command


async def _collect_session_events(session_stream: object) -> list[object]:
    return [event async for event in session_stream]  # type: ignore[attr-defined]


def _record_extension_events(
    session: CodingSession,
    monkeypatch: pytest.MonkeyPatch,
) -> list[object]:
    events: list[object] = []
    emit_event = session.extension_runtime.emit_event

    async def record(event: object) -> None:
        events.append(event)
        await emit_event(event)

    monkeypatch.setattr(session.extension_runtime, "emit_event", record)
    return events


def _assert_messages(actual: object, expected: object) -> None:
    def dump(message: object) -> object:
        model_dump = getattr(message, "model_dump", None)
        if callable(model_dump):
            return model_dump(exclude={"timestamp"})
        return message

    assert [dump(message) for message in actual] == [dump(message) for message in expected]  # type: ignore[union-attr]


def _config(
    tmp_path: Path, provider: ModelProvider, storage: JsonlSessionStorage
) -> CodingSessionConfig:
    return CodingSessionConfig(
        provider=provider,
        model="fake",
        system="You are Tau.",
        storage=storage,
        cwd=tmp_path,
    )


class SwitchableFakeProvider:
    def __init__(self, config: object) -> None:
        self.config = config
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class ModelLimitsFakeProvider(FakeProvider):
    def __init__(
        self,
        scripts: list[list[AssistantMessageEvent]],
        *,
        limits: RuntimeModelLimits | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__(scripts)
        self.limits = limits
        self.error = error
        self.discovery_calls: list[str] = []

    async def discover_model_limits(self, model: str) -> RuntimeModelLimits | None:
        self.discovery_calls.append(model)
        if self.error is not None:
            raise self.error
        return self.limits


class RaisingProvider:
    def __init__(self, fail_on_call: int = 1) -> None:
        self.fail_on_call = fail_on_call
        self.call_count = 0

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, messages, tools, signal, session_id
        self.call_count += 1
        should_fail = self.call_count == self.fail_on_call

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            if should_fail:
                raise RuntimeError("provider exploded")
            yield assistant_start(model="fake")
            yield assistant_done(message=AssistantMessage(content="Generated title"))

        return iterator()


class WaitingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[list[AgentMessage]] = []
        self.call_count = 0

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, tools, signal, session_id
        call_index = self.call_count
        self.call_count += 1
        self.calls.append(list(messages))

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            if call_index == 0:
                yield assistant_start(model="fake")
                self.started.set()
                await self.release.wait()
                yield assistant_done(message=AssistantMessage(content="First"))
                return
            yield assistant_start(model="fake")
            yield assistant_done(message=AssistantMessage(content="Second"))

        return iterator()


class CancellableWaitingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[list[AgentMessage]] = []

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, tools, session_id
        self.calls.append(list(messages))

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            yield assistant_start(model="fake")
            self.started.set()
            while not self.release.is_set():
                if signal is not None and signal.is_cancelled():
                    return
                await asyncio.sleep(0)
            yield assistant_done(message=AssistantMessage(content="Finished"))

        return iterator()


@pytest.mark.anyio
async def test_load_empty_session_defers_transcript_file(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")

    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    entries = await storage.read_all()
    assert entries == []
    assert not storage.path.exists()
    assert session.messages == ()
    assert session.state.model == "fake"
    assert session.thinking_level == "medium"
    assert session.available_thinking_levels == ("off", "minimal", "low", "medium", "high", "xhigh")
    assert session.cwd == tmp_path
    assert session.model == "fake"
    assert [tool.name for tool in session.tools] == ["read", "write", "edit", "bash"]


@pytest.mark.anyio
async def test_session_export_defaults_to_cwd(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / ".tau" / "sessions" / "session-1.jsonl")
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))
    await storage.append(MessageEntry(id="root", message=UserMessage(content="Export me")))

    output_path = await session.export()

    assert output_path == tmp_path / "session-1.html"
    html = output_path.read_text(encoding="utf-8")
    assert "Export me" in html
    assert str(storage.path) in html
    assert '<details class="system-prompt">' in html
    assert "You are Tau." in html


@pytest.mark.anyio
async def test_session_export_writes_jsonl_to_destination_directory(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / ".tau" / "sessions" / "session-1.jsonl")
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))
    await storage.append(MessageEntry(id="root", message=UserMessage(content="Export me")))

    output_path = await session.export(Path("exports"), format="jsonl")

    assert output_path == tmp_path / "exports" / "session-1.jsonl"
    jsonl = output_path.read_text(encoding="utf-8")
    assert "Export me" in jsonl
    assert "You are Tau." not in jsonl
    assert "system_prompt" not in jsonl


@pytest.mark.anyio
async def test_prompt_logs_unexpected_agent_call_exception(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    tau_paths = TauPaths(home=tmp_path / "tau-home", agents_home=tmp_path / "agents-home")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=RaisingProvider(),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            provider_name="fake-provider",
            session_id="session-1",
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    with pytest.raises(RuntimeError, match="provider exploded"):
        await _collect_session_events(session.prompt("Hello"))

    log_path = tau_paths.agent_calls_log_path
    assert session.last_diagnostic_log_path == log_path
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["kind"] == "exception"
    assert entry["phase"] == "agent_loop"
    assert entry["provider_name"] == "fake-provider"
    assert entry["model"] == "fake"
    assert entry["session_id"] == "session-1"
    assert entry["cwd"] == str(tmp_path)
    assert entry["exception"]["type"] == "RuntimeError"
    assert entry["exception"]["message"] == "provider exploded"
    assert "provider exploded" in entry["exception"]["traceback"]
    assert "Hello" not in log_path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_prompt_logs_error_event_diagnostic_data(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    tau_paths = TauPaths(home=tmp_path / "tau-home", agents_home=tmp_path / "agents-home")
    provider = FakeProvider(
        [
            [
                assistant_error(
                    message="provider failed",
                    data={"status_code": 400, "body": "bad request"},
                )
            ]
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            provider_name="fake-provider",
            session_id="session-1",
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    await _collect_session_events(session.prompt("Hello"))

    assert provider.session_ids == ["session-1"]
    log_path = tau_paths.agent_calls_log_path
    assert session.last_diagnostic_log_path == log_path
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["kind"] == "assistant_error"
    assert entry["error"] == {
        "message": "provider failed",
        "stop_reason": "error",
    }
    assert "Hello" not in log_path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_prompt_logs_safe_provider_stream_error_details(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    tau_paths = TauPaths(home=tmp_path / "tau-home", agents_home=tmp_path / "agents-home")
    error = AssistantMessage(
        stop_reason="error",
        error_message="Our servers are currently overloaded. Please try again later.",
        diagnostics=[
            AssistantMessageDiagnostic(
                type="provider_error",
                details={
                    "event": {
                        "type": "error",
                        "error": {
                            "type": "service_unavailable_error",
                            "code": "server_is_overloaded",
                            "message": "Our servers are currently overloaded. "
                            "Please try again later.",
                            "param": None,
                        },
                        "sequence_number": 2,
                    }
                },
            )
        ],
    )
    provider = FakeProvider([[AssistantErrorEvent(reason="error", error=error)]])
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            provider_name="openai-codex",
            session_id="session-1",
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    await _collect_session_events(session.prompt("Hello"))

    log_path = tau_paths.agent_calls_log_path
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["kind"] == "assistant_error"
    assert entry["error"] == {
        "message": "Our servers are currently overloaded. Please try again later.",
        "stop_reason": "error",
        "provider": {
            "event": {
                "type": "error",
                "sequence_number": 2,
                "error": {
                    "type": "service_unavailable_error",
                    "code": "server_is_overloaded",
                    "message": "Our servers are currently overloaded. Please try again later.",
                },
            }
        },
    }
    assert "Hello" not in log_path.read_text(encoding="utf-8")


class _CountingStorage:
    def __init__(self) -> None:
        self.entries: list[SessionEntry] = []
        self.reads = 0

    async def append(self, entry: SessionEntry) -> None:
        self.entries.append(entry)

    async def read_all(self) -> list[SessionEntry]:
        self.reads += 1
        return list(self.entries)


class _FaultInjectingStorage:
    def __init__(self, phase: str) -> None:
        self.entries: list[SessionEntry] = []
        self.phase = phase
        self.failed = False
        self.failures_remaining = 2 if phase == "message_twice" else 1

    async def append(self, entry: SessionEntry) -> None:
        target = (
            self.phase.startswith("message")
            and isinstance(entry, MessageEntry)
            or self.phase.startswith("leaf")
            and isinstance(entry, LeafEntry)
        )
        if target and (self.failures_remaining > 0 or self.phase == "message_always"):
            self.failed = True
            self.failures_remaining -= 1
            if self.phase.endswith("after"):
                self.entries.append(entry)
            raise OSError(f"simulated {self.phase} failure")
        self.entries.append(entry)

    async def read_all(self) -> list[SessionEntry]:
        if (
            self.phase == "refresh"
            and not self.failed
            and any(isinstance(entry, LeafEntry) for entry in self.entries)
        ):
            self.failed = True
            raise OSError("simulated refresh failure")
        return list(self.entries)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "phase",
    ["message_before", "message_after", "leaf_before", "leaf_after", "refresh"],
)
async def test_message_persistence_retry_is_idempotent(tmp_path: Path, phase: str) -> None:
    storage = _FaultInjectingStorage(phase)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )

    with pytest.raises(OSError, match="simulated"):
        await _collect_session_events(session.prompt("go"))

    messages = [entry for entry in storage.entries if isinstance(entry, MessageEntry)]
    assert len(messages) == 1
    assert messages[0].message.text == "go"
    leaves = [
        entry
        for entry in storage.entries
        if isinstance(entry, LeafEntry) and entry.entry_id == messages[0].id
    ]
    assert len(leaves) == 1
    restored = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )
    _assert_messages(restored.messages, [UserMessage(content="go")])


@pytest.mark.anyio
async def test_next_prompt_flushes_a_repair_that_failed_twice(tmp_path: Path) -> None:
    storage = _FaultInjectingStorage("message_twice")
    provider = FakeProvider(
        [[assistant_start(), assistant_done(AssistantMessage(content="Recovered."))]]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )

    with pytest.raises(OSError, match="simulated message_twice failure"):
        await _collect_session_events(session.prompt("go"))
    await _collect_session_events(session.prompt("continue"))

    persisted_texts = [
        entry.message.text for entry in storage.entries if isinstance(entry, MessageEntry)
    ]
    assert persisted_texts == ["go", "continue", "Recovered."]
    _model, _system, sent, _tools = provider.calls[-1]
    assert [message.text for message in sent] == ["go", "continue"]


@pytest.mark.anyio
async def test_clean_persist_reads_storage_once_per_message(tmp_path: Path) -> None:
    storage = _CountingStorage()
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider(
                [[assistant_start(), assistant_done(AssistantMessage(content="Hi."))]]
            ),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )

    reads_before = storage.reads
    await _collect_session_events(session.prompt("go"))

    persisted = [entry for entry in storage.entries if isinstance(entry, MessageEntry)]
    assert len(persisted) == 2
    # One deferred-metadata read plus one state refresh per message. A first
    # attempt must not also read to check ids it just minted.
    assert storage.reads - reads_before == len(persisted) + 1


@pytest.mark.anyio
async def test_reconcile_persistence_failure_is_logged(tmp_path: Path) -> None:
    storage = _FaultInjectingStorage("message_always")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )

    with pytest.raises(OSError, match="simulated message_always failure"):
        await _collect_session_events(session.prompt("go"))

    assert session.last_diagnostic_log_path is not None
    diagnostics = [
        json.loads(line)
        for line in session.last_diagnostic_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert diagnostics[-1]["phase"] == "session_persistence_reconcile"
    assert diagnostics[-1]["exception"]["message"] == "simulated message_always failure"


def _hang_tool(tool_started: asyncio.Event, release: asyncio.Event) -> AgentTool:
    async def hang(
        tool_call_id: object,
        arguments: object,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        tool_started.set()
        await release.wait()
        return AgentToolResult(content=[TextContent(text="done")])

    return AgentTool(
        name="hang",
        label="Hang",
        description="Block until released.",
        parameters={"type": "object"},
        execute_fn=hang,
    )


@pytest.mark.anyio
async def test_cancelled_prompt_teardown_persists_interrupted_tool_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Regression: Esc mid-tool-call cancels the consumer, and the synthetic
    # interrupted tool result never reached the session file, leaving a
    # dangling tool_use that providers reject with a 400 on later replays.
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    tool_started = asyncio.Event()
    release = asyncio.Event()
    tool_call = ToolCall(id="call-1", name="hang", arguments={})
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(
                    message=AssistantMessage(
                        content=assistant_content("Running.", [tool_call]),
                        stop_reason="toolUse",
                    )
                ),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Recovered.")),
            ],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            tools=[_hang_tool(tool_started, release)],
        )
    )
    extension_events = _record_extension_events(session, monkeypatch)

    async def consume() -> None:
        async for _event in session.prompt("go"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(tool_started.wait(), timeout=5)
    # Mirror the TUI interrupt: cancel the session, then the worker, with no
    # await in between.
    session.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = [event for event in extension_events if isinstance(event, AgentSettledEvent)]
    interrupted_result_index = next(
        index
        for index, event in enumerate(extension_events)
        if isinstance(event, MessageEndEvent)
        and isinstance(event.message, ToolResultMessage)
        and event.message.is_error
    )
    assert len(settled) == 1
    assert interrupted_result_index < extension_events.index(settled[0])

    expected_repair = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="hang",
        content=[TextContent(text="Tool call interrupted by user")],
        is_error=True,
    )
    entries = await storage.read_all()
    message_entries = [entry for entry in entries if entry.type == "message"]
    by_id = {entry.id: entry for entry in message_entries}
    repairs = [entry for entry in message_entries if isinstance(entry.message, ToolResultMessage)]
    assert len(repairs) == 1
    _assert_messages([repairs[0].message], [expected_repair])
    parent = by_id[repairs[0].parent_id]
    assert isinstance(parent.message, AssistantMessage)
    assert parent.message.tool_calls

    _ = await _collect_session_events(session.prompt("continue"))

    _model, _system, sent, _tools = provider.calls[-1]
    call_index = next(
        index
        for index, message in enumerate(sent)
        if isinstance(message, AssistantMessage) and message.tool_calls
    )
    _assert_messages([sent[call_index + 1]], [expected_repair])
    assert sum(isinstance(message, ToolResultMessage) for message in sent) == 1


@pytest.mark.anyio
async def test_completed_prompt_dispatches_and_yields_same_agent_settled_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done.")),
            ]
        ]
    )
    session = await CodingSession.load(
        _config(tmp_path, provider, JsonlSessionStorage(tmp_path / "session.jsonl"))
    )
    extension_events = _record_extension_events(session, monkeypatch)

    stream_events = await _collect_session_events(session.prompt("go"))

    extension_settled = [
        event for event in extension_events if isinstance(event, AgentSettledEvent)
    ]
    stream_settled = [event for event in stream_events if isinstance(event, AgentSettledEvent)]
    assert len(extension_settled) == 1
    assert len(stream_settled) == 1
    assert extension_settled[0] is stream_settled[0]
    assert isinstance(extension_events[-1], AgentSettledEvent)
    assert isinstance(stream_events[-1], AgentSettledEvent)


@pytest.mark.anyio
async def test_cancellation_during_settled_dispatch_does_not_redispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done.")),
            ]
        ]
    )
    session = await CodingSession.load(
        _config(tmp_path, provider, JsonlSessionStorage(tmp_path / "session.jsonl"))
    )
    extension_events: list[object] = []
    emit_event = session.extension_runtime.emit_event

    async def cancel_during_settled(event: object) -> None:
        extension_events.append(event)
        if isinstance(event, AgentSettledEvent):
            raise asyncio.CancelledError
        await emit_event(event)

    monkeypatch.setattr(session.extension_runtime, "emit_event", cancel_during_settled)

    with pytest.raises(asyncio.CancelledError):
        await _collect_session_events(session.prompt("go"))

    assert sum(isinstance(event, AgentSettledEvent) for event in extension_events) == 1
    assert session.is_running is False


@pytest.mark.anyio
async def test_cancelled_continue_dispatches_agent_settled_after_teardown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = CancellableWaitingProvider()
    session = await CodingSession.load(
        _config(tmp_path, provider, JsonlSessionStorage(tmp_path / "session.jsonl"))
    )
    extension_events: list[object] = []
    running_when_settled: list[bool] = []
    emit_event = session.extension_runtime.emit_event

    async def record_extension_event(event: object) -> None:
        extension_events.append(event)
        if isinstance(event, AgentSettledEvent):
            running_when_settled.append(session.is_running)
        await emit_event(event)

    monkeypatch.setattr(session.extension_runtime, "emit_event", record_extension_event)

    async def consume() -> None:
        async for _event in session.continue_():
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(provider.started.wait(), timeout=5)
    session.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sum(isinstance(event, AgentSettledEvent) for event in extension_events) == 1
    assert isinstance(extension_events[-1], AgentSettledEvent)
    assert running_when_settled == [False]


@pytest.mark.anyio
async def test_handled_input_does_not_dispatch_agent_settled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = await CodingSession.load(
        _config(tmp_path, FakeProvider([]), JsonlSessionStorage(tmp_path / "session.jsonl"))
    )
    extension_events = _record_extension_events(session, monkeypatch)

    async def handle_input(*args: object, **kwargs: object) -> InputHookOutcome:
        del args, kwargs
        return InputHookOutcome(handled=True, text="handled")

    monkeypatch.setattr(session.extension_runtime, "run_input_hooks", handle_input)

    stream_events = await _collect_session_events(session.prompt("do not run"))

    assert stream_events == []
    assert not any(isinstance(event, AgentSettledEvent) for event in extension_events)
    assert session.is_running is False


@pytest.mark.anyio
async def test_load_persists_repair_for_session_with_interrupted_tail_tool_call(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    user_entry = MessageEntry(message=UserMessage(content="Read README.md"))
    await storage.append(user_entry)
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    assistant_entry = MessageEntry(
        parent_id=user_entry.id,
        message=AssistantMessage(content=assistant_content("I'll read it.", [tool_call])),
    )
    await storage.append(assistant_entry)
    await storage.append(LeafEntry(parent_id=assistant_entry.id, entry_id=assistant_entry.id))

    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Recovered.")),
            ]
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )

    expected_repair = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=[TextContent(text="Tool call interrupted by user")],
        is_error=True,
    )
    assert provider.calls == []
    _assert_messages(
        session.messages,
        (
            UserMessage(content="Read README.md"),
            AssistantMessage(content=assistant_content("I'll read it.", [tool_call])),
            expected_repair,
        ),
    )

    entries = await storage.read_all()
    message_entries = [entry for entry in entries if entry.type == "message"]
    _assert_messages(
        [entry.message for entry in message_entries],
        [
            UserMessage(content="Read README.md"),
            AssistantMessage(content=assistant_content("I'll read it.", [tool_call])),
            expected_repair,
        ],
    )


@pytest.mark.anyio
async def test_load_persists_branch_without_orphaned_tool_result(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    user_entry = MessageEntry(message=UserMessage(content="what remains?"))
    await storage.append(user_entry)
    orphan_entry = MessageEntry(
        parent_id=user_entry.id,
        message=ToolResultMessage(
            tool_call_id="call-missing",
            tool_name="bash",
            content="Tool call interrupted by user",
            is_error=True,
        ),
    )
    await storage.append(orphan_entry)
    custom_entry = CustomEntry(
        parent_id=orphan_entry.id,
        namespace="example.state",
        data={"kept": True},
    )
    await storage.append(custom_entry)
    model_entry = ModelChangeEntry(
        parent_id=custom_entry.id,
        model="recovered-model",
    )
    await storage.append(model_entry)
    continued_entry = MessageEntry(
        parent_id=model_entry.id,
        message=UserMessage(content="continue"),
    )
    await storage.append(continued_entry)
    await storage.append(LeafEntry(parent_id=continued_entry.id, entry_id=continued_entry.id))

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )

    _assert_messages(
        session.messages,
        [UserMessage(content="what remains?"), UserMessage(content="continue")],
    )
    assert session.state.model == "recovered-model"
    assert any(
        entry.namespace == "example.state" and entry.data == {"kept": True}
        for entry in session.state.custom_entries
    )
    diagnostics = [
        entry
        for entry in (await storage.read_all())
        if isinstance(entry, CustomEntry) and entry.namespace == "tau.session-history-repair"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].data == {
        "version": 1,
        "synthesizedResults": 0,
        "droppedOrphanResults": 1,
        "droppedDuplicateResults": 0,
        "reorderedResults": 0,
    }

    restored = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )
    _assert_messages(restored.messages, session.messages)
    diagnostics = [
        entry
        for entry in (await storage.read_all())
        if isinstance(entry, CustomEntry) and entry.namespace == "tau.session-history-repair"
    ]
    assert len(diagnostics) == 1


@pytest.mark.anyio
async def test_load_persists_repair_for_historical_interrupted_tool_call(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    user_entry = MessageEntry(message=UserMessage(content="Read README.md"))
    await storage.append(user_entry)
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    assistant_entry = MessageEntry(
        parent_id=user_entry.id,
        message=AssistantMessage(content=assistant_content("I'll read it.", [tool_call])),
    )
    await storage.append(assistant_entry)
    continued_entry = MessageEntry(
        parent_id=assistant_entry.id,
        message=UserMessage(content="continue"),
    )
    await storage.append(continued_entry)
    await storage.append(LeafEntry(parent_id=continued_entry.id, entry_id=continued_entry.id))

    provider = FakeProvider([])
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )

    expected_repair = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=[TextContent(text="Tool call interrupted by user")],
        is_error=True,
    )
    assert provider.calls == []
    _assert_messages(
        session.messages,
        (
            UserMessage(content="Read README.md"),
            AssistantMessage(content=assistant_content("I'll read it.", [tool_call])),
            expected_repair,
            UserMessage(content="continue"),
        ),
    )

    entries = await storage.read_all()
    message_entries = [entry for entry in entries if entry.type == "message"]
    _assert_messages(
        [entry.message for entry in message_entries[-2:]],
        [
            expected_repair,
            UserMessage(content="continue"),
        ],
    )

    restored = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )
    assert restored.messages == session.messages


@pytest.mark.anyio
async def test_prompt_persists_user_assistant_and_leaf_entries(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Hi")),
            ]
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    _events = await _collect_session_events(session.prompt("Hello"))

    entries = await storage.read_all()
    assert storage.path.exists()
    assert isinstance(entries[0], SessionInfoEntry)
    assert entries[0].cwd == str(tmp_path)
    assert entries[1] == ModelChangeEntry(
        id=entries[1].id,
        parent_id=entries[0].id,
        model="fake",
        provider="openai",
        timestamp=entries[1].timestamp,
    )
    assert entries[2] == ThinkingLevelChangeEntry(
        id=entries[2].id,
        parent_id=entries[1].id,
        thinking_level="medium",
        timestamp=entries[2].timestamp,
    )
    message_entries = [entry for entry in entries if entry.type == "message"]
    leaf_entries = [entry for entry in entries if entry.type == "leaf"]
    _assert_messages(
        [entry.message for entry in message_entries],
        [
            UserMessage(content="Hello"),
            AssistantMessage(content="Hi"),
        ],
    )
    assert [entry.entry_id for entry in leaf_entries] == [entry.id for entry in message_entries]
    assert entries[-1].type == "leaf"
    assert entries[-1].entry_id == message_entries[-1].id
    _assert_messages(
        session.messages, (UserMessage(content="Hello"), AssistantMessage(content="Hi"))
    )


@pytest.mark.anyio
async def test_terminal_command_can_persist_output_to_context(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    result = await session.run_terminal_command("printf hello", add_to_context=True)

    assert result.output == "hello"
    assert result.added_to_context is True
    entries = await storage.read_all()
    messages = [entry.message for entry in entries if isinstance(entry, MessageEntry)]
    assert len(messages) == 1
    assert isinstance(messages[0], UserMessage)
    assert "Terminal command executed by the user." in messages[0].content
    assert "printf hello" in messages[0].content
    assert "hello" in messages[0].content


@pytest.mark.anyio
async def test_terminal_command_can_run_without_context(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    result = await session.run_terminal_command("printf hidden", add_to_context=False)

    assert result.output == "hidden"
    assert result.added_to_context is False
    entries = await storage.read_all()
    assert not any(isinstance(entry, MessageEntry) for entry in entries)


# The shell_command_prefix feature routes commands through bash only on POSIX
# (see create_bash_tool); on Windows they run under the default shell.
requires_posix_shell = pytest.mark.skipif(
    sys.platform == "win32", reason="shell_command_prefix uses bash only on POSIX"
)


@requires_posix_shell
@pytest.mark.anyio
async def test_terminal_command_uses_configured_shell_command_prefix(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            shell_command_prefix="shopt -s expand_aliases\nalias greet='printf terminal-alias'",
        )
    )

    result = await session.run_terminal_command("greet", add_to_context=False)

    assert result.output == "terminal-alias"
    assert result.added_to_context is False


@requires_posix_shell
@pytest.mark.anyio
async def test_agent_bash_tool_uses_configured_shell_command_prefix(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            shell_command_prefix="shopt -s expand_aliases\nalias greet='printf agent-alias'",
        )
    )
    bash_tool = next(tool for tool in session.tools if tool.name == "bash")

    result = await bash_tool.execute("call-1", {"command": "greet"})

    assert result.text == "agent-alias"
    assert isinstance(result.details, dict)
    assert result.details["shell_command_prefix_applied"] is True


def test_parse_terminal_command_prefixes() -> None:
    assert parse_terminal_command("! pwd") is not None
    add_request = parse_terminal_command("! pwd")
    assert add_request is not None
    assert add_request.command == "pwd"
    assert add_request.add_to_context is True
    hidden_request = parse_terminal_command("!! pwd")
    assert hidden_request is not None
    assert hidden_request.command == "pwd"
    assert hidden_request.add_to_context is False
    assert parse_terminal_command("hello") is None


@pytest.mark.anyio
async def test_prompt_queues_steering_while_session_is_running(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = WaitingProvider()
    session = await CodingSession.load(_config(tmp_path, provider, storage))
    run_events: list[object] = []

    async def run_prompt() -> None:
        async for event in session.prompt("Hello"):
            run_events.append(event)

    task = asyncio.create_task(run_prompt())
    await provider.started.wait()

    with pytest.raises(RuntimeError, match="already running"):
        await _collect_session_events(session.prompt("Dropped overlap"))

    queue_events = await _collect_session_events(
        session.prompt("Queued steering", streaming_behavior="steer")
    )
    entries_before_release = await storage.read_all()

    provider.release.set()
    await task

    assert queue_events == [QueueUpdateEvent(steering=("Queued steering",))]
    before_release_messages = [
        entry.message for entry in entries_before_release if entry.type == "message"
    ]
    _assert_messages(before_release_messages, [UserMessage(content="Hello")])
    assert entries_before_release[-1].type == "leaf"
    assert entries_before_release[-1].entry_id == next(
        entry.id for entry in entries_before_release if entry.type == "message"
    )
    _assert_messages(
        session.messages,
        (
            UserMessage(content="Hello"),
            AssistantMessage(content="First"),
            UserMessage(content="Queued steering"),
            AssistantMessage(content="Second"),
        ),
    )
    assert provider.calls[1] == list(session.messages[:3])
    entries = await storage.read_all()
    message_entries = [entry for entry in entries if entry.type == "message"]
    leaf_entries = [entry for entry in entries if entry.type == "leaf"]
    assert [entry.message for entry in message_entries] == list(session.messages)
    assert [entry.entry_id for entry in leaf_entries] == [entry.id for entry in message_entries]
    assert not any(isinstance(event, QueueUpdateEvent) for event in run_events)


@pytest.mark.anyio
async def test_tree_can_branch_from_first_user_message_before_assistant_response(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = CancellableWaitingProvider()
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    async def run_prompt() -> None:
        async for _event in session.prompt("Start here"):
            pass

    task = asyncio.create_task(run_prompt())
    await provider.started.wait()

    choices = await session.tree_choices()
    with pytest.raises(RuntimeError, match="Tau is still working"):
        await session.branch_to_entry(choices[0].entry_id)

    session.cancel()
    await task
    result = await session.branch_to_entry(choices[0].entry_id)
    entries = await storage.read_all()
    message_entries = [entry for entry in entries if entry.type == "message"]

    assert [choice.label for choice in choices] == ["user: Start here"]
    assert result == SessionTreeBranchResult(
        message=f"Branched session before {choices[0].entry_id}.",
        input_prefill="Start here",
    )
    assert session.messages == ()
    assert message_entries[0].message.text == "Start here"
    assert isinstance(message_entries[1].message, AssistantMessage)
    assert message_entries[1].message.stop_reason == "error"
    assert isinstance(entries[-1], LeafEntry)
    assert entries[-1].entry_id == message_entries[0].parent_id


@pytest.mark.anyio
async def test_tree_choices_label_structured_tool_calls_without_exposing_thinking(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    entry = MessageEntry(
        id="assistant",
        message=AssistantMessage(
            content=[
                ThinkingContent(thinking="Inspect the project before answering."),
                ToolCall(id="call-1", name="read", arguments={"path": "README.md"}),
                ToolCall(id="call-2", name="bash", arguments={"command": "git status"}),
            ]
        ),
    )
    await storage.append(entry)
    await storage.append(LeafEntry(entry_id=entry.id))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    choices = await session.tree_choices()

    assert len(choices) == 1
    assert choices[0].label == "tool call: read, bash"
    assert choices[0].is_tool_call is True


@pytest.mark.anyio
async def test_tree_choices_handles_deep_session_without_recursion_error(
    tmp_path: Path,
) -> None:
    # A long conversation is a deep root-to-leaf chain of entries. Building the
    # tree picker must not exceed Python's recursion limit. Regression for #277:
    # "/tree" on a long session raised "maximum recursion depth exceeded".
    depth = sys.getrecursionlimit() + 500
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    parent_id: str | None = None
    for index in range(depth):
        entry = MessageEntry(
            id=f"m{index}",
            parent_id=parent_id,
            message=UserMessage(content=f"message {index}"),
        )
        await storage.append(entry)
        parent_id = entry.id
    await storage.append(LeafEntry(parent_id=parent_id, entry_id=parent_id))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    choices = await session.tree_choices()

    assert len(choices) == depth
    assert choices[0].entry_id == "m0"
    assert choices[-1].entry_id == f"m{depth - 1}"


def test_ordered_tree_entries_preserves_branch_order() -> None:
    # Locks the traversal contract the iterative walk must preserve: emit a
    # node's direct children before descending, then depth-first into each child.
    entries = [
        MessageEntry(id="A", parent_id=None, message=UserMessage(content="A")),
        MessageEntry(id="B", parent_id=None, message=UserMessage(content="B")),
        MessageEntry(id="C", parent_id="A", message=UserMessage(content="C")),
        MessageEntry(id="D", parent_id="A", message=UserMessage(content="D")),
        MessageEntry(id="E", parent_id="B", message=UserMessage(content="E")),
        MessageEntry(id="F", parent_id="C", message=UserMessage(content="F")),
    ]

    ordered = _ordered_tree_entries(entries)

    assert [entry.id for entry in ordered] == ["A", "B", "C", "D", "F", "E"]


def test_ordered_tree_entries_terminates_on_parent_cycle() -> None:
    # A malformed parent cycle must terminate (not hang or overflow) and still
    # emit each entry exactly once. Guards the iterative walk's cycle safety.
    entries = [
        MessageEntry(id="a", parent_id="b", message=UserMessage(content="a")),
        MessageEntry(id="b", parent_id="a", message=UserMessage(content="b")),
    ]

    ordered = _ordered_tree_entries(entries)

    assert sorted(entry.id for entry in ordered) == ["a", "b"]
    assert len(ordered) == 2


@pytest.mark.anyio
async def test_branch_to_entry_repairs_orphaned_tool_result(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    root = MessageEntry(id="root", message=UserMessage(content="start"))
    orphan = MessageEntry(
        id="orphan",
        parent_id=root.id,
        message=ToolResultMessage(tool_call_id="call-missing", tool_name="bash", content="orphan"),
    )
    answer = MessageEntry(
        id="answer",
        parent_id=orphan.id,
        message=AssistantMessage(content="answer"),
    )
    for entry in (root, orphan, answer, LeafEntry(parent_id=root.id, entry_id=root.id)):
        await storage.append(entry)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )

    result = await session.branch_to_entry(answer.id)

    assert result.message.endswith("and repaired malformed tool history.")
    _assert_messages(
        session.messages,
        [UserMessage(content="start"), AssistantMessage(content="answer")],
    )
    diagnostics = [
        entry
        for entry in (await storage.read_all())
        if isinstance(entry, CustomEntry) and entry.namespace == "tau.session-history-repair"
    ]
    assert len(diagnostics) == 1


@pytest.mark.anyio
async def test_tree_branching_preserves_active_model(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    await storage.append(MessageEntry(id="first", message=UserMessage(content="Earlier")))
    await storage.append(ModelChangeEntry(id="historical-model", parent_id="first", model="old"))
    await storage.append(
        MessageEntry(
            id="assistant",
            parent_id="historical-model",
            message=AssistantMessage(content="Old answer"),
        )
    )
    await storage.append(ModelChangeEntry(id="current-model", parent_id="assistant", model="new"))
    await storage.append(
        MessageEntry(
            id="latest",
            parent_id="current-model",
            message=UserMessage(content="Latest"),
        )
    )
    await storage.append(LeafEntry(entry_id="latest"))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    result = await session.branch_to_entry("assistant")

    assert result == SessionTreeBranchResult(message="Branched session at assistant.")
    assert session.model == "new"
    assert session.state.model == "old"
    _assert_messages(
        session.messages,
        (
            UserMessage(content="Earlier"),
            AssistantMessage(content="Old answer"),
        ),
    )


@pytest.mark.anyio
async def test_context_usage_is_cached_until_session_context_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))
    calls = 0
    original_estimate = coding_session_module.estimate_context_usage

    def wrapped_estimate(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return original_estimate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(coding_session_module, "estimate_context_usage", wrapped_estimate)

    initial_usage = session.context_usage
    cached_usage = session.context_usage

    assert cached_usage is initial_usage
    assert calls == 1


@pytest.mark.anyio
async def test_context_usage_recalculates_after_prompt_and_compaction(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(
                    message=AssistantMessage(content="Long answer " * 80),
                ),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Short summary")),
            ],
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, storage))
    initial_usage = session.context_usage

    _events = await _collect_session_events(session.prompt("Explain context accounting."))
    after_prompt_usage = session.context_usage

    assert after_prompt_usage.message_count == 2
    assert after_prompt_usage.total_tokens > initial_usage.total_tokens
    assert session.context_token_estimate == after_prompt_usage.total_tokens

    _message = await session.compact("Context accounting was discussed.")
    after_compaction_usage = session.context_usage

    assert after_compaction_usage.message_count == 1
    assert after_compaction_usage.total_tokens < after_prompt_usage.total_tokens
    assert session.context_token_estimate == after_compaction_usage.total_tokens


@pytest.mark.anyio
async def test_session_persists_and_replays_thinking_level_changes(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    message = await session.set_thinking_level("high")
    entries = await storage.read_all()
    thinking_entries = [entry for entry in entries if entry.type == "thinking_level_change"]
    leaves = [entry for entry in entries if entry.type == "leaf"]

    restored = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    assert message == "Thinking mode: high"
    assert session.thinking_level == "high"
    assert len(thinking_entries) == 2
    assert thinking_entries[-1].thinking_level == "high"
    assert leaves[-1].entry_id == thinking_entries[-1].id
    assert restored.thinking_level == "high"
    assert restored.state.thinking_level == "high"


@pytest.mark.anyio
async def test_session_cycles_thinking_level(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    message = await session.cycle_thinking_level()

    assert message == "Thinking mode: high"
    assert session.thinking_level == "high"


@pytest.mark.anyio
async def test_session_updates_read_image_behavior_when_model_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    isolate_home(monkeypatch, tmp_path)
    provider_config = OpenAICompatibleProviderConfig(
        name="openai",
        models=("text-only", "vision"),
        default_model="text-only",
        model_metadata={
            "text-only": ProviderModelMetadata(input=("text",)),
            "vision": ProviderModelMetadata(input=("text", "image")),
        },
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="text-only",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=ProviderSettings(providers=(provider_config,)),
        )
    )
    output = BytesIO()
    Image.new("RGB", (8, 6), "navy").save(output, format="PNG")
    (tmp_path / "image.png").write_bytes(output.getvalue())
    read_tool = next(tool for tool in session.tools if tool.name == "read")

    omitted = await read_tool.execute("call-1", {"path": "image.png"})

    assert "do not infer or describe" in omitted.text
    assert not any(isinstance(block, ImageContent) for block in omitted.content)

    session.set_model("vision")
    attached = await read_tool.execute("call-2", {"path": "image.png"})

    assert any(isinstance(block, ImageContent) for block in attached.content)


@pytest.mark.anyio
async def test_session_uses_active_model_thinking_capabilities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    isolate_home(monkeypatch, tmp_path)
    provider_config = OpenAICompatibleProviderConfig(
        name="openai",
        models=("reasoner", "plain"),
        default_model="reasoner",
        thinking_levels=("off", "low", "high"),
        thinking_models=("reasoner",),
        thinking_default="low",
        thinking_parameter="reasoning_effort",
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="reasoner",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=ProviderSettings(providers=(provider_config,)),
        )
    )

    assert session.available_thinking_levels == ("off", "low", "high")
    assert session.thinking_level == "low"
    assert session.thinking_unavailable_reason is None
    assert await session.set_thinking_level("high") == "Thinking mode: high"

    with pytest.raises(ValueError, match="not available"):
        await session.set_thinking_level("medium")

    session.set_model("plain")

    assert session.available_thinking_levels == ()
    assert session.thinking_unavailable_reason == "openai:plain is not declared in thinking_models"
    with pytest.raises(ValueError, match="openai:plain is not declared in thinking_models"):
        await session.cycle_thinking_level()

    session.set_model("reasoner")

    assert session.available_thinking_levels == ("off", "low", "high")
    assert session.thinking_level == "high"
    assert session.thinking_unavailable_reason is None


@pytest.mark.anyio
async def test_session_persists_thinking_preference_for_new_sessions(tmp_path: Path) -> None:
    tau_paths = TauPaths(home=tmp_path / ".tau")
    provider_config = OpenAICodexProviderConfig(
        thinking_levels=("off", "minimal", "low", "medium", "high", "xhigh"),
        thinking_models=("gpt-5.5",),
        thinking_default="medium",
        thinking_parameter="reasoning.effort",
    )
    settings = ProviderSettings(
        default_provider="openai-codex",
        providers=(provider_config,),
    )
    storage = JsonlSessionStorage(tmp_path / "codex-session.jsonl")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5.5",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            provider_name="openai-codex",
            provider_settings=settings,
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    assert session.thinking_level == "medium"
    assert await session.set_thinking_level("low") == "Thinking mode: low"

    saved = load_provider_settings(tau_paths)
    assert saved.get_provider("openai-codex").thinking_defaults == {"gpt-5.5": "low"}

    new_session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5.5",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "new-codex-session.jsonl"),
            cwd=tmp_path,
            provider_name="openai-codex",
            provider_settings=saved,
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    assert new_session.thinking_level == "low"


@pytest.mark.anyio
async def test_resumed_session_history_overrides_saved_thinking_preference(
    tmp_path: Path,
) -> None:
    provider_config = OpenAICompatibleProviderConfig(
        name="openai",
        models=("reasoner",),
        default_model="reasoner",
        thinking_levels=("low", "high"),
        thinking_default="low",
        thinking_parameter="reasoning_effort",
        thinking_defaults={"reasoner": "low"},
    )
    storage = JsonlSessionStorage(tmp_path / "resume-thinking-session.jsonl")
    info = SessionInfoEntry(id="info", cwd=str(tmp_path))
    model = ModelChangeEntry(id="model", parent_id="info", model="reasoner")
    thinking = ThinkingLevelChangeEntry(
        id="thinking",
        parent_id="model",
        thinking_level="high",
    )
    leaf = LeafEntry(id="leaf", parent_id="thinking", entry_id="thinking")
    for entry in (info, model, thinking, leaf):
        await storage.append(entry)

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="reasoner",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=ProviderSettings(providers=(provider_config,)),
        )
    )

    assert session.thinking_level == "high"


@pytest.mark.anyio
async def test_session_uses_codex_subscription_thinking_capabilities(
    tmp_path: Path,
) -> None:
    provider_config = OpenAICodexProviderConfig(
        thinking_levels=("off", "minimal", "low", "medium", "high", "xhigh"),
        thinking_models=("gpt-5.5",),
        thinking_default="medium",
        thinking_parameter="reasoning.effort",
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5.5",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "codex-session.jsonl"),
            cwd=tmp_path,
            provider_name="openai-codex",
            provider_settings=ProviderSettings(providers=(provider_config,)),
        )
    )

    assert session.available_thinking_levels == (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    )
    assert session.thinking_unavailable_reason is None
    assert await session.set_thinking_level("high") == "Thinking mode: high"


@pytest.mark.anyio
async def test_session_refreshes_runtime_provider_for_thinking_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created: list[tuple[str | None, str | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del provider_config, credential_store
        created.append((model, thinking_level))
        return SwitchableFakeProvider(object())

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    provider_config = OpenAICompatibleProviderConfig(
        name="openai",
        models=("reasoner",),
        default_model="reasoner",
        thinking_levels=("low", "high"),
        thinking_default="low",
        thinking_parameter="reasoning_effort",
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="reasoner",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "runtime-session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=ProviderSettings(providers=(provider_config,)),
            runtime_provider_config=provider_config,
            thinking_level="high",
        )
    )

    assert created == [("reasoner", "high")]

    await session.set_thinking_level("low")

    assert created[-1] == ("reasoner", "low")

    await session.aclose()


@pytest.mark.anyio
async def test_load_restores_existing_transcript(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    user_entry = MessageEntry(id="user", message=UserMessage(content="Earlier"))
    assistant_entry = MessageEntry(
        id="assistant",
        parent_id="user",
        message=AssistantMessage(content="Restored"),
    )
    await storage.append(user_entry)
    await storage.append(assistant_entry)

    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    _assert_messages(
        session.messages,
        (
            UserMessage(content="Earlier"),
            AssistantMessage(content="Restored"),
        ),
    )


@pytest.mark.anyio
async def test_load_detaches_missing_root_parent_from_imported_branch(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    root = MessageEntry(
        id="root",
        parent_id="missing-external-parent",
        message=UserMessage(content="Root"),
    )
    assistant = MessageEntry(
        id="assistant",
        parent_id="root",
        message=AssistantMessage(content="Restored"),
    )
    await storage.append(root)
    await storage.append(assistant)
    await storage.append(LeafEntry(entry_id="assistant"))

    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    _assert_messages(
        session.messages,
        (
            UserMessage(content="Root"),
            AssistantMessage(content="Restored"),
        ),
    )
    assert session.state.active_leaf_id == "assistant"


@pytest.mark.anyio
async def test_tree_branching_detaches_missing_root_parent_from_imported_branch(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    root = MessageEntry(
        id="root",
        parent_id="missing-external-parent",
        message=UserMessage(content="Root"),
    )
    assistant = MessageEntry(
        id="assistant",
        parent_id="root",
        message=AssistantMessage(content="Restored"),
    )
    await storage.append(root)
    await storage.append(assistant)
    await storage.append(LeafEntry(parent_id="assistant", entry_id="assistant"))

    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))
    choices = await session.tree_choices()
    result = await session.branch_to_entry("root")

    assert [choice.entry_id for choice in choices] == ["root", "assistant"]
    assert result == SessionTreeBranchResult(
        message="Branched session before root.",
        input_prefill="Root",
    )
    assert session.messages == ()


@pytest.mark.anyio
async def test_load_restores_explicit_empty_leaf_branch(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    await storage.append(root)
    await storage.append(LeafEntry(entry_id="root"))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    result = await session.branch_to_entry("root")
    reloaded = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    assert result == SessionTreeBranchResult(
        message="Branched session before root.",
        input_prefill="Root",
    )
    assert session.messages == ()
    assert reloaded.messages == ()
    assert reloaded.state.active_leaf_id is None


@pytest.mark.anyio
async def test_load_restores_active_leaf_branch(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    left = MessageEntry(
        id="left",
        parent_id="root",
        message=AssistantMessage(content="Inactive branch"),
    )
    right = MessageEntry(
        id="right",
        parent_id="root",
        message=AssistantMessage(content="Active branch"),
    )
    await storage.append(root)
    await storage.append(left)
    await storage.append(right)
    await storage.append(LeafEntry(entry_id="right"))

    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    _assert_messages(
        session.messages,
        (
            UserMessage(content="Root"),
            AssistantMessage(content="Active branch"),
        ),
    )
    assert session.state.active_leaf_id == "right"


@pytest.mark.anyio
async def test_session_tree_choices_indent_only_diverged_branches(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    main = MessageEntry(id="main", parent_id="root", message=AssistantMessage(content="Main"))
    first_branch = MessageEntry(
        id="first-branch",
        parent_id="root",
        message=AssistantMessage(content="First branch"),
    )
    first_branch_child = MessageEntry(
        id="first-branch-child",
        parent_id="first-branch",
        message=UserMessage(content="Follow-up"),
    )
    main_child = MessageEntry(
        id="main-child",
        parent_id="main",
        message=UserMessage(content="Main follow-up"),
    )
    second_branch = MessageEntry(
        id="second-branch",
        parent_id="root",
        message=AssistantMessage(content="Second branch"),
    )
    await storage.append(root)
    await storage.append(main)
    await storage.append(first_branch)
    await storage.append(first_branch_child)
    await storage.append(main_child)
    await storage.append(second_branch)
    await storage.append(LeafEntry(entry_id="second-branch"))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    choices = await session.tree_choices()

    assert [choice.label for choice in choices] == [
        "user: Root",
        "assistant: Main",
        "  assistant: First branch",
        "  assistant: Second branch",
        "user: Main follow-up",
        "  user: Follow-up",
    ]


@pytest.mark.anyio
async def test_session_branches_to_previous_entry_without_destroying_history(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    left = MessageEntry(id="left", parent_id="root", message=AssistantMessage(content="Left"))
    right = MessageEntry(id="right", parent_id="root", message=AssistantMessage(content="Right"))
    await storage.append(root)
    await storage.append(left)
    await storage.append(right)
    await storage.append(LeafEntry(entry_id="right"))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    result = await session.branch_to_entry("left")

    entries = await storage.read_all()
    assert result == SessionTreeBranchResult(message="Branched session at left.")
    _assert_messages(
        session.messages, (UserMessage(content="Root"), AssistantMessage(content="Left"))
    )
    assert [entry.id for entry in entries if entry.type == "message"] == ["root", "left", "right"]
    assert isinstance(entries[-1], LeafEntry)
    assert entries[-1].entry_id == "left"


@pytest.mark.anyio
async def test_persist_after_branch_keeps_state_on_active_branch(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="New answer")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Branch summary")),
            ],
        ]
    )
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    answer = MessageEntry(id="answer", parent_id="root", message=AssistantMessage(content="Answer"))
    abandoned = MessageEntry(
        id="abandoned",
        parent_id="answer",
        message=UserMessage(content="Abandoned follow-up"),
    )
    abandoned_answer = MessageEntry(
        id="abandoned-answer",
        parent_id="abandoned",
        message=AssistantMessage(content="Abandoned answer"),
    )
    await storage.append(root)
    await storage.append(answer)
    await storage.append(abandoned)
    await storage.append(abandoned_answer)
    await storage.append(LeafEntry(entry_id="abandoned-answer"))
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    await session.branch_to_entry("answer")
    _events = await _collect_session_events(session.prompt("New follow-up"))

    _assert_messages(
        session.state.messages,
        (
            UserMessage(content="Root"),
            AssistantMessage(content="Answer"),
            UserMessage(content="New follow-up"),
            AssistantMessage(content="New answer"),
        ),
    )
    assert "abandoned" not in session.state.context_entry_ids
    assert "abandoned-answer" not in session.state.context_entry_ids

    await session.compact()
    compactions = [entry for entry in await storage.read_all() if entry.type == "compaction"]
    assert len(compactions) == 1
    assert "abandoned" not in compactions[0].replaces_entry_ids
    assert "abandoned-answer" not in compactions[0].replaces_entry_ids
    assert "Abandoned" not in provider.calls[1][2][0].content


@pytest.mark.anyio
async def test_session_branches_to_before_selected_user_message_with_prefill(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    assistant = MessageEntry(
        id="assistant",
        parent_id="root",
        message=AssistantMessage(content="Answer"),
    )
    followup = MessageEntry(
        id="followup",
        parent_id="assistant",
        message=UserMessage(content="Try this again"),
    )
    await storage.append(root)
    await storage.append(assistant)
    await storage.append(followup)
    await storage.append(LeafEntry(entry_id="followup"))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    result = await session.branch_to_entry("followup")

    entries = await storage.read_all()
    assert result == SessionTreeBranchResult(
        message="Branched session before followup.",
        input_prefill="Try this again",
    )
    _assert_messages(
        session.messages, (UserMessage(content="Root"), AssistantMessage(content="Answer"))
    )
    assert [entry.id for entry in entries if entry.type == "message"] == [
        "root",
        "assistant",
        "followup",
    ]
    assert isinstance(entries[-1], LeafEntry)
    assert entries[-1].entry_id == "assistant"


@pytest.mark.anyio
async def test_session_branch_preserves_active_model(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    first_model = ModelChangeEntry(id="model-a", model="first-model")
    left = MessageEntry(
        id="left",
        parent_id="model-a",
        message=UserMessage(content="Before switch"),
    )
    second_model = ModelChangeEntry(
        id="model-b",
        parent_id="left",
        model="second-model",
    )
    right = MessageEntry(
        id="right",
        parent_id="model-b",
        message=AssistantMessage(content="After switch"),
    )
    await storage.append(first_model)
    await storage.append(left)
    await storage.append(second_model)
    await storage.append(right)
    await storage.append(LeafEntry(entry_id="right"))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    assert session.model == "second-model"

    await session.branch_to_entry("left")

    assert session.state.model == "first-model"
    assert session.model == "second-model"


@pytest.mark.anyio
async def test_session_branch_with_summary_keeps_pre_branch_model_and_messages(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    first_model = ModelChangeEntry(id="model-a", model="first-model")
    left = MessageEntry(
        id="left",
        parent_id="model-a",
        message=UserMessage(content="Before switch"),
    )
    second_model = ModelChangeEntry(
        id="model-b",
        parent_id="left",
        model="second-model",
    )
    right = MessageEntry(
        id="right",
        parent_id="model-b",
        message=AssistantMessage(content="After switch"),
    )
    await storage.append(first_model)
    await storage.append(left)
    await storage.append(second_model)
    await storage.append(right)
    await storage.append(LeafEntry(entry_id="right"))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    assert session.model == "second-model"

    await session.branch_to_entry("left", summarize=True)

    assert session.state.model == "first-model"
    assert session.model == "second-model"
    assert len(session.messages) == 2
    assert session.messages[0].text == "Before switch"
    assert session.messages[1].content.startswith(
        "The following is a summary of a branch that this conversation came back from:"
    )


@pytest.mark.anyio
async def test_session_branch_with_summary_rebuilds_context(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="The abandoned branch went left.")),
            ]
        ]
    )
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    left = MessageEntry(id="left", parent_id="root", message=AssistantMessage(content="Left"))
    right = MessageEntry(
        id="right",
        parent_id="left",
        message=UserMessage(content="Abandoned follow-up"),
    )
    await storage.append(root)
    await storage.append(left)
    await storage.append(right)
    await storage.append(LeafEntry(entry_id="right"))
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    result = await session.branch_to_entry("root", summarize=True)
    entries = await storage.read_all()
    summary = entries[-2]

    assert "with branch summary" in result.message
    assert summary.type == "branch_summary"
    assert summary.parent_id == "root"
    assert summary.branch_root_id == "root"
    assert summary.summary.startswith(
        "The user explored a different conversation branch before returning here."
    )
    assert "The abandoned branch went left." in summary.summary
    assert provider.calls[0][3] == []
    assert "<conversation>" in provider.calls[0][2][0].content
    assert "Use this EXACT format:" in provider.calls[0][2][0].content
    assert "Abandoned follow-up" in provider.calls[0][2][0].content
    assert len(session.messages) == 2
    assert session.messages[0].text == "Root"
    assert session.messages[1].role == "user"
    assert isinstance(session.messages[1].content, str)
    assert session.messages[1].content.startswith(
        "The following is a summary of a branch that this conversation came back from:"
    )
    assert "The abandoned branch went left." in session.messages[1].content


@pytest.mark.anyio
async def test_session_branch_with_summary_accepts_custom_instructions(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Custom branch summary.")),
            ]
        ]
    )
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    left = MessageEntry(id="left", parent_id="root", message=AssistantMessage(content="Left"))
    right = MessageEntry(
        id="right",
        parent_id="left",
        message=UserMessage(content="Abandoned follow-up"),
    )
    await storage.append(root)
    await storage.append(left)
    await storage.append(right)
    await storage.append(LeafEntry(entry_id="right"))
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    await session.branch_to_entry(
        "root",
        summarize=True,
        custom_instructions="Focus on failing commands.",
    )

    prompt = provider.calls[0][2][0].content
    assert "Use this EXACT format:" in prompt
    assert "Additional focus: Focus on failing commands." in prompt


@pytest.mark.anyio
async def test_session_branch_with_summary_tracks_file_operations(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="File work summary.")),
            ]
        ]
    )
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    read_call = ToolCall(id="read-1", name="read", arguments={"path": "src/read_only.py"})
    edit_call = ToolCall(id="edit-1", name="edit", arguments={"path": "src/changed.py"})
    assistant = MessageEntry(
        id="assistant",
        parent_id="root",
        message=AssistantMessage(content=assistant_content("Using tools", [read_call, edit_call])),
    )
    await storage.append(root)
    await storage.append(assistant)
    await storage.append(LeafEntry(entry_id="assistant"))
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    await session.branch_to_entry("root", summarize=True)
    entries = await storage.read_all()
    summary = entries[-2]

    assert summary.type == "branch_summary"
    assert "<read-files>\nsrc/read_only.py\n</read-files>" in summary.summary
    assert "<modified-files>\nsrc/changed.py\n</modified-files>" in summary.summary


@pytest.mark.anyio
async def test_session_branch_with_summary_falls_back_when_model_summary_is_unavailable(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    left = MessageEntry(id="left", parent_id="root", message=AssistantMessage(content="Left"))
    right = MessageEntry(
        id="right",
        parent_id="left",
        message=UserMessage(content="Abandoned follow-up"),
    )
    await storage.append(root)
    await storage.append(left)
    await storage.append(right)
    await storage.append(LeafEntry(entry_id="right"))
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    result = await session.branch_to_entry("root", summarize=True)
    entries = await storage.read_all()
    summary = entries[-2]

    assert "with branch summary" in result.message
    assert summary.type == "branch_summary"
    assert "Automatically compacted 2 prior message(s)." in summary.summary
    assert "Abandoned follow-up" in summary.summary
    assert len(session.messages) == 2
    assert session.messages[0].text == "Root"
    assert "Abandoned follow-up" in session.messages[1].content


@pytest.mark.anyio
async def test_continue_persists_only_new_messages(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    await storage.append(MessageEntry(id="user", message=UserMessage(content="Continue me")))
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Continued")),
            ]
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    _events = await _collect_session_events(session.continue_())

    entries = await storage.read_all()
    message_entries = [entry for entry in entries if entry.type == "message"]
    _assert_messages(
        [entry.message for entry in message_entries],
        [
            UserMessage(content="Continue me"),
            AssistantMessage(content="Continued"),
        ],
    )


@pytest.mark.anyio
async def test_tool_results_are_persisted(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    tool_call = ToolCall(id="call-1", name="missing", arguments={})
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(
                    message=AssistantMessage(content=assistant_content("Using tool", [tool_call])),
                    finish_reason="tool_calls",
                ),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ],
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    _events = await _collect_session_events(session.prompt("Use a tool"))

    messages = [entry.message for entry in await storage.read_all() if entry.type == "message"]
    assert any(isinstance(message, ToolResultMessage) for message in messages)


@pytest.mark.anyio
async def test_session_preserves_explicit_empty_system_prompt(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    config = CodingSessionConfig(
        provider=provider,
        model="fake",
        system="",
        storage=storage,
        cwd=tmp_path,
    )
    session = await CodingSession.load(config)

    _events = await _collect_session_events(session.prompt("Hello"))

    assert provider.calls[0][1] == ""


@pytest.mark.anyio
async def test_session_builds_system_prompt_when_system_is_omitted(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills"
    skills_dir.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("Follow project rules.", encoding="utf-8")
    (skills_dir / "testing").mkdir()
    (skills_dir / "testing" / "SKILL.md").write_text(
        "---\ndescription: Test code\n---\n# Testing",
        encoding="utf-8",
    )
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    config = CodingSessionConfig(
        provider=provider,
        model="fake",
        storage=storage,
        cwd=tmp_path,
        trust_default="always",
        resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
    )
    session = await CodingSession.load(config)

    _events = await _collect_session_events(session.prompt("Hello"))

    assert "Available tools:\n- read: Read file contents" in provider.calls[0][1]
    assert '<project_instructions path="' in provider.calls[0][1]
    assert "Follow project rules." in provider.calls[0][1]
    assert "<available_skills>" in provider.calls[0][1]
    assert "<name>testing</name>" in provider.calls[0][1]
    assert [Path(context_file.path).name for context_file in session.context_files] == ["AGENTS.md"]


@pytest.mark.anyio
async def test_session_touches_session_manager_after_persisting_messages(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Greeting")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ],
        ]
    )
    config = CodingSessionConfig(
        provider=provider,
        model="fake",
        system="You are Tau.",
        storage=storage,
        cwd=tmp_path,
        session_id=record.id,
        session_manager=manager,
        resource_paths=TauResourcePaths(root=tmp_path / "resources", agents_root=None),
    )
    session = await CodingSession.load(config)

    _events = await _collect_session_events(session.prompt("Hello"))

    updated = manager.get_session(record.id)
    assert updated is not None
    assert updated.updated_at >= record.updated_at


@pytest.mark.anyio
async def test_session_auto_names_first_unnamed_managed_session(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content='"Fix broken CLI output now"')),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
        )
    )

    await _collect_session_events(session.prompt("Please fix the broken CLI output."))

    renamed = manager.get_session(record.id)
    assert renamed is not None
    assert renamed.title == "Fix broken CLI output"
    assert provider.calls[0][0] == "fake"
    assert provider.calls[0][3] == []
    assert "Please fix the broken CLI output." in provider.calls[0][2][0].content
    _assert_messages(
        provider.calls[1][2], [UserMessage(content="Please fix the broken CLI output.")]
    )


@pytest.mark.anyio
async def test_session_yields_expanded_custom_prompt_before_auto_naming(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake")
    resources_root = tmp_path / "resources"
    prompts_dir = resources_root / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "review.md").write_text("Review this target:\n{{ arguments }}")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Review target")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
            resource_paths=TauResourcePaths(root=resources_root, agents_root=None),
        )
    )

    stream = session.prompt("/review src/app.py")
    for _ in range(3):
        await anext(stream)
    prompt_event = await asyncio.wait_for(anext(stream), timeout=1)

    assert isinstance(prompt_event, MessageEndEvent)
    assert isinstance(prompt_event.message, UserMessage)
    assert prompt_event.message.text == "Review this target:\nsrc/app.py"
    assert provider.calls == []

    await _collect_session_events(stream)
    renamed = manager.get_session(record.id)
    assert renamed is not None
    assert renamed.title == "Review target"


@pytest.mark.anyio
async def test_session_auto_name_falls_back_when_provider_fails(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake")
    provider = FakeProvider(
        [
            [
                assistant_error(message="naming failed"),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
        )
    )

    await _collect_session_events(session.prompt("Investigate flaky session restore tests"))

    renamed = manager.get_session(record.id)
    assert renamed is not None
    assert renamed.title == "Investigate flaky session restore"
    _assert_messages(
        session.messages,
        (
            UserMessage(content="Investigate flaky session restore tests"),
            AssistantMessage(content="Done"),
        ),
    )


@pytest.mark.anyio
async def test_session_auto_name_falls_back_when_provider_returns_unusable_title(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="!!!")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
        )
    )

    await _collect_session_events(session.prompt("Debug failing model picker"))

    renamed = manager.get_session(record.id)
    assert renamed is not None
    assert renamed.title == "Debug failing model picker"


@pytest.mark.anyio
async def test_session_auto_name_does_not_overwrite_manual_name(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake", title="Manual name")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
        )
    )

    await _collect_session_events(session.prompt("Rename this automatically"))

    unchanged = manager.get_session(record.id)
    assert unchanged is not None
    assert unchanged.title == "Manual name"
    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_session_auto_name_does_not_index_new_session_before_first_persist(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.prepare_session(cwd=tmp_path, model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Generated title")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=record.cwd,
            session_id=record.id,
            session_manager=manager,
            index_on_first_persist=True,
        )
    )

    stream = session.prompt("Stop before the first persisted message")
    _first_event = await anext(stream)
    await stream.aclose()

    assert manager.get_session(record.id) is None
    assert await storage.read_all() == []


@pytest.mark.anyio
async def test_session_loads_and_expands_skills(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills" / "testing"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# Testing\nRun pytest.", encoding="utf-8")
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    config = CodingSessionConfig(
        provider=provider,
        model="fake",
        system="You are Tau.",
        storage=storage,
        cwd=tmp_path,
        resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
    )
    session = await CodingSession.load(config)

    _events = await _collect_session_events(session.prompt("/skill:testing\n\nadd tests"))

    assert {skill.name for skill in session.skills} == {"testing"}
    assert '<skill name="testing" location="' in provider.calls[0][2][0].content
    assert "References are relative to" in provider.calls[0][2][0].content
    assert provider.calls[0][2][0].content.endswith("</skill>\n\nadd tests")
    assert session.handle_command("/skill:testing").handled is False


@pytest.mark.anyio
async def test_session_skills_disabled_suppresses_skill_index(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "testing.md").write_text(
        "---\ndescription: Test code\n---\n# Testing",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("Follow project rules.", encoding="utf-8")
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    config = CodingSessionConfig(
        provider=provider,
        model="fake",
        storage=storage,
        cwd=tmp_path,
        trust_default="always",
        skills_enabled=False,
        resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
    )
    session = await CodingSession.load(config)

    _events = await _collect_session_events(session.prompt("Hello"))

    # Skill discovery is suppressed: no skills, no <available_skills> index.
    assert session.skills == ()
    assert "<available_skills>" not in provider.calls[0][1]
    # Project context (AGENTS.md) remains unaffected by disabling skills.
    assert "Follow project rules." in provider.calls[0][1]
    assert [Path(context_file.path).name for context_file in session.context_files] == ["AGENTS.md"]
    # /skill: commands have nothing to expand against.
    with pytest.raises(ResourceError):
        session.expand_prompt_text("/skill:testing")


@pytest.mark.anyio
async def test_session_reload_preserves_disabled_skills(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "testing.md").write_text(
        "---\ndescription: Test code\n---\n# Testing",
        encoding="utf-8",
    )
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            storage=storage,
            cwd=tmp_path,
            skills_enabled=False,
            resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
        )
    )

    assert session.skills == ()

    await session.reload()

    assert session.skills == ()
    assert "<available_skills>" not in session.system_prompt


@pytest.mark.anyio
async def test_system_command_shows_prompt_without_persisting_or_adding_context(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider([])
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )

    before_messages = session.messages
    before_entries = await storage.read_all()

    result = session.handle_command("/system")

    assert result.handled is True
    assert result.message == "You are Tau."
    assert session.messages == before_messages
    assert await storage.read_all() == before_entries
    assert provider.calls == []


@pytest.mark.anyio
async def test_session_expands_prompt_templates_as_slash_commands(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    prompts_dir = resource_root / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "example.md").write_text(
        "Custom prompt for {{ arguments }}.",
        encoding="utf-8",
    )
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    config = CodingSessionConfig(
        provider=provider,
        model="fake",
        system="You are Tau.",
        storage=storage,
        cwd=tmp_path,
        resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
    )
    session = await CodingSession.load(config)

    assert [template.name for template in session.prompt_templates] == ["example"]
    assert session.handle_command("/example src/app.py").handled is False

    _events = await _collect_session_events(session.prompt("/example src/app.py"))

    assert provider.calls[0][2][0].content == "Custom prompt for src/app.py."


@pytest.mark.anyio
async def test_reserved_prompts_template_cannot_shadow_picker_command(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    prompts_dir = resource_root / "prompts"
    prompts_dir.mkdir(parents=True)
    reserved_path = prompts_dir / "prompts.md"
    reserved_path.write_text("Shadow the picker", encoding="utf-8")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
        )
    )

    result = session.handle_command("/prompts")

    assert result.handled is True
    assert result.prompts_picker_requested is True
    assert session.prompt_templates == ()
    assert any(
        diagnostic.path == reserved_path
        and "reserved by the built-in /prompts command" in diagnostic.message
        for diagnostic in session.resource_diagnostics
    )


@pytest.mark.anyio
async def test_reserved_tools_template_cannot_shadow_picker_command(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    prompts_dir = resource_root / "prompts"
    prompts_dir.mkdir(parents=True)
    reserved_path = prompts_dir / "tools.md"
    reserved_path.write_text("Shadow the picker", encoding="utf-8")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
        )
    )

    result = session.handle_command("/tools")

    assert result.handled is True
    assert result.tools_picker_requested is True
    assert session.prompt_templates == ()
    assert any(
        diagnostic.path == reserved_path
        and "reserved by the built-in /tools command" in diagnostic.message
        for diagnostic in session.resource_diagnostics
    )


@pytest.mark.anyio
async def test_session_skill_index_lets_agent_read_relevant_skill_file(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills" / "testing"
    skills_dir.mkdir(parents=True)
    skill_path = skills_dir / "SKILL.md"
    skill_path.write_text(
        "---\ndescription: Use when writing tests\n---\n# Testing\nRun pytest.",
        encoding="utf-8",
    )
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": str(skill_path)})
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(
                    message=AssistantMessage(
                        content=assistant_content("Reading skill.", [tool_call])
                    ),
                    finish_reason="tool_calls",
                ),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Skill applied.")),
            ],
        ]
    )
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            storage=storage,
            cwd=tmp_path,
            resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
        )
    )

    _events = await _collect_session_events(session.prompt("Add tests."))

    assert "<available_skills>" in provider.calls[0][1]
    assert f"<location>{skill_path}</location>" in provider.calls[0][1]
    assert len(provider.calls) == 2
    tool_result = provider.calls[1][2][-1]
    assert isinstance(tool_result, ToolResultMessage)
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.tool_name == "read"
    assert tool_result.is_error is False
    assert "# Testing\nRun pytest." in tool_result.text
    assert isinstance(tool_result.details, dict)
    assert tool_result.details["path"] == str(skill_path)


@pytest.mark.anyio
async def test_session_loads_with_resource_diagnostics_instead_of_failing(
    tmp_path: Path,
) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills"
    (skills_dir / "good").mkdir(parents=True)
    (skills_dir / "good" / "SKILL.md").write_text("# Directory skill", encoding="utf-8")
    (skills_dir / "legacy.md").write_text("# Legacy bare-md skill", encoding="utf-8")
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    config = CodingSessionConfig(
        provider=FakeProvider([]),
        model="fake",
        system="You are Tau.",
        storage=storage,
        cwd=tmp_path,
        resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
    )

    session = await CodingSession.load(config)

    assert "good" in {skill.name for skill in session.skills}
    assert len(session.resource_diagnostics) == 1
    assert (
        "bare .md files are no longer treated as skills" in session.resource_diagnostics[0].message
    )
    assert "Resource diagnostics: 1" in (session.handle_command("/session").message or "")


@pytest.mark.anyio
async def test_session_loads_tau_native_system_prompt_files(tmp_path: Path) -> None:
    tau_home = tmp_path / "tau-home"
    project_tau = tmp_path / ".tau"
    tau_home.mkdir()
    project_tau.mkdir()
    (tau_home / "SYSTEM.md").write_text("User base", encoding="utf-8")
    (project_tau / "SYSTEM.md").write_text("Project base", encoding="utf-8")
    (tau_home / "APPEND_SYSTEM.md").write_text("User append", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Project instructions", encoding="utf-8")

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            trust_default="always",
            resource_paths=TauResourcePaths(root=tau_home, agents_root=None),
        )
    )

    assert session.system_prompt.startswith("Project base\n\nUser append")
    assert "User base" not in session.system_prompt
    assert "Project instructions" in session.system_prompt
    assert "Current date:" in session.system_prompt
    assert f"Current working directory: {tmp_path}" in session.system_prompt
    prompt_diagnostics = [
        item for item in session.resource_diagnostics if item.kind == "system-prompt"
    ]
    assert [item.severity for item in prompt_diagnostics] == ["info", "warning", "info"]


@pytest.mark.anyio
async def test_explicit_system_prompt_values_override_discovered_files(tmp_path: Path) -> None:
    tau_home = tmp_path / "tau-home"
    tau_home.mkdir()
    (tau_home / "SYSTEM.md").write_bytes(b"\xff")
    (tau_home / "APPEND_SYSTEM.md").write_bytes(b"\xff")

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            resource_paths=TauResourcePaths(root=tau_home, agents_root=None),
            custom_system_prompt="Explicit base",
            append_system_prompt="Explicit append",
        )
    )

    assert session.system_prompt.startswith("Explicit base\n\nExplicit append")
    assert all(
        "explicit startup value" in item.message
        for item in session.resource_diagnostics
        if item.kind == "system-prompt"
    )


@pytest.mark.anyio
async def test_session_reload_tracks_system_prompt_file_precedence(tmp_path: Path) -> None:
    tau_home = tmp_path / "tau-home"
    project_tau = tmp_path / ".tau"
    tau_home.mkdir()
    project_tau.mkdir()
    user_prompt = tau_home / "SYSTEM.md"
    project_prompt = project_tau / "SYSTEM.md"
    append_prompt = tau_home / "APPEND_SYSTEM.md"
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            trust_default="always",
            resource_paths=TauResourcePaths(root=tau_home, agents_root=None),
        )
    )
    assert "You are an expert coding assistant operating inside Tau" in session.system_prompt

    user_prompt.write_text("User base", encoding="utf-8")
    summary = await session.reload()
    assert summary.system_prompt_rebuilt is True
    assert session.system_prompt.startswith("User base")

    append_prompt.write_text("Reloaded append", encoding="utf-8")
    summary = await session.reload()
    assert summary.system_prompt_rebuilt is True
    assert session.system_prompt.startswith("User base\n\nReloaded append")

    project_prompt.write_text("Project base", encoding="utf-8")
    summary = await session.reload()
    assert summary.system_prompt_rebuilt is True
    assert session.system_prompt.startswith("Project base")

    project_prompt.write_text("Changed project base", encoding="utf-8")
    await session.reload()
    assert session.system_prompt.startswith("Changed project base")

    project_prompt.unlink()
    await session.reload()
    assert session.system_prompt.startswith("User base\n\nReloaded append")

    user_prompt.unlink()
    await session.reload()
    assert session.system_prompt.startswith(
        "You are an expert coding assistant operating inside Tau"
    )
    assert "Reloaded append" in session.system_prompt

    append_prompt.unlink()
    await session.reload()
    assert "Reloaded append" not in session.system_prompt


@pytest.mark.anyio
async def test_failed_system_prompt_file_reload_keeps_previous_prompt(tmp_path: Path) -> None:
    tau_home = tmp_path / "tau-home"
    tau_home.mkdir()
    prompt_path = tau_home / "SYSTEM.md"
    prompt_path.write_text("Valid base", encoding="utf-8")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            resource_paths=TauResourcePaths(root=tau_home, agents_root=None),
        )
    )
    previous = session.system_prompt

    prompt_path.write_bytes(b"\xff")
    with pytest.raises(ResourceError, match="Could not read replacement system prompt file"):
        await session.reload()

    assert session.system_prompt == previous


@pytest.mark.anyio
async def test_new_session_adopts_system_prompt_resource_tracking(tmp_path: Path) -> None:
    tau_home = tmp_path / "tau-home"
    tau_home.mkdir()
    prompt_path = tau_home / "SYSTEM.md"
    prompt_path.write_text("Base A", encoding="utf-8")
    manager = SessionManager(TauPaths(home=tau_home, agents_home=tmp_path / "agents-home"))
    record = manager.create_session(cwd=tmp_path, model="fake")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            storage=JsonlSessionStorage(record.path),
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
            resource_paths=TauResourcePaths(root=tau_home, agents_root=None),
        )
    )

    prompt_path.write_text("Base B", encoding="utf-8")
    await session.new_session()
    assert session.system_prompt.startswith("Base B")

    prompt_path.write_text("Base A", encoding="utf-8")
    summary = await session.reload()

    assert summary.system_prompt_rebuilt is True
    assert session.system_prompt.startswith("Base A")


@pytest.mark.anyio
async def test_session_reload_refreshes_resources_and_system_prompt(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            storage=storage,
            cwd=tmp_path,
            trust_default="always",
            resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
        )
    )
    assert session.skills == ()
    assert session.context_files == ()

    skills_dir = resource_root / "skills" / "testing"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\ndescription: Test code\n---\n# Testing\nRun pytest.",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("Reloaded project rules.", encoding="utf-8")

    entries_before = await storage.read_all()
    command = session.handle_command("/reload")
    assert command.reload_requested is True
    summary = await session.reload()
    entries_after = await storage.read_all()
    _events = await _collect_session_events(session.prompt("Hello"))

    assert summary.skills.after == 1
    assert summary.context_files.after == 1
    assert summary.system_prompt_rebuilt is True
    assert entries_after == entries_before
    assert {skill.name for skill in session.skills} == {"testing"}
    assert [Path(context_file.path).name for context_file in session.context_files] == ["AGENTS.md"]
    assert "Reloaded project rules." in provider.calls[0][1]
    assert "<name>testing</name>" in provider.calls[0][1]


@pytest.mark.anyio
async def test_session_reload_detects_disable_model_invocation_change(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills" / "testing"
    skills_dir.mkdir(parents=True)
    skill_path = skills_dir / "SKILL.md"
    skill_path.write_text(
        "---\ndescription: Test code\n---\n# Testing\nRun pytest.",
        encoding="utf-8",
    )
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            storage=storage,
            cwd=tmp_path,
            trust_default="always",
            resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
        )
    )
    assert "<name>testing</name>" in session.system_prompt

    skill_path.write_text(
        "---\ndescription: Test code\ndisable-model-invocation: true\n---\n# Testing\nRun pytest.",
        encoding="utf-8",
    )
    summary = await session.reload()

    assert summary.system_prompt_rebuilt is True
    assert "<name>testing</name>" not in session.system_prompt
    # The skill stays loaded for explicit /skill:testing invocation.
    assert {skill.name for skill in session.skills} == {"testing"}


@pytest.mark.anyio
async def test_session_reload_skips_provider_settings_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_load_provider_settings(paths: TauPaths | None = None) -> ProviderSettings:
        del paths
        raise AssertionError("/reload should not refresh provider settings")

    monkeypatch.setattr(
        coding_session_module,
        "load_provider_settings",
        fail_load_provider_settings,
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_settings=ProviderSettings(
                providers=(OpenAICompatibleProviderConfig(name="openai"),)
            ),
        )
    )

    command = session.handle_command("/reload")
    assert command.reload_requested is True
    await session.reload()


@pytest.mark.anyio
async def test_session_reload_leaves_system_prompt_when_inputs_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            storage=storage,
            cwd=tmp_path,
        )
    )

    def fail_build_system_prompt(options: object) -> str:
        del options
        raise AssertionError("system prompt should not be rebuilt")

    monkeypatch.setattr(
        coding_session_module,
        "build_system_prompt",
        fail_build_system_prompt,
    )

    command = session.handle_command("/reload")
    assert command.reload_requested is True
    summary = await session.reload()

    assert summary.system_prompt_rebuilt is False


@pytest.mark.anyio
async def test_session_provider_settings_reload_uses_session_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tau_paths = TauPaths(home=tmp_path / "tau-home", agents_home=tmp_path / "agents-home")
    seen_paths: list[TauPaths | None] = []

    def load_provider_settings(paths: TauPaths | None = None) -> ProviderSettings:
        seen_paths.append(paths)
        return ProviderSettings(providers=(OpenAICompatibleProviderConfig(name="openai"),))

    monkeypatch.setattr(coding_session_module, "load_provider_settings", load_provider_settings)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "provider-reload-session.jsonl"),
            cwd=tmp_path,
            provider_settings=ProviderSettings(
                providers=(OpenAICompatibleProviderConfig(name="openai"),)
            ),
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    session.reload_provider_settings()

    assert seen_paths == [tau_paths]


@pytest.mark.anyio
async def test_session_compact_persists_summary_and_rebuilds_context(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Session answer")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Generated session summary")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Next answer")),
            ],
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, storage))
    _events = await _collect_session_events(session.prompt("Explain sessions."))

    message_count_before = len(session.messages)
    message_entries_before = [
        entry.id for entry in await storage.read_all() if entry.type == "message"
    ]

    result = await session.compact("Focus on session persistence.")
    entries_after_compact = await storage.read_all()
    compactions = [entry for entry in entries_after_compact if entry.type == "compaction"]
    leaves = [entry for entry in entries_after_compact if entry.type == "leaf"]

    _next_events = await _collect_session_events(session.prompt("Continue."))

    assert result == f"Compacted {message_count_before} context entries."
    assert len(compactions) == 1
    assert isinstance(compactions[0], CompactionEntry)
    assert compactions[0].summary == "Generated session summary"
    assert compactions[0].replaces_entry_ids == message_entries_before
    assert leaves[-1].entry_id == compactions[0].id
    assert provider.calls[1][1].startswith("You are a context summarization assistant.")
    assert "Additional focus: Focus on session persistence." in provider.calls[1][2][0].content
    _assert_messages(
        provider.calls[2][2],
        [
            UserMessage(content=("Previous conversation summary:\nGenerated session summary")),
            UserMessage(content="Continue."),
        ],
    )


@pytest.mark.anyio
async def test_session_auto_compacts_after_response_when_threshold_is_exceeded(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    large_prompt = "Explain sessions.\n" + ("old context " * 12_000)
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="First answer")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Second answer")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Generated automatic summary")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Third answer")),
            ],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            auto_compact_token_threshold=1,
        )
    )
    _first_events = await _collect_session_events(session.prompt(large_prompt))

    _second_events = await _collect_session_events(session.prompt("Continue."))
    _third_events = await _collect_session_events(session.prompt("Next."))

    entries = await storage.read_all()
    compactions = [entry for entry in entries if entry.type == "compaction"]

    assert len(compactions) == 1
    assert compactions[0].summary == "Generated automatic summary"
    assert "Explain sessions." in provider.calls[2][2][0].content
    _assert_messages(
        provider.calls[3][2],
        [
            UserMessage(content=f"Previous conversation summary:\n{compactions[0].summary}"),
            UserMessage(content="Continue."),
            AssistantMessage(content="Second answer"),
            UserMessage(content="Next."),
        ],
    )


@pytest.mark.anyio
async def test_session_auto_compacts_from_provider_reported_usage(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(
                    message=AssistantMessage(
                        content="First answer",
                        usage=Usage(total_tokens=1_000),
                        timestamp=2_000_000_000_000,
                    )
                ),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(
                    message=AssistantMessage(
                        content="Second answer",
                        usage=Usage(total_tokens=60_000),
                        timestamp=2_000_000_000_001,
                    )
                ),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Provider-usage summary")),
            ],
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            auto_compact_token_threshold=50_000,
        )
    )

    # The first turn is large enough to provide a compaction cut point, while its
    # character estimate remains below the configured 50k threshold.
    await _collect_session_events(session.prompt("First prompt.\n" + ("old " * 25_000)))
    assert session.context_usage.provider_tokens == 1_000
    assert session.has_provider_context_usage is True
    await _collect_session_events(session.prompt("Second short prompt."))

    compactions = [entry for entry in await storage.read_all() if entry.type == "compaction"]
    assert len(compactions) == 1
    assert compactions[0].summary == "Provider-usage summary"


@pytest.mark.anyio
async def test_session_auto_compacts_with_pi_style_default_threshold(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    large_prompt = "Explain sessions.\n" + ("old context " * 12_000)
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="First answer")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Second answer")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Default threshold summary")),
            ],
        ]
    )
    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                models=("fake",),
                default_model="fake",
                context_windows={"fake": 20_000},
            ),
        ),
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            provider_name="local",
            provider_settings=settings,
        )
    )

    assert session.context_window_tokens == 20_000
    assert session.auto_compact_token_threshold == 3_616

    _first_events = await _collect_session_events(session.prompt(large_prompt))
    _second_events = await _collect_session_events(session.prompt("Continue."))

    compactions = [entry for entry in await storage.read_all() if entry.type == "compaction"]

    assert len(compactions) == 1
    assert compactions[0].summary == "Default threshold summary"


@pytest.mark.anyio
async def test_session_uses_live_provider_limits_for_compaction_threshold(
    tmp_path: Path,
) -> None:
    provider = ModelLimitsFakeProvider(
        [],
        limits=RuntimeModelLimits(
            context_window=372_000,
            max_output_tokens=128_000,
            effective_context_window_percent=95,
        ),
    )
    settings = ProviderSettings(
        default_provider="openai-codex",
        providers=(
            OpenAICodexProviderConfig(
                models=("gpt-5.6-sol",),
                default_model="gpt-5.6-sol",
                context_windows={"gpt-5.6-sol": 272_000},
            ),
        ),
    )

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="gpt-5.6-sol",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai-codex",
            provider_settings=settings,
        )
    )

    assert provider.discovery_calls == ["gpt-5.6-sol"]
    assert session.context_window_tokens == 372_000
    assert session.auto_compact_token_threshold == 334_800
    assert session.context_window_source == "provider live catalog"
    assert session.model_limits_discovery_error is None


@pytest.mark.anyio
async def test_session_falls_back_when_live_model_limit_discovery_fails(
    tmp_path: Path,
) -> None:
    provider = ModelLimitsFakeProvider([], error=RuntimeError("catalog unavailable"))
    settings = ProviderSettings(
        default_provider="openai-codex",
        providers=(
            OpenAICodexProviderConfig(
                models=("gpt-5.6-sol",),
                default_model="gpt-5.6-sol",
                context_windows={"gpt-5.6-sol": 272_000},
            ),
        ),
    )

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="gpt-5.6-sol",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai-codex",
            provider_settings=settings,
        )
    )

    assert session.context_window_tokens == 272_000
    assert session.auto_compact_token_threshold == 255_616
    assert session.context_window_source == "configured catalog"
    assert session.model_limits_discovery_error == "RuntimeError: catalog unavailable"


@pytest.mark.anyio
async def test_session_compacts_and_retries_once_after_context_overflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    large_prompt = "Collect context.\n" + ("old context " * 12_000)
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="First answer")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Second answer")),
            ],
            [assistant_error(message="This model's maximum context length was exceeded.")],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Overflow recovery summary")),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Recovered answer")),
            ],
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, storage))
    extension_events = _record_extension_events(session, monkeypatch)
    _first_events = await _collect_session_events(session.prompt(large_prompt))
    _second_events = await _collect_session_events(session.prompt("Keep this recent turn."))
    extension_events.clear()

    retry_events = await _collect_session_events(session.prompt("Trigger overflow."))
    entries = await storage.read_all()
    compactions = [entry for entry in entries if entry.type == "compaction"]

    assert len(compactions) == 1
    assert compactions[0].summary == "Overflow recovery summary"
    assert any(
        getattr(event, "type", None) == "message_end"
        and getattr(getattr(event, "message", None), "text", None) == "Recovered answer"
        for event in retry_events
    )
    assert [message.text for message in provider.calls[4][2][:4]] == [
        "Previous conversation summary:\nOverflow recovery summary",
        "Keep this recent turn.",
        "Second answer",
        "Trigger overflow.",
    ]
    assert len(provider.calls[4][2]) == 4
    overflow_errors = [
        entry.message
        for entry in entries
        if entry.type == "message"
        and isinstance(entry.message, AssistantMessage)
        and entry.message.stop_reason == "error"
    ]
    assert len(overflow_errors) == 1
    extension_event_types = [getattr(event, "type", None) for event in extension_events]
    assert extension_event_types[-2:] == ["auto_retry_end", "agent_settled"]
    assert extension_event_types.count("agent_settled") == 1
    assert sum(isinstance(event, AgentSettledEvent) for event in retry_events) == 1


@pytest.mark.anyio
async def test_huggingface_session_pins_successful_automatic_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[tuple[str | None, object | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        inference_provider: str | None = None,
        response_headers_observer: object | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, thinking_level
        created.append((inference_provider, response_headers_observer))
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    provider_config = OpenAICompatibleProviderConfig(
        name="huggingface",
        models=("zai-org/GLM-5.2",),
        default_model="zai-org/GLM-5.2",
    )
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(
        cwd=tmp_path,
        model="zai-org/GLM-5.2",
        provider_name="huggingface",
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="zai-org/GLM-5.2",
            system="You are Tau.",
            storage=JsonlSessionStorage(record.path),
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
            provider_name="huggingface",
            provider_settings=ProviderSettings(providers=(provider_config,)),
            runtime_provider_config=provider_config,
        )
    )

    observer = created[0][1]
    assert callable(observer)
    original_touch_session = manager.touch_session
    active_provider = session._harness.config.provider

    def fail_touch_session(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise PermissionError("session index is read-only")

    monkeypatch.setattr(manager, "touch_session", fail_touch_session)
    with pytest.raises(PermissionError, match="session index is read-only"):
        observer({"X-Inference-Provider": "deepinfra"})

    assert session.inference_provider is None
    assert session._harness.config.provider is active_provider

    monkeypatch.setattr(manager, "touch_session", original_touch_session)
    observer({"X-Inference-Provider": "deepinfra"})

    assert session.inference_provider == "deepinfra"
    assert created[-1][0] == "deepinfra"
    assert manager.get_session(record.id).inference_provider == "deepinfra"  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_huggingface_session_re_resolves_pin_on_model_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[tuple[str | None, str | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        inference_provider: str | None = None,
        response_headers_observer: object | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, thinking_level, response_headers_observer
        created.append((model, inference_provider))
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    provider_config = OpenAICompatibleProviderConfig(
        name="huggingface",
        models=("zai-org/GLM-5.2", "deepseek-ai/DeepSeek-V4-Flash"),
        default_model="zai-org/GLM-5.2",
        inference_providers={
            "zai-org/GLM-5.2": "deepinfra",
            "deepseek-ai/DeepSeek-V4-Flash": "fireworks-ai",
        },
    )
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(
        cwd=tmp_path,
        model="zai-org/GLM-5.2",
        provider_name="huggingface",
        inference_provider="deepinfra",
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="zai-org/GLM-5.2",
            system="You are Tau.",
            storage=JsonlSessionStorage(record.path),
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
            provider_name="huggingface",
            inference_provider="deepinfra",
            provider_settings=ProviderSettings(providers=(provider_config,)),
            runtime_provider_config=provider_config,
            resource_paths=TauResourcePaths(
                root=tmp_path / ".tau",
                paths=TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"),
            ),
        )
    )

    session.set_model("deepseek-ai/DeepSeek-V4-Flash")

    assert created == [
        ("zai-org/GLM-5.2", "deepinfra"),
        ("deepseek-ai/DeepSeek-V4-Flash", "fireworks-ai"),
    ]
    assert session.model == "deepseek-ai/DeepSeek-V4-Flash"
    assert session.inference_provider == "fireworks-ai"
    assert manager.get_session(record.id).inference_provider == "fireworks-ai"  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_startup_model_override_rebuilds_model_dependent_runtime_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[tuple[str | None, str | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        inference_provider: str | None = None,
        response_headers_observer: object | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, thinking_level, response_headers_observer
        created.append((model, inference_provider))
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    old_model = "zai-org/GLM-5.2"
    override_model = "deepseek-ai/DeepSeek-V4-Flash"
    provider_config = OpenAICompatibleProviderConfig(
        name="huggingface",
        models=(old_model, override_model),
        default_model=old_model,
        inference_providers={old_model: "deepinfra", override_model: "fireworks-ai"},
    )
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    await storage.append(ModelChangeEntry(model=old_model))
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model=override_model,
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            provider_name="huggingface",
            inference_provider="fireworks-ai",
            provider_settings=ProviderSettings(providers=(provider_config,)),
            runtime_provider_config=provider_config,
            resource_paths=TauResourcePaths(
                root=tmp_path / ".tau",
                paths=TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"),
            ),
        )
    )

    await session.apply_startup_model_override(override_model)

    assert session.model == override_model
    assert session.inference_provider == "fireworks-ai"
    assert created == [
        (old_model, "fireworks-ai"),
        (override_model, "fireworks-ai"),
    ]
    assert session._harness.config.provider is session._owned_providers[-1]


@pytest.mark.anyio
async def test_session_switches_configured_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created_providers: list[SwitchableFakeProvider] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, model, thinking_level
        provider = SwitchableFakeProvider(provider_config)
        created_providers.append(provider)
        return provider

    isolate_home(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(name="openai"),
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen", "llama"),
                default_model="qwen",
            ),
        ),
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=settings,
        )
    )

    session.set_provider("local")

    assert session.provider_name == "local"
    assert session.model == "qwen"
    assert session.available_models == ("qwen", "llama")
    assert [(choice.provider_name, choice.model) for choice in session.available_model_choices] == [
        ("local", "qwen"),
        ("local", "llama"),
    ]
    assert len(created_providers) == 1

    session.set_provider("local")

    assert len(created_providers) == 2

    await session.aclose()

    assert [provider.closed for provider in created_providers] == [True, True]


@pytest.mark.anyio
async def test_session_switch_uses_session_credential_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    tau_paths = TauPaths(home=tmp_path / "tau-home", agents_home=tmp_path / "agents-home")
    FileCredentialStore(tau_paths.home / "credentials.json").set("openai", "stored-key")
    credential_store_paths: list[Path] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del provider_config, model, thinking_level
        assert credential_store is not None
        credential_store_paths.append(credential_store.path)
        return SwitchableFakeProvider(object())

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                api_key_env="LOCAL_API_KEY",
                credential_name=None,
                models=("qwen",),
                default_model="qwen",
            ),
            OpenAICompatibleProviderConfig(name="openai", credential_name="openai"),
        ),
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "switch-store-session.jsonl"),
            cwd=tmp_path,
            provider_name="local",
            provider_settings=settings,
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    session.set_provider("openai")

    assert credential_store_paths == [tau_paths.home / "credentials.json"]


@pytest.mark.anyio
async def test_available_model_choices_hide_unusable_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LOCAL_API_KEY", "local-key")
    tau_paths = TauPaths(home=tmp_path / "tau-home", agents_home=tmp_path / "agents-home")
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(name="openai"),
            OpenAICompatibleProviderConfig(
                name="local",
                api_key_env="LOCAL_API_KEY",
                credential_name=None,
                models=("qwen", "llama"),
                default_model="qwen",
            ),
        ),
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=settings,
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    assert session.available_models == ()
    assert session.available_providers == ("local",)
    assert [(choice.provider_name, choice.model) for choice in session.available_model_choices] == [
        ("local", "qwen"),
        ("local", "llama"),
    ]


@pytest.mark.anyio
async def test_available_model_choices_include_stored_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    tau_paths = TauPaths(home=tmp_path / "tau-home", agents_home=tmp_path / "agents-home")
    FileCredentialStore(tau_paths.home / "credentials.json").set("openai", "stored-key")
    settings = ProviderSettings(
        default_provider="openai",
        providers=(OpenAICompatibleProviderConfig(name="openai", credential_name="openai"),),
    )

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "stored-session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=settings,
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    assert session.available_providers == ("openai",)
    assert ("openai", "gpt-5.4") in [
        (choice.provider_name, choice.model) for choice in session.available_model_choices
    ]


@pytest.mark.anyio
async def test_session_toggles_and_cycles_scoped_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "local-key")
    tau_paths = TauPaths(home=tmp_path / "tau-home", agents_home=tmp_path / "agents-home")
    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                api_key_env="LOCAL_API_KEY",
                credential_name=None,
                models=("qwen", "llama"),
                default_model="qwen",
            ),
        ),
        scoped_models=(ScopedModelConfig(provider="local", model="qwen"),),
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="qwen",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "scoped-session.jsonl"),
            cwd=tmp_path,
            provider_name="local",
            provider_settings=settings,
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    llama = ModelChoice(provider_name="local", model="llama")
    scoped = session.toggle_scoped_model(llama)
    choice = session.cycle_scoped_model()
    saved = json.loads((tau_paths.home / "providers.json").read_text(encoding="utf-8"))

    assert [(item.provider_name, item.model) for item in scoped] == [
        ("local", "qwen"),
        ("local", "llama"),
    ]
    assert choice == llama
    assert session.model == "llama"
    assert saved["scoped_models"] == [
        {"provider": "local", "model": "qwen"},
        {"provider": "local", "model": "llama"},
    ]


@requires_posix_shell
@pytest.mark.anyio
async def test_session_resume_preserves_shell_command_prefix(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    first_record = manager.create_session(cwd=first_cwd, model="fake", title="First")
    second_record = manager.create_session(cwd=second_cwd, model="fake", title="Second")
    second_storage = JsonlSessionStorage(second_record.path)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(first_record.path),
            cwd=first_record.cwd,
            session_id=first_record.id,
            session_manager=manager,
            shell_command_prefix="shopt -s expand_aliases\nalias greet='printf resumed-alias'",
        )
    )
    await second_storage.append(SessionInfoEntry(cwd=str(second_record.cwd)))
    await second_storage.append(ModelChangeEntry(model="fake"))

    await session.resume(second_record.id)
    result = await session.run_terminal_command("greet", add_to_context=False)

    assert result.output == "resumed-alias"


@pytest.mark.anyio
async def test_session_resumes_indexed_session(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    first_cwd = tmp_path / "first"
    first_cwd.mkdir()
    first_record = manager.create_session(cwd=first_cwd, model="fake", title="First")
    second_cwd = tmp_path / "second"
    second_cwd.mkdir(parents=True)
    second_record = manager.create_session(cwd=second_cwd, model="fake", title="Second")
    first_storage = JsonlSessionStorage(first_record.path)
    second_storage = JsonlSessionStorage(second_record.path)
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Second answer")),
            ]
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=first_storage,
            cwd=first_record.cwd,
            session_id=first_record.id,
            session_manager=manager,
        )
    )
    await second_storage.append(SessionInfoEntry(cwd=str(second_record.cwd)))
    await second_storage.append(ModelChangeEntry(model="fake"))
    await second_storage.append(MessageEntry(message=UserMessage(content="Earlier")))
    await second_storage.append(MessageEntry(message=AssistantMessage(content="Restored")))

    message = await session.resume(second_record.id)
    _events = await _collect_session_events(session.prompt("Continue."))

    assert message == f"Resumed session: {second_record.id}"
    assert session.session_id == second_record.id
    assert session.cwd == second_record.cwd
    assert [item.text for item in session.messages[:2]] == ["Earlier", "Restored"]
    _assert_messages(
        provider.calls[0][2],
        [
            UserMessage(content="Earlier"),
            AssistantMessage(content="Restored"),
            UserMessage(content="Continue."),
        ],
    )
    # The replacement session's persistence listener must be detached on
    # adoption, or every message after resume is persisted twice.
    entries = await second_storage.read_all()
    persisted_texts = [entry.message.text for entry in entries if entry.type == "message"]
    assert persisted_texts == ["Earlier", "Restored", "Continue.", "Second answer"]


@pytest.mark.anyio
async def test_session_toggle_scoped_model_preserves_newer_provider_file_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "local-key")
    monkeypatch.setenv("REMOTE_API_KEY", "remote-key")
    tau_paths = TauPaths(home=tmp_path / "tau-home", agents_home=tmp_path / "agents-home")
    loaded_settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                api_key_env="LOCAL_API_KEY",
                models=("qwen", "llama"),
                default_model="qwen",
            ),
        ),
        scoped_models=(ScopedModelConfig(provider="local", model="qwen"),),
    )
    newer_settings = ProviderSettings(
        default_provider="local",
        providers=(
            loaded_settings.get_provider("local"),
            OpenAICompatibleProviderConfig(
                name="remote",
                api_key_env="REMOTE_API_KEY",
                models=("sonnet",),
                default_model="sonnet",
            ),
        ),
        scoped_models=(
            ScopedModelConfig(provider="local", model="qwen"),
            ScopedModelConfig(provider="remote", model="sonnet"),
        ),
    )
    save_provider_settings(newer_settings, tau_paths)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="qwen",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "scoped-session.jsonl"),
            cwd=tmp_path,
            provider_name="local",
            provider_settings=loaded_settings,
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    session.toggle_scoped_model(ModelChoice(provider_name="local", model="llama"))

    saved = coding_session_module.load_provider_settings(tau_paths)
    assert saved.get_provider("remote").default_model == "sonnet"
    assert saved.scoped_models == (
        ScopedModelConfig(provider="local", model="qwen"),
        ScopedModelConfig(provider="remote", model="sonnet"),
        ScopedModelConfig(provider="local", model="llama"),
    )


@pytest.mark.anyio
async def test_session_set_model_rejects_model_not_declared_for_provider(tmp_path: Path) -> None:
    provider_config = OpenAICompatibleProviderConfig(
        name="openai",
        models=("gpt-5",),
        default_model="gpt-5",
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=ProviderSettings(providers=(provider_config,)),
        )
    )

    with pytest.raises(
        coding_session_module.ProviderConfigError,
        match="Model is not configured for provider openai: gpt-5.5",
    ):
        session.set_model("gpt-5.5")

    assert session.model == "gpt-5"


@pytest.mark.anyio
async def test_session_load_falls_back_when_persisted_model_does_not_match_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created: list[tuple[str, str | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, thinking_level
        created.append((provider_config.name, model))  # type: ignore[attr-defined]
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    await storage.append(SessionInfoEntry(cwd=str(tmp_path)))
    await storage.append(ModelChangeEntry(model="gpt-5"))
    provider_config = OpenAICodexProviderConfig(
        models=("gpt-5.5",),
        default_model="gpt-5.5",
    )

    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5.5",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
            provider_name="openai-codex",
            provider_settings=ProviderSettings(
                default_provider="openai-codex",
                providers=(provider_config,),
            ),
            runtime_provider_config=provider_config,
        )
    )

    assert session.state.model == "gpt-5"
    assert session.model == "gpt-5.5"
    assert created == [("openai-codex", "gpt-5.5")]


@pytest.mark.anyio
async def test_session_set_model_persists_default_provider_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    isolate_home(monkeypatch, tmp_path)
    tau_paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    provider_config = OpenAICompatibleProviderConfig(
        name="openai",
        models=("gpt-5", "gpt-5-mini"),
        default_model="gpt-5",
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=ProviderSettings(providers=(provider_config,)),
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    session.set_model("gpt-5-mini")

    saved = coding_session_module.load_provider_settings(tau_paths)
    assert saved.default_provider == "openai"
    assert saved.get_provider("openai").default_model == "gpt-5-mini"


@pytest.mark.anyio
async def test_session_set_model_choice_persists_default_provider_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    isolate_home(monkeypatch, tmp_path)
    tau_paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen", "llama"),
                default_model="qwen",
            ),
        ),
    )
    created: list[tuple[str, str | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, thinking_level
        created.append((provider_config.name, model))  # type: ignore[attr-defined]
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=settings,
            runtime_provider_config=settings.get_provider("openai"),
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )
    created.clear()

    session.set_model_choice(ModelChoice(provider_name="local", model="llama"))

    saved = coding_session_module.load_provider_settings(tau_paths)
    assert saved.default_provider == "local"
    assert saved.get_provider("local").default_model == "llama"
    assert created == [("local", "llama")]


@pytest.mark.anyio
async def test_session_set_model_choice_switches_provider_model_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    isolate_home(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_API_KEY", "local-key")
    tau_paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen", "llama"),
                default_model="qwen",
            ),
        ),
        scoped_models=(
            ScopedModelConfig(provider="openai", model="gpt-5"),
            ScopedModelConfig(provider="local", model="llama"),
        ),
    )
    created: list[tuple[str, str | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, thinking_level
        created.append((provider_config.name, model))  # type: ignore[attr-defined]
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=settings,
            runtime_provider_config=settings.get_provider("openai"),
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )
    created.clear()

    choice = session.cycle_scoped_model()

    assert choice == ModelChoice(provider_name="local", model="llama")
    assert session.provider_name == "local"
    assert session.model == "llama"
    assert created == [("local", "llama")]


@pytest.mark.anyio
async def test_session_set_model_preserves_newer_provider_file_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    isolate_home(monkeypatch, tmp_path)
    tau_paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    loaded_provider = OpenAICompatibleProviderConfig(
        name="openai",
        models=("gpt-5", "gpt-5-mini"),
        default_model="gpt-5",
    )
    newer_settings = ProviderSettings(
        default_provider="openai",
        providers=(
            loaded_provider,
            OpenAICompatibleProviderConfig(
                name="openrouter",
                api_key_env="OPENROUTER_API_KEY",
                credential_name="openrouter",
                models=("openai/gpt-5.5",),
                default_model="openai/gpt-5.5",
                headers={"X-Title": "Tau"},
            ),
        ),
        scoped_models=(ScopedModelConfig(provider="openrouter", model="openai/gpt-5.5"),),
    )
    save_provider_settings(newer_settings, tau_paths)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="openai",
            provider_settings=ProviderSettings(providers=(loaded_provider,)),
            resource_paths=TauResourcePaths(root=tau_paths.home, paths=tau_paths),
        )
    )

    session.set_model("gpt-5-mini")

    saved = coding_session_module.load_provider_settings(tau_paths)
    assert saved.get_provider("openai").default_model == "gpt-5-mini"
    assert saved.get_provider("openrouter").headers == {"X-Title": "Tau"}
    assert saved.scoped_models == (
        ScopedModelConfig(provider="openrouter", model="openai/gpt-5.5"),
    )


@pytest.mark.anyio
async def test_session_new_session_uses_default_provider_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    current_record = manager.create_session(
        cwd=tmp_path,
        model="openai/gpt-5.5",
        provider_name="openrouter",
    )
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
            OpenAICompatibleProviderConfig(
                name="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key_env="OPENROUTER_API_KEY",
                models=("openai/gpt-5.5",),
                default_model="openai/gpt-5.5",
            ),
        ),
    )
    created: list[tuple[str, str | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, thinking_level
        created.append((provider_config.name, model))  # type: ignore[attr-defined]
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="openai/gpt-5.5",
            system="You are Tau.",
            storage=JsonlSessionStorage(current_record.path),
            cwd=current_record.cwd,
            session_id=current_record.id,
            session_manager=manager,
            provider_name="openrouter",
            provider_settings=settings,
            runtime_provider_config=settings.get_provider("openrouter"),
        )
    )
    created.clear()

    message = await session.new_session()

    assert message.startswith("Started new session: ")
    assert session.provider_name == "openai"
    assert session.model == "gpt-5"
    assert manager.get_session(session.session_id) is None
    assert created == [("openai", "gpt-5")]


@pytest.mark.anyio
async def test_session_new_session_is_indexed_after_first_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    current_record = manager.create_session(cwd=tmp_path, model="fake", provider_name="fake")
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
        ),
    )

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> FakeProvider:
        del provider_config, credential_store, model, thinking_level
        return FakeProvider(
            [
                [
                    assistant_start(model="gpt-5"),
                    assistant_done(message=AssistantMessage(content="Greeting")),
                ],
                [
                    assistant_start(model="gpt-5"),
                    assistant_done(message=AssistantMessage(content="Done")),
                ],
            ]
        )

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(current_record.path),
            cwd=current_record.cwd,
            session_id=current_record.id,
            session_manager=manager,
            provider_name="fake",
            provider_settings=settings,
        )
    )

    _message = await session.new_session()
    pending_id = session.session_id

    assert pending_id is not None
    assert manager.get_session(pending_id) is None
    assert all(record.id != pending_id for record in manager.list_sessions(tmp_path))

    _events = await _collect_session_events(session.prompt("Hello"))

    indexed = manager.get_session(pending_id)
    assert indexed is not None
    assert indexed.provider_name == "openai"
    assert indexed.model == "gpt-5"
    assert indexed.title == "Greeting"
    assert indexed.path.exists()


@pytest.mark.anyio
async def test_session_name_indexes_pending_session_without_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    current_record = manager.create_session(cwd=tmp_path, model="fake", provider_name="fake")
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
        ),
    )

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> FakeProvider:
        del provider_config, credential_store, model, thinking_level
        return FakeProvider([])

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(current_record.path),
            cwd=current_record.cwd,
            session_id=current_record.id,
            session_manager=manager,
            provider_name="fake",
            provider_settings=settings,
        )
    )

    _message = await session.new_session()
    pending_id = session.session_id

    assert pending_id is not None
    assert manager.get_session(pending_id) is None

    result = session.handle_command("/name Customer bugfix")

    indexed = manager.get_session(pending_id)
    assert result.message == "Session renamed: Customer bugfix"
    assert indexed is not None
    assert indexed.title == "Customer bugfix"
    assert indexed.provider_name == "openai"
    assert indexed.model == "gpt-5"
    assert indexed.path.exists()
    assert await JsonlSessionStorage(indexed.path).read_all()


@pytest.mark.anyio
async def test_session_resume_uses_target_session_provider_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    first_cwd = tmp_path / "first"
    first_cwd.mkdir()
    first_record = manager.create_session(
        cwd=first_cwd,
        model="gpt-5",
        provider_name="openai",
        title="First",
    )
    second_cwd = tmp_path / "second"
    second_cwd.mkdir(parents=True)
    second_record = manager.create_session(
        cwd=second_cwd,
        model="qwen",
        provider_name="local",
        title="Second",
    )
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen",),
                default_model="qwen",
            ),
        ),
    )
    created: list[tuple[str, str | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, thinking_level
        created.append((provider_config.name, model))  # type: ignore[attr-defined]
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    second_storage = JsonlSessionStorage(second_record.path)
    await second_storage.append(SessionInfoEntry(cwd=str(second_record.cwd)))
    await second_storage.append(ModelChangeEntry(model="qwen"))
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5",
            system="You are Tau.",
            storage=JsonlSessionStorage(first_record.path),
            cwd=first_record.cwd,
            session_id=first_record.id,
            session_manager=manager,
            provider_name="openai",
            provider_settings=settings,
            runtime_provider_config=settings.get_provider("openai"),
        )
    )
    created.clear()

    await session.resume(second_record.id)

    assert session.provider_name == "local"
    assert session.model == "qwen"
    assert created == [("local", "qwen")]


@pytest.mark.anyio
async def test_session_resume_missing_provider_preserves_active_provider_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    first_cwd = tmp_path / "first"
    first_cwd.mkdir()
    first_record = manager.create_session(
        cwd=first_cwd,
        model="gpt-5",
        provider_name="openai",
        title="First",
    )
    second_cwd = tmp_path / "second"
    second_cwd.mkdir(parents=True)
    second_record = manager.create_session(
        cwd=second_cwd,
        model="qwen",
        provider_name=None,
        title="Legacy second",
    )
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen",),
                default_model="qwen",
            ),
        ),
    )
    created: list[tuple[str, str | None]] = []

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, thinking_level
        created.append((provider_config.name, model))  # type: ignore[attr-defined]
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    second_storage = JsonlSessionStorage(second_record.path)
    await second_storage.append(SessionInfoEntry(cwd=str(second_record.cwd)))
    await second_storage.append(ModelChangeEntry(model="qwen"))
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5",
            system="You are Tau.",
            storage=JsonlSessionStorage(first_record.path),
            cwd=first_record.cwd,
            session_id=first_record.id,
            session_manager=manager,
            provider_name="openai",
            provider_settings=settings,
            runtime_provider_config=settings.get_provider("openai"),
        )
    )
    created.clear()

    await session.resume(second_record.id)

    assert session.provider_name == "openai"
    assert session.model == "gpt-5"
    assert created == [("openai", "gpt-5"), ("openai", "gpt-5")]


@pytest.mark.anyio
async def test_session_resume_rejects_incompatible_provider_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    first_cwd = tmp_path / "first"
    first_cwd.mkdir()
    first_record = manager.create_session(
        cwd=first_cwd,
        model="gpt-5",
        provider_name="openai",
        title="First",
    )
    second_cwd = tmp_path / "second"
    second_cwd.mkdir(parents=True)
    second_record = manager.create_session(
        cwd=second_cwd,
        model="gpt-5.5",
        provider_name="local",
        title="Bad second",
    )
    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen",),
                default_model="qwen",
            ),
        ),
    )

    def create_provider(
        provider_config: object,
        *,
        credential_store: FileCredentialStore | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> SwitchableFakeProvider:
        del credential_store, model, thinking_level
        return SwitchableFakeProvider(provider_config)

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="gpt-5",
            system="You are Tau.",
            storage=JsonlSessionStorage(first_record.path),
            cwd=first_record.cwd,
            session_id=first_record.id,
            session_manager=manager,
            provider_name="openai",
            provider_settings=settings,
            runtime_provider_config=settings.get_provider("openai"),
        )
    )

    with pytest.raises(
        ProviderConfigError,
        match="Model is not configured for provider local: gpt-5.5",
    ):
        await session.resume(second_record.id)

    assert session.provider_name == "openai"
    assert session.model == "gpt-5"


@pytest.mark.anyio
async def test_session_context_usage_recalculates_after_resume(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    first_cwd = tmp_path / "first"
    first_cwd.mkdir()
    first_record = manager.create_session(cwd=first_cwd, model="fake", title="First")
    second_cwd = tmp_path / "second"
    second_cwd.mkdir(parents=True)
    second_record = manager.create_session(cwd=second_cwd, model="fake", title="Second")
    first_storage = JsonlSessionStorage(first_record.path)
    second_storage = JsonlSessionStorage(second_record.path)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=first_storage,
            cwd=first_record.cwd,
            session_id=first_record.id,
            session_manager=manager,
        )
    )
    before_resume_usage = session.context_usage
    await second_storage.append(SessionInfoEntry(cwd=str(second_record.cwd)))
    await second_storage.append(ModelChangeEntry(model="fake"))
    await second_storage.append(MessageEntry(message=UserMessage(content="Earlier " * 20)))
    await second_storage.append(MessageEntry(message=AssistantMessage(content="Restored " * 20)))

    _message = await session.resume(second_record.id)
    after_resume_usage = session.context_usage

    assert before_resume_usage.message_count == 0
    assert after_resume_usage.message_count == 2
    assert after_resume_usage.total_tokens > before_resume_usage.total_tokens
    assert session.context_token_estimate == after_resume_usage.total_tokens


def test_custom_prompt_template_retains_precedence_over_other_commands(tmp_path: Path) -> None:
    session = CodingSession(
        _config(tmp_path, FakeProvider([]), JsonlSessionStorage(tmp_path / "session.jsonl")),
        state=object(),  # type: ignore[arg-type]
        harness=object(),  # type: ignore[arg-type]
        last_parent_id=None,
        prompt_templates=(
            PromptTemplate(name="new", path=tmp_path / "new.md", content="Custom workflow"),
        ),
    )

    result = session.handle_command("/new")

    assert result.handled is False
    assert session.expand_prompt_text("/new") == "Custom workflow"


def test_minimal_commands_are_handled(tmp_path: Path) -> None:
    session = CodingSession(
        _config(tmp_path, FakeProvider([]), JsonlSessionStorage(tmp_path / "session.jsonl")),
        state=object(),  # type: ignore[arg-type]
        harness=object(),  # type: ignore[arg-type]
        last_parent_id=None,
    )

    assert session.handle_command("hello").handled is False
    assert session.handle_command("/new").new_session_requested is True
    assert session.handle_command("/clear").handled is False
    assert session.handle_command("/quit").exit_requested is True
    assert session.handle_command("/exit").exit_requested is True
    assert session.handle_command("/unknown").handled is False
