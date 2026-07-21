"""Behavioral tests for the shipped example extensions.

Mirrors Pi's practice of testing example extensions from the main suite
(plan-mode-extension.test.ts and friends), with one difference: instead of
mocking the extension API, these load the real files through the real
`ExtensionRuntime` and drive the composed tools — which doubles as a template
for how extension authors can test their own extensions.
"""

from pathlib import Path

import pytest

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult
from tau_coding import TauResourcePaths
from tau_coding.extensions import ExtensionRuntime

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


# -- hello_tool.py -------------------------------------------------------------


def test_hello_and_permission_gate_examples_load(tmp_path: Path) -> None:
    runtime = _runtime_with_examples(tmp_path, "hello_tool.py", "permission_gate.py")

    assert runtime.extension_names == ("hello_tool", "permission_gate")
    assert [tool.name for tool in runtime.extension_tools] == ["hello"]


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
