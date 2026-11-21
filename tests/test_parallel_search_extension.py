"""Exercise the opt-in search extension through Tau and the MCP HTTP client."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from pi_event_helpers import assistant_done
from tau_agent import AssistantMessage, ToolCall, ToolExecutionEndEvent, ToolResultMessage
from tau_agent.harness import SimpleCancellationToken
from tau_agent.session import JsonlSessionStorage
from tau_ai import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig, TauResourcePaths
from tau_coding.cli import run_print_mode
from tau_coding.extensions import ExtensionRuntime

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples/extensions/parallel_search.py"
INSTALLED = ROOT / "src/tau_coding/data/examples/extensions/parallel_search.py"
SOURCE_URL = "https://docs.python.org/3/library/asyncio-task.html"
SOURCE_TEXT = f"Title: Coroutines and Tasks\nURL: {SOURCE_URL}\nUse asyncio.TaskGroup."
ARGUMENTS = {
    "objective": "Find public Python documentation on structured concurrency.",
    "search_queries": ["Python asyncio TaskGroup documentation"],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _runtime(tmp_path: Path, example: Path = EXAMPLE) -> ExtensionRuntime:
    runtime = ExtensionRuntime()
    runtime.load(
        TauResourcePaths(root=tmp_path / "home", cwd=tmp_path, agents_root=tmp_path / "agents"),
        extra_paths=(example,),
        include_resource_dirs=False,
    )
    assert not [diag for diag in runtime.diagnostics if diag.severity == "error"]
    return runtime


def _mock_http(monkeypatch, runtime, handler):  # noqa: ANN001
    module = sys.modules[runtime.extension_tools[0].execute_fn.__module__]
    monkeypatch.setattr(
        module,
        "create_async_client",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False, **kwargs
        ),
    )
    return module


def _mcp_response(request: httpx.Request, result: dict) -> httpx.Response:
    if request.method != "POST":
        return httpx.Response(405)
    payload = json.loads(request.content)
    if payload["method"] == "notifications/initialized":
        return httpx.Response(202)
    if payload["method"] == "initialize":
        result = {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "test-search", "version": "1"},
        }
    elif payload["method"] == "tools/list":
        result = {"tools": [{"name": "web_search", "inputSchema": {"type": "object"}}]}
    else:
        assert payload["method"] == "tools/call"
        assert payload["params"] == {"name": "web_search", "arguments": ARGUMENTS}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})


def test_packaged_search_example_matches_checkout() -> None:
    assert EXAMPLE.read_bytes() == INSTALLED.read_bytes()


@pytest.mark.parametrize("example", [EXAMPLE, INSTALLED])
async def test_search_uses_mcp_and_preserves_source_urls(tmp_path, monkeypatch, example):  # noqa: ANN001
    runtime = _runtime(tmp_path, example)
    requests = []

    def handle(request):  # noqa: ANN001, ANN202
        requests.append(request)
        return _mcp_response(request, {"content": [{"type": "text", "text": SOURCE_TEXT}]})

    _mock_http(monkeypatch, runtime, handle)
    result = await runtime.compose_tools([])[0].execute("search", ARGUMENTS)
    assert result.text == SOURCE_TEXT
    assert requests
    assert all(str(req.url) == "https://mcp.parallel.ai/mcp" for req in requests)
    assert all(req.headers["user-agent"] == "tau-parallel-search/1.0" for req in requests)
    assert all(
        "authorization" not in req.headers and "x-api-key" not in req.headers for req in requests
    )


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"content": []}, "No search results returned."),
        ({"content": [], "structuredContent": {"url": SOURCE_URL}}, SOURCE_URL),
    ],
)
async def test_empty_and_structured_results(tmp_path, monkeypatch, response, expected):  # noqa: ANN001
    runtime = _runtime(tmp_path)
    _mock_http(monkeypatch, runtime, lambda request: _mcp_response(request, response))
    assert expected in (await runtime.compose_tools([])[0].execute("search", ARGUMENTS)).text


@pytest.mark.parametrize("failure", ["http", "rate_limit", "tool", "timeout"])
async def test_search_failures_are_tool_errors(tmp_path, monkeypatch, failure):  # noqa: ANN001
    runtime = _runtime(tmp_path)

    async def handle(request):  # noqa: ANN001, ANN202
        if failure in {"http", "rate_limit"}:
            return httpx.Response(429 if failure == "rate_limit" else 503)
        if failure == "timeout":
            await asyncio.Event().wait()
        return _mcp_response(
            request, {"isError": True, "content": [{"type": "text", "text": "Search unavailable"}]}
        )

    module = _mock_http(monkeypatch, runtime, handle)
    monkeypatch.setattr(module, "SEARCH_TIMEOUT", 0.1)
    provider = FakeProvider(
        [
            [
                assistant_done(
                    AssistantMessage(
                        content=[ToolCall(id="s", name="parallel_search", arguments=ARGUMENTS)]
                    )
                )
            ],
            [
                assistant_done(
                    AssistantMessage(content="Search failed; I have no verified sources.")
                )
            ],
        ]
    )
    session = await CodingSession.load(_config(tmp_path, provider, extension_paths=(EXAMPLE,)))
    module = _mock_http(monkeypatch, session.extension_runtime, handle)
    monkeypatch.setattr(module, "SEARCH_TIMEOUT", 0.1)
    try:
        events = [event async for event in session.prompt("Search public Python documentation.")]
        execution = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
        assert execution.is_error
        assert {
            "http": "unavailable",
            "rate_limit": "unavailable",
            "tool": "Search unavailable",
            "timeout": "timed out",
        }[failure] in execution.result.text
        assert len(provider.calls) == 2
    finally:
        await session.aclose()


async def test_cancellation_stops_inflight_http(tmp_path, monkeypatch):  # noqa: ANN001
    runtime = _runtime(tmp_path)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def handle(request):  # noqa: ANN001, ANN202
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    _mock_http(monkeypatch, runtime, handle)
    signal = SimpleCancellationToken()
    task = asyncio.create_task(runtime.compose_tools([])[0].execute("search", ARGUMENTS, signal))
    await asyncio.wait_for(started.wait(), 2)
    signal.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert stopped.is_set()


@pytest.mark.parametrize(
    "arguments",
    [
        {"objective": " ", "search_queries": ["Python documentation"]},
        {"objective": "Public docs", "search_queries": []},
        {**ARGUMENTS, "conversation": "This field must never be forwarded"},
    ],
)
async def test_invalid_arguments_do_not_reach_http(tmp_path, monkeypatch, arguments):  # noqa: ANN001
    runtime = _runtime(tmp_path)

    def fail(request):  # noqa: ANN001
        pytest.fail("Invalid search arguments must not reach the network")

    _mock_http(monkeypatch, runtime, fail)
    with pytest.raises(ValueError):
        await runtime.compose_tools([])[0].execute("search", arguments)


def _config(tmp_path, provider, **kwargs):  # noqa: ANN001, ANN202
    return CodingSessionConfig(
        provider=provider,
        model="fake",
        storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
        cwd=tmp_path,
        resource_paths=TauResourcePaths(
            root=tmp_path / "home", cwd=tmp_path, agents_root=tmp_path / "agents"
        ),
        extensions_enabled=False,
        auto_compact_enabled=False,
        **kwargs,
    )


async def test_default_session_does_not_enable_search(tmp_path, monkeypatch):  # noqa: ANN001
    def fail(*args, **kwargs):  # noqa: ANN002, ANN003
        pytest.fail("The inactive extension must not make HTTP requests")

    monkeypatch.setattr(httpx.AsyncClient, "send", fail)
    session = await CodingSession.load(_config(tmp_path, FakeProvider([])))
    try:
        assert [tool.name for tool in session.tools] == ["read", "write", "edit", "bash"]
    finally:
        await session.aclose()


async def test_source_reaches_printed_answer(tmp_path, monkeypatch, capsys):  # noqa: ANN001
    # A deterministic provider projects the URL it actually receives. This tests
    # Tau's data path, rather than making a claim about a live model's decisions.
    class CitingProvider(FakeProvider):
        def stream_response(self, **kwargs):  # noqa: ANN003, ANN202
            if not self.calls:
                self._streams = [
                    [
                        assistant_done(
                            AssistantMessage(
                                content=[
                                    ToolCall(id="s", name="parallel_search", arguments=ARGUMENTS)
                                ]
                            )
                        )
                    ]
                ]
            else:
                result = kwargs["messages"][-1]
                assert isinstance(result, ToolResultMessage)
                assert result.text == SOURCE_TEXT
                url = next(
                    line.removeprefix("URL: ")
                    for line in result.text.splitlines()
                    if line.startswith("URL: ")
                )
                self._streams = [
                    [
                        assistant_done(
                            AssistantMessage(
                                content=f"Use asyncio.TaskGroup. [Python documentation]({url})"
                            )
                        )
                    ]
                ]
            return super().stream_response(**kwargs)

    original_load = ExtensionRuntime.load

    def load_with_http(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        original_load(self, *args, **kwargs)
        _mock_http(
            monkeypatch,
            self,
            lambda request: _mcp_response(
                request, {"content": [{"type": "text", "text": SOURCE_TEXT}]}
            ),
        )

    monkeypatch.setattr(ExtensionRuntime, "load", load_with_http)
    provider = CitingProvider([])
    assert await run_print_mode(
        prompt="Search public Python documentation and cite the source.",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        resource_paths=TauResourcePaths(
            root=tmp_path / "home", cwd=tmp_path, agents_root=tmp_path / "agents"
        ),
        extension_paths=(EXAMPLE,),
        extensions_enabled=False,
    )
    assert [tool.name for tool in provider.calls[0][3]] == [
        "read",
        "write",
        "edit",
        "bash",
        "parallel_search",
    ]
    assert f"[Python documentation]({SOURCE_URL})" in capsys.readouterr().out


async def test_tool_call_hook_can_block_search(tmp_path, monkeypatch):  # noqa: ANN001
    hook = tmp_path / "gate.py"
    hook.write_text(
        "from tau_coding.extensions import ToolCallHookResult\n"
        "def setup(tau):\n"
        "    tau.on('tool_call', lambda event, context: "
        "ToolCallHookResult(block=True, reason='Not authorized'))\n"
    )
    runtime = _runtime(tmp_path)

    def fail(request):  # noqa: ANN001
        pytest.fail("Blocked search must not reach the network")

    _mock_http(monkeypatch, runtime, fail)
    # Load both through the ordinary runtime so the hook wraps the search tool.
    paths = TauResourcePaths(root=tmp_path / "home", cwd=tmp_path, agents_root=tmp_path / "agents")
    runtime.load(paths, extra_paths=(EXAMPLE, hook), include_resource_dirs=False)
    _mock_http(monkeypatch, runtime, fail)
    result = await runtime.compose_tools([])[0].execute("search", ARGUMENTS)
    assert "Not authorized" in result.text
