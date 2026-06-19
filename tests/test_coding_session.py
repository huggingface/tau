import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tau_agent import (
    AgentMessage,
    AgentTool,
    AssistantMessage,
    QueueUpdateEvent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.session import (
    CompactionEntry,
    JsonlSessionStorage,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionInfoEntry,
    ThinkingLevelChangeEntry,
)
from tau_ai import (
    CancellationToken,
    FakeProvider,
    ModelProvider,
    ProviderEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
)
from tau_coding import (
    CodingSession,
    CodingSessionConfig,
    FileCredentialStore,
    ModelChoice,
    OpenAICompatibleProviderConfig,
    ProviderSettings,
    ScopedModelConfig,
    SessionManager,
    TauPaths,
    TauResourcePaths,
)
from tau_coding import session as coding_session_module
from tau_coding.session import parse_terminal_command


async def _collect_session_events(session_stream: object) -> list[object]:
    return [event async for event in session_stream]  # type: ignore[attr-defined]


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


class RaisingProvider:
    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        del model, system, messages, tools, signal

        async def iterator() -> AsyncIterator[ProviderEvent]:
            raise RuntimeError("provider exploded")
            yield  # pragma: no cover

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
    ) -> AsyncIterator[ProviderEvent]:
        del model, system, tools, signal
        call_index = self.call_count
        self.call_count += 1
        self.calls.append(list(messages))

        async def iterator() -> AsyncIterator[ProviderEvent]:
            if call_index == 0:
                yield ProviderResponseStartEvent(model="fake")
                self.started.set()
                await self.release.wait()
                yield ProviderResponseEndEvent(message=AssistantMessage(content="First"))
                return
            yield ProviderResponseStartEvent(model="fake")
            yield ProviderResponseEndEvent(message=AssistantMessage(content="Second"))

        return iterator()


@pytest.mark.anyio
async def test_load_empty_session_appends_metadata(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")

    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    entries = await storage.read_all()
    assert isinstance(entries[0], SessionInfoEntry)
    assert entries[0].cwd == str(tmp_path)
    assert entries[1] == ModelChangeEntry(
        id=entries[1].id, parent_id=entries[0].id, model="fake", timestamp=entries[1].timestamp
    )
    assert entries[2] == ThinkingLevelChangeEntry(
        id=entries[2].id,
        parent_id=entries[1].id,
        thinking_level="medium",
        timestamp=entries[2].timestamp,
    )
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


@pytest.mark.anyio
async def test_session_export_writes_jsonl_to_destination_directory(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / ".tau" / "sessions" / "session-1.jsonl")
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))
    await storage.append(MessageEntry(id="root", message=UserMessage(content="Export me")))

    output_path = await session.export(Path("exports"), format="jsonl")

    assert output_path == tmp_path / "exports" / "session-1.jsonl"
    assert "Export me" in output_path.read_text(encoding="utf-8")


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
async def test_prompt_persists_user_assistant_and_leaf_entries(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Hi")),
            ]
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    _events = await _collect_session_events(session.prompt("Hello"))

    entries = await storage.read_all()
    message_entries = [entry for entry in entries if entry.type == "message"]
    assert [entry.message for entry in message_entries] == [
        UserMessage(content="Hello"),
        AssistantMessage(content="Hi"),
    ]
    assert entries[-1].type == "leaf"
    assert entries[-1].entry_id == message_entries[-1].id
    assert session.messages == (UserMessage(content="Hello"), AssistantMessage(content="Hi"))


@pytest.mark.anyio
async def test_terminal_command_can_persist_output_to_context(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    session = await CodingSession.load(_config(tmp_path, FakeProvider([]), storage))

    result = await session.run_terminal_command("printf hello", add_to_context=True)

    assert result.ok is True
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

    assert result.ok is True
    assert result.output == "hidden"
    assert result.added_to_context is False
    entries = await storage.read_all()
    assert not any(isinstance(entry, MessageEntry) for entry in entries)


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
    assert [entry.type for entry in entries_before_release] == [
        "session_info",
        "model_change",
        "thinking_level_change",
    ]
    assert session.messages == (
        UserMessage(content="Hello"),
        AssistantMessage(content="First"),
        UserMessage(content="Queued steering"),
        AssistantMessage(content="Second"),
    )
    assert provider.calls[1] == list(session.messages[:3])
    entries = await storage.read_all()
    message_entries = [entry for entry in entries if entry.type == "message"]
    assert [entry.message for entry in message_entries] == list(session.messages)
    assert any(isinstance(event, QueueUpdateEvent) for event in run_events)


@pytest.mark.anyio
async def test_context_usage_recalculates_after_prompt_and_compaction(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(
                    message=AssistantMessage(content="Long answer " * 80),
                ),
            ]
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
async def test_session_uses_active_model_thinking_capabilities(tmp_path: Path) -> None:
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
    assert await session.set_thinking_level("high") == "Thinking mode: high"

    with pytest.raises(ValueError, match="not available"):
        await session.set_thinking_level("medium")

    session.set_model("plain")

    assert session.available_thinking_levels == ()
    with pytest.raises(ValueError, match="unavailable"):
        await session.cycle_thinking_level()


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

    assert session.messages == (
        UserMessage(content="Earlier"),
        AssistantMessage(content="Restored"),
    )


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

    assert session.messages == (
        UserMessage(content="Root"),
        AssistantMessage(content="Active branch"),
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
    assert result == "Branched session at left."
    assert session.messages == (UserMessage(content="Root"), AssistantMessage(content="Left"))
    assert [entry.id for entry in entries if entry.type == "message"] == ["root", "left", "right"]
    assert isinstance(entries[-1], LeafEntry)
    assert entries[-1].entry_id == "left"


@pytest.mark.anyio
async def test_session_branch_restores_model_from_selected_path(tmp_path: Path) -> None:
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
    assert session.model == "first-model"


@pytest.mark.anyio
async def test_session_branch_with_summary_rebuilds_context(tmp_path: Path) -> None:
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

    assert "with branch summary" in result
    assert summary.type == "branch_summary"
    assert summary.parent_id == "root"
    assert summary.branch_root_id == "root"
    assert session.messages[0] == UserMessage(content="Root")
    assert session.messages[1].role == "user"
    assert isinstance(session.messages[1].content, str)
    assert "Previous branch summary from root:" in session.messages[1].content
    assert "Abandoned follow-up" in session.messages[1].content


@pytest.mark.anyio
async def test_continue_persists_only_new_messages(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    await storage.append(MessageEntry(id="user", message=UserMessage(content="Continue me")))
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Continued")),
            ]
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, storage))

    _events = await _collect_session_events(session.continue_())

    entries = await storage.read_all()
    message_entries = [entry for entry in entries if entry.type == "message"]
    assert [entry.message for entry in message_entries] == [
        UserMessage(content="Continue me"),
        AssistantMessage(content="Continued"),
    ]


@pytest.mark.anyio
async def test_tool_results_are_persisted(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    tool_call = ToolCall(id="call-1", name="missing", arguments={})
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(
                    message=AssistantMessage(content="Using tool", tool_calls=[tool_call]),
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
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
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
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
    (skills_dir / "testing.md").write_text(
        "---\ndescription: Test code\n---\n# Testing",
        encoding="utf-8",
    )
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    config = CodingSessionConfig(
        provider=provider,
        model="fake",
        storage=storage,
        cwd=tmp_path,
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
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
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
async def test_session_loads_and_expands_skills(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "testing.md").write_text("# Testing\nRun pytest.", encoding="utf-8")
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
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

    _events = await _collect_session_events(session.prompt("/skill:testing add tests"))

    assert [skill.name for skill in session.skills] == ["testing"]
    assert '<skill name="testing" location="' in provider.calls[0][2][0].content
    assert "References are relative to" in provider.calls[0][2][0].content
    assert provider.calls[0][2][0].content.endswith("</skill>\n\nadd tests")
    assert session.handle_command("/skill:testing").handled is False


@pytest.mark.anyio
async def test_session_skill_index_lets_agent_read_relevant_skill_file(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills"
    skills_dir.mkdir(parents=True)
    skill_path = skills_dir / "testing.md"
    skill_path.write_text(
        "---\ndescription: Use when writing tests\n---\n# Testing\nRun pytest.",
        encoding="utf-8",
    )
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": str(skill_path)})
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(
                    message=AssistantMessage(content="Reading skill.", tool_calls=[tool_call]),
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Skill applied.")),
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
    assert tool_result.name == "read"
    assert tool_result.ok is True
    assert "# Testing\nRun pytest." in tool_result.content
    assert tool_result.data is not None
    assert tool_result.data["path"] == str(skill_path)


@pytest.mark.anyio
async def test_session_loads_with_resource_diagnostics_instead_of_failing(
    tmp_path: Path,
) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills"
    (skills_dir / "dup").mkdir(parents=True)
    (skills_dir / "dup" / "SKILL.md").write_text("# Directory skill", encoding="utf-8")
    (skills_dir / "dup.md").write_text("# File skill", encoding="utf-8")
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

    assert [skill.name for skill in session.skills] == ["dup"]
    assert len(session.resource_diagnostics) == 1
    assert "Duplicate skill name" in session.resource_diagnostics[0].message
    assert "Resource diagnostics: 1" in (session.handle_command("/session").message or "")


@pytest.mark.anyio
async def test_session_reload_refreshes_resources_and_system_prompt(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            storage=storage,
            cwd=tmp_path,
            resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
        )
    )
    assert session.skills == ()
    assert session.context_files == ()

    skills_dir = resource_root / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "testing.md").write_text(
        "---\ndescription: Test code\n---\n# Testing\nRun pytest.",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("Reloaded project rules.", encoding="utf-8")

    result = session.handle_command("/reload")
    _events = await _collect_session_events(session.prompt("Hello"))

    assert result.message is not None
    assert "Skills: 1" in result.message
    assert "Context files: 1" in result.message
    assert [skill.name for skill in session.skills] == ["testing"]
    assert [Path(context_file.path).name for context_file in session.context_files] == ["AGENTS.md"]
    assert "Reloaded project rules." in provider.calls[0][1]
    assert "<name>testing</name>" in provider.calls[0][1]


@pytest.mark.anyio
async def test_session_compact_persists_summary_and_rebuilds_context(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Session answer")),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Next answer")),
            ],
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, storage))
    _events = await _collect_session_events(session.prompt("Explain sessions."))

    message_count_before = len(session.messages)
    message_entries_before = [
        entry.id for entry in await storage.read_all() if entry.type == "message"
    ]

    result = await session.compact("The user asked about sessions and got an explanation.")
    entries_after_compact = await storage.read_all()
    compactions = [entry for entry in entries_after_compact if entry.type == "compaction"]
    leaves = [entry for entry in entries_after_compact if entry.type == "leaf"]

    _next_events = await _collect_session_events(session.prompt("Continue."))

    assert result == f"Compacted {message_count_before} context entries."
    assert len(compactions) == 1
    assert isinstance(compactions[0], CompactionEntry)
    assert compactions[0].replaces_entry_ids == message_entries_before
    assert leaves[-1].entry_id == compactions[0].id
    assert provider.calls[1][2] == [
        UserMessage(
            content=(
                "Previous conversation summary:\n"
                "The user asked about sessions and got an explanation."
            )
        ),
        UserMessage(content="Continue."),
    ]


@pytest.mark.anyio
async def test_session_auto_compacts_before_prompt_when_threshold_is_exceeded(
    tmp_path: Path,
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="First answer")),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Second answer")),
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
    _first_events = await _collect_session_events(session.prompt("Explain sessions."))

    _second_events = await _collect_session_events(session.prompt("Continue."))

    entries = await storage.read_all()
    compactions = [entry for entry in entries if entry.type == "compaction"]

    assert len(compactions) == 1
    assert "Automatically compacted 2 prior message(s)." in compactions[0].summary
    assert "user: Explain sessions." in compactions[0].summary
    assert "assistant: First answer" in compactions[0].summary
    assert provider.calls[1][2] == [
        UserMessage(content=f"Previous conversation summary:\n{compactions[0].summary}"),
        UserMessage(content="Continue."),
    ]


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
    assert ("openai", "gpt-5.5") in [
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


@pytest.mark.anyio
async def test_session_resumes_indexed_session(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    first_record = manager.create_session(cwd=tmp_path / "first", model="fake", title="First")
    second_cwd = tmp_path / "second"
    second_cwd.mkdir(parents=True)
    second_record = manager.create_session(cwd=second_cwd, model="fake", title="Second")
    first_storage = JsonlSessionStorage(first_record.path)
    second_storage = JsonlSessionStorage(second_record.path)
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Second answer")),
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
    assert [item.content for item in session.messages[:2]] == ["Earlier", "Restored"]
    assert provider.calls[0][2] == [
        UserMessage(content="Earlier"),
        AssistantMessage(content="Restored"),
        UserMessage(content="Continue."),
    ]


@pytest.mark.anyio
async def test_session_context_usage_recalculates_after_resume(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    first_record = manager.create_session(cwd=tmp_path / "first", model="fake", title="First")
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


def test_minimal_commands_are_handled(tmp_path: Path) -> None:
    session = CodingSession(
        _config(tmp_path, FakeProvider([]), JsonlSessionStorage(tmp_path / "session.jsonl")),
        state=object(),  # type: ignore[arg-type]
        harness=object(),  # type: ignore[arg-type]
        last_parent_id=None,
    )

    assert session.handle_command("hello").handled is False
    assert session.handle_command("/new").new_session_requested is True
    assert session.handle_command("/clear").message == "Unknown command: /clear"
    assert session.handle_command("/quit").exit_requested is True
    assert session.handle_command("/exit").message == "Unknown command: /exit"
    assert session.handle_command("/unknown").message == "Unknown command: /unknown"
