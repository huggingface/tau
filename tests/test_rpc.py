import json
from io import StringIO
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pi_event_helpers import assistant_done, assistant_start, text_delta
from tau_agent import AssistantMessage
from tau_agent.session import JsonlSessionStorage
from tau_ai import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding import cli as cli_module
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
    assert records[1]["data"]["model"] == "fake"


@pytest.mark.anyio
def test_cli_routes_rpc_mode_without_a_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_run(*args: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "run_openai_rpc_mode", fake_run)

    result = CliRunner().invoke(cli_module.app, ["--mode", "rpc"])

    assert result.exit_code == 0
    assert called is True


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
