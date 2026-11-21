"""Behavioral tests for the shipped example extensions.

Mirrors Pi's practice of testing example extensions from the main suite
(plan-mode-extension.test.ts and friends), with one difference: instead of
mocking the extension API, these load the real files through the real
`ExtensionRuntime` and drive the composed tools — which doubles as a template
for how extension authors can test their own extensions.
"""

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
import pytest

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult
from tau_coding import TauResourcePaths
from tau_coding.extensions import ExtensionRuntime, NullUiBridge

pytestmark = pytest.mark.anyio

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples" / "extensions"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _runtime_with_examples(tmp_path: Path, *names: str) -> ExtensionRuntime:
    runtime = ExtensionRuntime()
    runtime.load(
        TauResourcePaths(
            root=tmp_path / "home-tau",
            cwd=tmp_path / "project",
            agents_root=tmp_path / "home-agents",
        ),
        extra_paths=tuple(EXAMPLES_DIR / name for name in names),
        include_resource_dirs=False,
    )
    assert not [diag for diag in runtime.diagnostics if diag.severity == "error"]
    return runtime


def _fake_tool(name: str) -> tuple[AgentTool, list[dict[str, object]]]:
    executed: list[dict[str, object]] = []

    async def executor(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        del tool_call_id, signal, on_update
        executed.append(dict(arguments))
        return AgentToolResult(content=[TextContent(text="ran")])

    tool = AgentTool(
        name=name,
        label=name,
        description="fake",
        parameters={},
        execute_fn=executor,
    )
    return tool, executed


class SelectingUiBridge(NullUiBridge):
    def __init__(self, selected: str | None) -> None:
        self.selected = selected
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    @property
    def has_ui(self) -> bool:
        return True

    async def select(
        self,
        title: str,
        options: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> str | None:
        del timeout
        choices = tuple(options)
        self.calls.append((title, choices))
        return self.selected


# -- shipped examples ----------------------------------------------------------


def test_shipped_examples_load(tmp_path: Path) -> None:
    runtime = _runtime_with_examples(
        tmp_path,
        "ask_user_question.py",
        "hello_tool.py",
        "permission_gate.py",
        "prompt_section.py",
        "sidebar_status.py",
    )

    assert runtime.extension_names == (
        "ask_user_question",
        "hello_tool",
        "permission_gate",
        "prompt_section",
        "sidebar_status",
    )
    assert [tool.name for tool in runtime.extension_tools] == [
        "ask_user_question",
        "hello",
    ]
    assert runtime.prompt_sections[0].title == "Review procedure"
    assert "```bash" in runtime.prompt_sections[0].body


async def test_ask_user_question_returns_selected_option(tmp_path: Path) -> None:
    runtime = _runtime_with_examples(tmp_path, "ask_user_question.py")
    ui = SelectingUiBridge("Run focused tests")
    runtime.set_ui_bridge(ui)
    tool = runtime.compose_tools([])[0]

    result = await tool.execute(
        "test-call",
        {
            "question": "Which checks should I run?",
            "options": ["Run focused tests", "Run all tests"],
        },
    )

    assert result.text == "The user selected: Run focused tests"
    assert result.details == {"selected": "Run focused tests"}
    assert ui.calls == [
        (
            "Which checks should I run?",
            ("Run focused tests", "Run all tests"),
        )
    ]


async def test_ask_user_question_handles_cancel(tmp_path: Path) -> None:
    runtime = _runtime_with_examples(tmp_path, "ask_user_question.py")
    runtime.set_ui_bridge(SelectingUiBridge(None))
    tool = runtime.compose_tools([])[0]

    result = await tool.execute(
        "test-call",
        {"question": "Continue?", "options": ["Yes", "No"]},
    )

    assert result.text == "The user did not select an option."
    assert result.details == {"selected": None}


# -- hello_tool.py -------------------------------------------------------------


async def test_hello_tool_greets(tmp_path: Path) -> None:
    runtime = _runtime_with_examples(tmp_path, "hello_tool.py")
    hello = runtime.compose_tools([])[0]

    result = await hello.execute("test-call", {"who": "Tau"})

    assert result.text == "Hello, Tau!"


async def test_hello_tool_defaults_to_world(tmp_path: Path) -> None:
    runtime = _runtime_with_examples(tmp_path, "hello_tool.py")
    hello = runtime.compose_tools([])[0]

    result = await hello.execute("test-call", {})

    assert result.text == "Hello, world!"


# -- permission_gate.py ---------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build/",
        "rm -fr /tmp/x",
        "git push --force origin main",
        "git reset --hard HEAD~3",
        "chmod -R 777 .",
        "dd if=/dev/zero of=/dev/sda",
    ],
)
async def test_permission_gate_blocks_dangerous_bash(tmp_path: Path, command: str) -> None:
    runtime = _runtime_with_examples(tmp_path, "permission_gate.py")
    bash, executed = _fake_tool("bash")
    wrapped = runtime.compose_tools([bash])[0]

    result = await wrapped.execute("test-call", {"command": command})

    assert "blocked" in result.text.lower()
    assert "guarded pattern" in result.text
    assert executed == []


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "rm build/output.txt",
        "git push origin feature-branch",
        "git log --oneline",
    ],
)
async def test_permission_gate_allows_safe_bash(tmp_path: Path, command: str) -> None:
    runtime = _runtime_with_examples(tmp_path, "permission_gate.py")
    bash, executed = _fake_tool("bash")
    wrapped = runtime.compose_tools([bash])[0]

    result = await wrapped.execute("test-call", {"command": command})

    assert result.text == "ran"
    assert executed == [{"command": command}]


async def test_permission_gate_ignores_other_tools(tmp_path: Path) -> None:
    runtime = _runtime_with_examples(tmp_path, "permission_gate.py")
    write, executed = _fake_tool("write")
    wrapped = runtime.compose_tools([write])[0]

    result = await wrapped.execute("test-call", {"content": "rm -rf / would be bad"})

    assert result.text == "ran"
    assert len(executed) == 1


# -- keenable_search.py ---------------------------------------------------------


def _keenable_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[AgentTool, list[httpx.Request]]:
    runtime = _runtime_with_examples(tmp_path, "keenable_search.py")
    # Each load creates a fresh synthetic module; patch the newest one.
    module = [
        mod
        for name, mod in sys.modules.items()
        if name.startswith("tau_extension_") and hasattr(mod, "KEYLESS_URL")
    ][-1]
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(**kwargs: object) -> httpx.AsyncClient:
        assert kwargs["follow_redirects"] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(record))

    monkeypatch.setattr(module, "_client_factory", factory)
    return runtime.compose_tools([])[0], seen


async def test_keenable_search_keyless_sends_title_and_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    payload = {
        "results": [
            {"title": "Tau", "url": "https://example.com/tau", "snippet": "A coding agent."},
            {"title": "No URL", "snippet": "dropped"},
            {"title": "", "url": "https://example.com/b", "description": "fallback"},
        ]
    }
    tool, seen = _keenable_runtime(
        tmp_path, monkeypatch, lambda r: httpx.Response(200, json=payload)
    )

    result = await tool.execute("test-call", {"query": "tau agent", "max_results": 50})

    assert tool.name == "web_search"
    request = seen[0]
    assert str(request.url) == "https://api.keenable.ai/v1/search/public"
    assert request.headers["X-Keenable-Title"] == "Tau"
    assert "X-API-Key" not in request.headers
    assert json.loads(request.content) == {"query": "tau agent", "max_results": 10}
    assert result.text == (
        "[1] Tau\nhttps://example.com/tau\nA coding agent.\n\n"
        "[2] https://example.com/b\nhttps://example.com/b\nfallback"
    )
    assert result.details == {"results": ["https://example.com/tau", "https://example.com/b"]}


async def test_keenable_search_uses_key_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KEENABLE_API_KEY", "secret")
    tool, seen = _keenable_runtime(
        tmp_path, monkeypatch, lambda r: httpx.Response(200, json={"results": []})
    )

    result = await tool.execute("test-call", {"query": "q"})

    assert str(seen[0].url) == "https://api.keenable.ai/v1/search"
    assert seen[0].headers["X-API-Key"] == "secret"
    assert "X-Keenable-Title" not in seen[0].headers
    assert result.text == "No web results for: q"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "Keenable rate limit reached. Try again later or set KEENABLE_API_KEY."),
        (500, "Keenable search failed: HTTP 500."),
        (307, "Keenable search failed: HTTP 307."),
    ],
)
async def test_keenable_search_reports_http_errors_without_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    tool, _ = _keenable_runtime(
        tmp_path,
        monkeypatch,
        lambda r: httpx.Response(
            status, text="raw upstream body", headers={"location": "https://other.example"}
        ),
    )

    result = await tool.execute("test-call", {"query": "q"})

    assert result.text == expected
    assert result.details == {"error": expected}


async def test_keenable_search_timeout_and_empty_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    tool, seen = _keenable_runtime(tmp_path, monkeypatch, boom)

    assert (await tool.execute("test-call", {"query": "q"})).text == "Keenable search timed out."
    empty = await tool.execute("test-call", {"query": "  "})
    assert empty.text == "web_search needs a non-empty query."
    assert len(seen) == 1
