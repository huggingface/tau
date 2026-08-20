import json
from collections.abc import AsyncIterator
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from pi_event_helpers import assistant_done, assistant_start, text_delta
from tau_agent import AssistantMessage, Usage, UserMessage
from tau_agent.session import JsonlSessionStorage, LeafEntry, MessageEntry, ModelChangeEntry
from tau_ai import FakeProvider
from tau_coding import (
    CodingSession,
    CodingSessionConfig,
    ModelChoice,
    OpenAICompatibleProviderConfig,
    ProviderSettings,
)
from tau_coding import cli as cli_module
from tau_coding.provider_config import ProviderModelMetadata
from tau_coding.rpc import RpcServer


async def _session(tmp_path: Path, provider: FakeProvider) -> CodingSession:
    return await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
        )
    )


@pytest.mark.anyio
async def test_rpc_streams_correlated_response_and_events(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                text_delta("hello"),
                assistant_done(AssistantMessage(content="hello", model="fake")),
            ]
        ]
    )
    session = await _session(tmp_path, provider)
    stdin = StringIO('{"id":"one","type":"prompt","message":"hi"}\n')
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()

    records = [json.loads(line) for line in stdout.getvalue().split("\n") if line]
    assert records[0] == {
        "type": "response",
        "command": "prompt",
        "success": True,
        "id": "one",
    }
    assert any(record["type"] == "agent_start" for record in records)
    assert records[-1]["type"] == "agent_settled"


@pytest.mark.anyio
async def test_rpc_state_matches_pi_frontend_contract(tmp_path: Path) -> None:
    session = await _session(tmp_path, FakeProvider([]))
    stdin = StringIO(
        '{"id":"state","type":"get_state"}\n'
        '{"id":"models","type":"get_available_models"}\n'
        '{"id":"cycle","type":"cycle_model"}\n'
        '{"id":"thinking","type":"set_thinking_level","level":"off"}\n'
    )
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()

    state, models, cycle, thinking = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert set(state["data"]) == {
        "model",
        "thinkingLevel",
        "isStreaming",
        "isCompacting",
        "steeringMode",
        "followUpMode",
        "sessionFile",
        "sessionId",
        "sessionName",
        "autoCompactionEnabled",
        "messageCount",
        "pendingMessageCount",
    }
    assert state["data"]["model"]["id"] == "fake"
    assert models["data"]["models"][0]["provider"] == "openai"
    assert cycle["data"] is None
    assert thinking == {
        "type": "response",
        "command": "set_thinking_level",
        "success": True,
        "id": "thinking",
    }


@pytest.mark.anyio
async def test_rpc_model_shape_uses_catalog_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider_config = OpenAICompatibleProviderConfig(
        name="local",
        base_url="http://localhost:1234/v1",
        models=("vision",),
        default_model="vision",
        model_metadata={
            "vision": ProviderModelMetadata(
                name="Vision Model",
                api="openai-responses",
                reasoning=True,
                input=("text", "image"),
                context_window=131_072,
                max_tokens=32_768,
                cost={"input": 1.0, "output": 2.0},
            )
        },
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="vision",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="local",
            provider_settings=ProviderSettings(
                default_provider="local", providers=(provider_config,)
            ),
            runtime_provider_config=provider_config,
        )
    )
    stdin = StringIO('{"id":"state","type":"get_state"}\n')
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()

    model = json.loads(stdout.getvalue())["data"]["model"]
    assert model == {
        "id": "vision",
        "name": "Vision Model",
        "api": "openai-responses",
        "provider": "local",
        "baseUrl": "http://localhost:1234/v1",
        "reasoning": True,
        "input": ["text", "image"],
        "contextWindow": 131072,
        "maxTokens": 32768,
        "cost": {"input": 1.0, "output": 2.0, "cacheRead": 0.0, "cacheWrite": 0.0},
    }


@pytest.mark.anyio
async def test_rpc_session_inspection_matches_pi_shapes(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    user = MessageEntry(message=UserMessage(content="question"))
    assistant = MessageEntry(
        parent_id=user.id,
        message=AssistantMessage(
            content="answer",
            model="fake",
            usage=Usage(input=10, output=3, cache_read=20, cache_write=5),
        ),
    )
    model_change = ModelChangeEntry(
        parent_id=assistant.id,
        model="fake-next",
        provider="historical-openai",
    )
    legacy_model_change = ModelChangeEntry(
        parent_id=model_change.id,
        model="legacy-model",
    )
    leaf = LeafEntry(parent_id=legacy_model_change.id, entry_id=legacy_model_change.id)
    await storage.append(user)
    await storage.append(assistant)
    await storage.append(model_change)
    await storage.append(legacy_model_change)
    await storage.append(leaf)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="fake",
            system="You are Tau.",
            storage=storage,
            cwd=tmp_path,
        )
    )
    stdin = StringIO(
        '{"id":"entries","type":"get_entries"}\n'
        '{"id":"tree","type":"get_tree"}\n'
        '{"id":"last","type":"get_last_assistant_text"}\n'
        '{"id":"forks","type":"get_fork_messages"}\n'
        '{"id":"stats","type":"get_session_stats"}\n'
    )
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()

    entries, tree, last, forks, stats = [
        json.loads(line) for line in stdout.getvalue().splitlines()
    ]
    projected_entries = entries["data"]["entries"]
    assert projected_entries[1]["parentId"] == user.id
    assert projected_entries[2]["type"] == "model_change"
    assert projected_entries[2]["provider"] == "historical-openai"
    assert projected_entries[2]["modelId"] == "fake-next"
    assert projected_entries[3]["provider"] == "openai"
    assert projected_entries[3]["modelId"] == "legacy-model"
    assert all(entry["type"] != "leaf" for entry in projected_entries)
    tree_root = tree["data"]["tree"][0]
    assert tree_root["children"][0]["entry"]["id"] == assistant.id
    assert tree_root["children"][0]["children"][0]["entry"]["type"] == "model_change"
    assert last["data"] == {"text": "answer"}
    assert forks["data"] == {"messages": [{"entryId": user.id, "text": "question"}]}
    assert stats["data"]["tokens"] == {
        "input": 10,
        "output": 3,
        "cacheRead": 20,
        "cacheWrite": 5,
        "total": 38,
    }
    assert stats["data"]["cost"] == 0.0
    assert isinstance(stats["data"]["cost"], float)


@pytest.mark.anyio
async def test_rpc_compaction_returns_canonical_summary_and_boundary(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    parent_id: str | None = None
    original_ids: list[str] = []
    for index in range(6):
        user = MessageEntry(
            parent_id=parent_id,
            message=UserMessage(content=f"question-{index}-" + "x" * 8_000),
        )
        assistant = MessageEntry(
            parent_id=user.id,
            message=AssistantMessage(content="y" * 8_000, model="fake"),
        )
        await storage.append(user)
        await storage.append(assistant)
        original_ids.extend((user.id, assistant.id))
        parent_id = assistant.id
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(AssistantMessage(content="real summary", model="fake")),
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
    stdin = StringIO('{"id":"compact","type":"compact"}\n')
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()

    response = json.loads(stdout.getvalue())
    compaction = next(entry for entry in await storage.read_all() if entry.type == "compaction")
    boundary = response["data"]["firstKeptEntryId"]
    assert response["data"]["summary"] == "real summary"
    assert boundary in original_ids
    assert boundary not in compaction.replaces_entry_ids
    assert boundary != compaction.id
    assert compaction.first_kept_entry_id == boundary
    assert compaction.tokens_before == response["data"]["tokensBefore"]


@pytest.mark.anyio
async def test_rpc_auto_compaction_control_updates_pi_state(tmp_path: Path) -> None:
    session = await _session(tmp_path, FakeProvider([]))
    stdin = StringIO(
        '{"id":"set","type":"set_auto_compaction","enabled":false}\n'
        '{"id":"state","type":"get_state"}\n'
    )
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()

    changed, state = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert changed["success"] is True
    assert state["data"]["autoCompactionEnabled"] is False


@pytest.mark.anyio
async def test_rpc_direct_bash_matches_pi_result_shape(tmp_path: Path) -> None:
    session = await _session(tmp_path, FakeProvider([]))
    stdin = StringIO('{"id":"bash","type":"bash","command":"printf ok"}\n')
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()

    response = json.loads(stdout.getvalue())
    assert response["data"] == {
        "output": "ok",
        "exitCode": 0,
        "cancelled": False,
        "truncated": False,
    }


@pytest.mark.anyio
async def test_rpc_reports_bad_records_and_continues(tmp_path: Path) -> None:
    session = await _session(tmp_path, FakeProvider([]))
    stdin = StringIO('not-json\n{"id":2,"type":"get_state"}\n')
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()

    records = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert records[0]["success"] is False
    assert records[0]["command"] == "parse"
    assert records[1]["id"] == 2
    assert records[1]["success"] is True
    assert records[1]["data"]["model"]["id"] == "fake"
    assert records[1]["data"]["model"]["provider"] == "openai"


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["providers", "sessions", "setup", "export", "update"])
def test_rpc_mode_never_dispatches_utility_commands(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    called: list[str] = []
    monkeypatch.setattr(cli_module, "providers_command", lambda: called.append("providers"))
    monkeypatch.setattr(
        cli_module, "render_session_list", lambda records: called.append("sessions")
    )
    monkeypatch.setattr(cli_module, "setup_command", lambda **kwargs: called.append("setup"))
    monkeypatch.setattr(cli_module, "_run_export_cli", lambda args: called.append("export"))
    monkeypatch.setattr(cli_module, "update_command", lambda: called.append("update"))

    result = CliRunner().invoke(cli_module.app, ["--mode", "rpc", command])

    assert result.exit_code == 2
    assert called == []


def test_cli_routes_rpc_mode_without_a_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_run(*args: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "run_openai_rpc_mode", fake_run)

    result = CliRunner().invoke(cli_module.app, ["--mode", "rpc"])

    assert result.exit_code == 0
    assert called is True


class _PreflightFailureSession:
    model = "before"
    provider_name = "provider-before"
    thinking_level = "off"
    available_thinking_levels = ("off",)
    available_model_choices: tuple[object, ...] = ()
    messages: tuple[object, ...] = ()
    session_id = None
    auto_compact_token_threshold = None
    session_stats = SimpleNamespace()
    command_registry = SimpleNamespace(list_commands=lambda: ())
    state = SimpleNamespace(active_leaf_id=None)

    async def emit_pending_session_start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None

    def cancel(self) -> None:
        return None

    def prompt(
        self, content: str, *, streaming_behavior: str | None = None
    ) -> AsyncIterator[object]:
        del content, streaming_behavior

        async def fail() -> AsyncIterator[object]:
            raise ValueError("preflight rejected")
            yield

        return fail()

    def set_model_choice(self, choice: ModelChoice) -> None:
        raise ValueError(f"Model is not available: {choice.provider_name}:{choice.model}")


@pytest.mark.anyio
async def test_rpc_preflight_failure_returns_one_correlated_failure() -> None:
    session = _PreflightFailureSession()
    stdin = StringIO('{"id":"bad","type":"prompt","message":"hi"}\n')
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()  # type: ignore[arg-type]

    records = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert records == [
        {
            "type": "response",
            "command": "prompt",
            "success": False,
            "error": "preflight rejected",
            "id": "bad",
        }
    ]


@pytest.mark.anyio
async def test_rpc_failed_model_change_does_not_mutate_session() -> None:
    session = _PreflightFailureSession()
    stdin = StringIO('{"id":"model","type":"set_model","provider":"other","modelId":"missing"}\n')
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()  # type: ignore[arg-type]

    record = json.loads(stdout.getvalue())
    assert record["success"] is False
    assert session.provider_name == "provider-before"
    assert session.model == "before"


@pytest.mark.anyio
async def test_rpc_splits_only_on_lf_and_accepts_crlf(tmp_path: Path) -> None:
    session = await _session(tmp_path, FakeProvider([]))
    separator = chr(0x2028)
    stdin = StringIO(f'{{"id":"a{separator}b","type":"get_state"}}\r\n')
    stdout = StringIO()

    await RpcServer(session, stdin=stdin, stdout=stdout).run()

    record = json.loads(stdout.getvalue())
    assert record["success"] is True
    assert record["id"] == "a\u2028b"
