"""Tests for extension discovery, loading, hooks, and session wiring."""

import asyncio
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from pi_event_helpers import assistant_done, assistant_start
from tau_agent import AssistantMessage, ToolCall, UserMessage
from tau_agent.messages import AgentMessage, assistant_content
from tau_agent.session import CustomEntry, JsonlSessionStorage, LeafEntry, MessageEntry
from tau_agent.tools import AgentTool, AgentToolResult
from tau_agent.types import JSONValue
from tau_ai import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig, TauResourcePaths
from tau_coding.extensions import (
    CustomMessageView,
    ExtensionAPI,
    ExtensionError,
    ExtensionRuntime,
    InputEvent,
    InputHookResult,
    MessageRenderOptions,
    ToolCallHookResult,
    ToolResultHookResult,
    discover_extensions,
    load_extensions,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _paths(tmp_path: Path) -> TauResourcePaths:
    return TauResourcePaths(
        root=tmp_path / "home-tau",
        cwd=tmp_path / "project",
        agents_root=tmp_path / "home-agents",
    )


def _write_extension(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


HELLO_TOOL_EXTENSION = """
from tau_agent.tools import AgentTool, AgentToolResult


async def _run(tool_call_id, arguments, signal=None, on_update=None):
    return AgentToolResult(content=f"hello {arguments.get('who', 'world')}")


def setup(tau):
    tau.register_tool(
        AgentTool(
            name="hello",
            label="Hello",
            description="Say hello.",
            parameters={"type": "object", "properties": {"who": {"type": "string"}}},
            execute_fn=_run,
            prompt_snippet="Greet someone by name.",
        )
    )
"""


def _user_extensions_dir(paths: TauResourcePaths) -> Path:
    return paths.root / "extensions"


def _project_extensions_dir(paths: TauResourcePaths) -> Path:
    assert paths.cwd is not None
    return paths.cwd / ".tau" / "extensions"


def _runtime_with(
    paths: TauResourcePaths,
    *,
    include_project_dir: bool = False,
) -> ExtensionRuntime:
    runtime = ExtensionRuntime()
    runtime.load(paths, include_project_dir=include_project_dir)
    return runtime


class RecordingSession:
    """Minimal BoundSession implementation for runtime tests."""

    def __init__(self, tmp_path: Path, *, running: bool = False) -> None:
        self.cwd = tmp_path
        self.model = "fake"
        self.provider_name = "fake"
        self.session_id = "session-1"
        self.system_prompt = "You are Tau."
        self.is_running = running
        self.messages: tuple[AgentMessage, ...] = ()
        self.steered: list[str] = []
        self.followed_up: list[str] = []
        self.custom_entries: list[tuple[str, dict[str, JSONValue]]] = []
        self.queued_custom: list[tuple[str, str | None, dict[str, JSONValue] | None]] = []

    def queue_steering_message(
        self,
        content: str,
        *,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        self.steered.append(content)
        self.queued_custom.append((content, custom_type, details))

    def queue_follow_up_message(
        self,
        content: str,
        *,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        self.followed_up.append(content)
        self.queued_custom.append((content, custom_type, details))

    async def append_custom_entry(self, namespace: str, data: dict[str, JSONValue]) -> None:
        self.custom_entries.append((namespace, data))


# -- discovery and loading ----------------------------------------------------


def test_discovers_user_extensions_and_skips_project_by_default(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "user_ext", "def setup(tau):\n    pass\n")
    _write_extension(_project_extensions_dir(paths), "proj_ext", "def setup(tau):\n    pass\n")

    discovered, diagnostics = discover_extensions(paths)

    assert [entry.name for entry in discovered] == ["user_ext"]
    assert diagnostics == ()


def test_project_extensions_load_first_when_enabled(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "ext_a", "def setup(tau):\n    pass\n")
    _write_extension(_project_extensions_dir(paths), "ext_b", "def setup(tau):\n    pass\n")

    discovered, _ = discover_extensions(paths, include_project_dir=True)

    assert [entry.name for entry in discovered] == ["ext_b", "ext_a"]


def test_duplicate_extension_names_prefer_first_loaded(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "dup", "def setup(tau):\n    pass\n")
    _write_extension(_project_extensions_dir(paths), "dup", "def setup(tau):\n    pass\n")

    discovered, diagnostics = discover_extensions(paths, include_project_dir=True)

    assert len(discovered) == 1
    assert discovered[0].path == _project_extensions_dir(paths) / "dup.py"
    assert any("duplicate extension name" in diag.message for diag in diagnostics)


def test_explicit_extension_paths_load_even_with_discovery_disabled(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "skipped", "def setup(tau):\n    pass\n")
    explicit = _write_extension(tmp_path / "elsewhere", "explicit", "def setup(tau):\n    pass\n")

    discovered, _ = discover_extensions(
        paths,
        extra_paths=(explicit,),
        include_resource_dirs=False,
    )

    assert [entry.name for entry in discovered] == ["explicit"]


def test_missing_explicit_path_is_an_error_diagnostic(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    _, diagnostics = discover_extensions(paths, extra_paths=(tmp_path / "nope.py",))

    assert any(diag.severity == "error" for diag in diagnostics)


def test_package_extension_loads_with_relative_import(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    package_dir = _user_extensions_dir(paths) / "pkg_ext"
    package_dir.mkdir(parents=True)
    (package_dir / "helper.py").write_text("VALUE = 41\n", encoding="utf-8")
    (package_dir / "extension.py").write_text(
        "from . import helper\n\n\ndef setup(tau):\n    setup.value = helper.VALUE + 1\n",
        encoding="utf-8",
    )

    result = load_extensions(paths)

    assert [ext.name for ext in result.extensions] == ["pkg_ext"]
    assert result.diagnostics == ()
    result.extensions[0].setup(object())
    assert result.extensions[0].setup.value == 42  # type: ignore[attr-defined]


def _write_src_layout_extension(repo: Path, *, entry_name: str = "extension") -> Path:
    package_dir = repo / "src" / "my_ext"
    package_dir.mkdir(parents=True)
    (package_dir / "helper.py").write_text("VALUE = 41\n", encoding="utf-8")
    entry = package_dir / f"{entry_name}.py"
    entry.write_text(
        "from . import helper\n\n\ndef setup(tau):\n    setup.value = helper.VALUE + 1\n",
        encoding="utf-8",
    )
    return entry


def test_manifest_declares_src_layout_entry(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    _write_src_layout_extension(repo)
    (repo / "pyproject.toml").write_text(
        '[tool.tau]\nextensions = ["src/my_ext/extension.py"]\n', encoding="utf-8"
    )

    result = load_extensions(paths, extra_paths=(repo,), include_resource_dirs=False)

    assert [ext.name for ext in result.extensions] == ["my_ext"]
    assert result.diagnostics == ()
    result.extensions[0].setup(object())
    assert result.extensions[0].setup.value == 42  # type: ignore[attr-defined]


def test_manifest_wins_over_root_extension_py(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    _write_src_layout_extension(repo)
    (repo / "extension.py").write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.tau]\nextensions = ["src/my_ext/extension.py"]\n', encoding="utf-8"
    )

    discovered, _ = discover_extensions(paths, extra_paths=(repo,), include_resource_dirs=False)

    assert [entry.name for entry in discovered] == ["my_ext"]
    assert discovered[0].path == repo / "src" / "my_ext" / "extension.py"


def test_manifest_entry_named_after_file_when_not_extension_py(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    _write_src_layout_extension(repo, entry_name="main")
    (repo / "pyproject.toml").write_text(
        '[tool.tau]\nextensions = ["src/my_ext/main.py"]\n', encoding="utf-8"
    )

    discovered, _ = discover_extensions(paths, extra_paths=(repo,), include_resource_dirs=False)

    assert [entry.name for entry in discovered] == ["main"]
    assert discovered[0].package_dir == repo / "src" / "my_ext"


def test_manifest_discovered_in_extensions_dir(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = _user_extensions_dir(paths) / "my-ext-repo"
    _write_src_layout_extension(repo)
    (repo / "pyproject.toml").write_text(
        '[tool.tau]\nextensions = ["src/my_ext/extension.py"]\n', encoding="utf-8"
    )

    discovered, diagnostics = discover_extensions(paths)

    assert [entry.name for entry in discovered] == ["my_ext"]
    assert diagnostics == ()


def test_manifest_multiple_entries_load_independently(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    _write_src_layout_extension(repo)
    other = repo / "src" / "other_ext"
    other.mkdir()
    (other / "extension.py").write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.tau]\nextensions = ["src/my_ext/extension.py", "src/other_ext/extension.py"]\n',
        encoding="utf-8",
    )

    result = load_extensions(paths, extra_paths=(repo,), include_resource_dirs=False)

    assert [ext.name for ext in result.extensions] == ["my_ext", "other_ext"]
    assert result.diagnostics == ()


def test_manifest_duplicate_entry_names_first_wins(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    for parent in ("src", "legacy"):
        package_dir = repo / parent / "my_ext"
        package_dir.mkdir(parents=True)
        (package_dir / "extension.py").write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.tau]\nextensions = ["src/my_ext/extension.py", "legacy/my_ext/extension.py"]\n',
        encoding="utf-8",
    )

    discovered, diagnostics = discover_extensions(
        paths, extra_paths=(repo,), include_resource_dirs=False
    )

    assert [entry.name for entry in discovered] == ["my_ext"]
    assert discovered[0].path == repo / "src" / "my_ext" / "extension.py"
    assert any("duplicate extension name" in diag.message for diag in diagnostics)


def test_manifest_empty_list_falls_back_to_extension_py(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "extension.py").write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[tool.tau]\nextensions = []\n", encoding="utf-8")

    discovered, diagnostics = discover_extensions(
        paths, extra_paths=(repo,), include_resource_dirs=False
    )

    assert [entry.name for entry in discovered] == ["repo"]
    assert diagnostics == ()


def test_explicit_file_path_to_package_entry_cannot_reach_siblings(tmp_path: Path) -> None:
    """`-e` on an entry *file* loads it standalone: relative imports fail.

    Package extensions must be loaded through their directory (or a manifest);
    this pins the failure mode the docs warn about.
    """
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    entry = _write_src_layout_extension(repo)

    result = load_extensions(paths, extra_paths=(entry,), include_resource_dirs=False)

    assert result.extensions == ()
    assert any(
        diag.severity == "error" and "failed to import extension" in diag.message
        for diag in result.diagnostics
    )


def test_manifest_missing_entry_is_error_with_extension_py_fallback(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "extension.py").write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.tau]\nextensions = ["src/gone/extension.py"]\n', encoding="utf-8"
    )

    discovered, diagnostics = discover_extensions(
        paths, extra_paths=(repo,), include_resource_dirs=False
    )

    assert [entry.name for entry in discovered] == ["repo"]
    assert any(
        diag.severity == "error" and "does not exist" in diag.message for diag in diagnostics
    )


def test_manifest_without_tau_table_falls_back_to_extension_py(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "extension.py").write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

    discovered, diagnostics = discover_extensions(
        paths, extra_paths=(repo,), include_resource_dirs=False
    )

    assert [entry.name for entry in discovered] == ["repo"]
    assert diagnostics == ()


def test_manifest_invalid_declarations_are_error_diagnostics(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "extension.py").write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[tool.tau]\nextensions = 3\n", encoding="utf-8")

    discovered, diagnostics = discover_extensions(
        paths, extra_paths=(repo,), include_resource_dirs=False
    )

    assert [entry.name for entry in discovered] == ["repo"]
    assert any(
        diag.severity == "error" and "list of file paths" in diag.message for diag in diagnostics
    )


def test_manifest_parse_failure_is_warning_with_fallback(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "extension.py").write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("not [ valid toml", encoding="utf-8")

    discovered, diagnostics = discover_extensions(
        paths, extra_paths=(repo,), include_resource_dirs=False
    )

    assert [entry.name for entry in discovered] == ["repo"]
    assert any(
        diag.severity == "warning" and "could not parse" in diag.message for diag in diagnostics
    )


def test_helper_modules_stay_namespaced_in_sys_modules(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    package_dir = _user_extensions_dir(paths) / "pkg_ns"
    package_dir.mkdir(parents=True)
    (package_dir / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package_dir / "extension.py").write_text(
        "from . import helper\n\n\ndef setup(tau):\n    pass\n",
        encoding="utf-8",
    )

    load_extensions(paths)

    assert "helper" not in sys.modules
    assert any(
        name.startswith("tau_extension_pkg_ns") and name.endswith(".helper") for name in sys.modules
    )


def test_broken_extension_is_isolated(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "broken", "raise RuntimeError('boom')\n")
    _write_extension(_user_extensions_dir(paths), "works", "def setup(tau):\n    pass\n")

    result = load_extensions(paths)

    assert [ext.name for ext in result.extensions] == ["works"]
    assert any(diag.name == "broken" and diag.severity == "error" for diag in result.diagnostics)


def test_extension_without_setup_is_rejected(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "nosetup", "VALUE = 1\n")

    result = load_extensions(paths)

    assert result.extensions == ()
    assert any("entry point" in diag.message for diag in result.diagnostics)


def test_async_setup_is_rejected(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths), "async_setup", "async def setup(tau):\n    pass\n"
    )

    result = load_extensions(paths)

    assert result.extensions == ()
    assert any("sync function" in diag.message for diag in result.diagnostics)


def test_underscore_files_are_skipped(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "_private", "def setup(tau):\n    pass\n")

    discovered, _ = discover_extensions(paths)

    assert discovered == ()


# -- runtime registration ------------------------------------------------------


def test_setup_failure_rolls_back_registrations(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths),
        "half_done",
        (
            "from tau_agent.tools import AgentTool, AgentToolResult\n\n\n"
            "async def _run(tool_call_id, arguments, signal=None, on_update=None):\n"
            "    return AgentToolResult(content='x')\n\n\n"
            "def setup(tau):\n"
            "    tau.register_tool(AgentTool(name='t', description='d',"
            " parameters={}, execute_fn=_run))\n"
            "    raise RuntimeError('late failure')\n"
        ),
    )

    runtime = _runtime_with(paths)

    assert runtime.extension_names == ()
    assert runtime.extension_tools == ()
    assert any("setup failed" in diag.message for diag in runtime.diagnostics)


def test_extension_tool_registration_and_composition(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "hello_ext", HELLO_TOOL_EXTENSION)

    runtime = _runtime_with(paths)
    composed = runtime.compose_tools([])

    assert [tool.name for tool in composed] == ["hello"]


async def test_extension_tool_overrides_builtin_by_name(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths),
        "override",
        (
            "from tau_agent.tools import AgentTool, AgentToolResult\n\n\n"
            "async def _run(tool_call_id, arguments, signal=None, on_update=None):\n"
            "    return AgentToolResult("
            " content='intercepted')\n\n\n"
            "def setup(tau):\n"
            "    tau.register_tool(AgentTool(name='read', label='Read', description='replacement',"
            " parameters={}, execute_fn=_run))\n"
        ),
    )

    async def builtin_read(
        tool_call_id: str, arguments: object, signal: object = None, on_update: object = None
    ) -> AgentToolResult:
        return AgentToolResult(content="builtin")

    builtin = AgentTool(
        name="read", label="read", description="builtin", parameters={}, execute_fn=builtin_read
    )

    runtime = _runtime_with(paths)
    composed = runtime.compose_tools([builtin])

    assert [tool.name for tool in composed] == ["read"]
    result = await composed[0].execute("call-1", {})
    assert result.text == "intercepted"


def test_duplicate_tool_registration_first_wins(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    body = HELLO_TOOL_EXTENSION
    _write_extension(_user_extensions_dir(paths), "ext_one", body)
    _write_extension(_user_extensions_dir(paths), "ext_two", body)

    runtime = _runtime_with(paths)

    assert len(runtime.extension_tools) == 1
    assert any("already registered" in diag.message for diag in runtime.diagnostics)


def test_extension_commands_layer_onto_default_registry(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths),
        "cmd_ext",
        (
            "def _handler(args, context):\n"
            "    return f'echo: {args}'\n\n\n"
            "def setup(tau):\n"
            "    tau.register_command('echo', _handler, description='Echo args.')\n"
        ),
    )

    runtime = _runtime_with(paths)
    registry = runtime.build_command_registry()

    command = registry.get("echo")
    assert command is not None
    result = command.handler(_command_context(registry, "/echo hi", "echo", "hi"))
    assert result.handled is True
    assert result.message == "echo: hi"


def test_extension_command_cannot_shadow_builtin(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths),
        "shadow",
        ("def setup(tau):\n    tau.register_command('model', lambda args, context: 'hijacked')\n"),
    )

    runtime = _runtime_with(paths)
    registry = runtime.build_command_registry()

    command = registry.get("model")
    assert command is not None
    assert command.description == "Choose the active model."
    assert any("could not register command" in diag.message for diag in runtime.diagnostics)


def test_extension_command_errors_are_contained(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths),
        "boom_cmd",
        (
            "def _handler(args, context):\n"
            "    raise RuntimeError('bad command')\n\n\n"
            "def setup(tau):\n"
            "    tau.register_command('boom', _handler)\n"
        ),
    )

    runtime = _runtime_with(paths)
    registry = runtime.build_command_registry()
    command = registry.get("boom")
    assert command is not None

    result = command.handler(_command_context(registry, "/boom", "boom", ""))

    assert result.handled is True
    assert result.message is not None and "failed" in result.message
    assert any("command:/boom" in diag.message for diag in runtime.diagnostics)


def _command_context(registry: object, text: str, name: str, args: str) -> object:
    from tau_coding.commands import CommandContext

    return CommandContext(session=None, registry=registry, text=text, name=name, args=args)  # type: ignore[arg-type]


def test_prompt_guideline_registration(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "guidance")
    api.add_prompt_guideline("Always run the tests before claiming success")
    api.add_prompt_guideline("   ")

    assert runtime.prompt_guidelines == ("Always run the tests before claiming success",)
    assert any("empty prompt guideline" in diag.message for diag in runtime.diagnostics)


def test_unknown_event_subscription_is_a_diagnostic(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths),
        "bad_event",
        "def setup(tau):\n    tau.on('no_such_event', lambda event, context: None)\n",
    )

    runtime = _runtime_with(paths)

    assert runtime.extension_names == ("bad_event",)
    assert any("unknown event" in diag.message for diag in runtime.diagnostics)


# -- hook dispatch ---------------------------------------------------------------


async def test_tool_call_hook_can_block(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "guard")
    api.on(
        "tool_call",
        lambda event, context: (
            ToolCallHookResult(block=True, reason="not allowed")
            if event.tool_name == "danger"
            else None
        ),
    )

    tool = _make_tool("danger", content="ran")
    wrapped = runtime.compose_tools([tool])[0]
    result = await wrapped.execute("call-1", {})

    assert result.text.startswith("Tool call blocked:")
    assert "not allowed" in result.text


async def test_tool_call_hook_can_rewrite_arguments(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "rewrite")
    api.on("tool_call", lambda event, context: ToolCallHookResult(arguments={"who": "tau"}))

    seen: list[dict[str, object]] = []

    async def executor(
        tool_call_id: str, arguments: object, signal: object = None, on_update: object = None
    ) -> AgentToolResult:
        seen.append(dict(arguments))  # type: ignore[call-overload]
        return AgentToolResult(content="ok")

    tool = AgentTool(name="echo", label="echo", description="d", parameters={}, execute_fn=executor)
    wrapped = runtime.compose_tools([tool])[0]
    await wrapped.execute("call-1", {"who": "world"})

    assert seen == [{"who": "tau"}]


async def test_tool_call_hook_can_clear_arguments(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "clearer")
    api.on("tool_call", lambda event, context: ToolCallHookResult(arguments={}))

    seen: list[dict[str, object]] = []

    async def executor(
        tool_call_id: str, arguments: object, signal: object = None, on_update: object = None
    ) -> AgentToolResult:
        seen.append(dict(arguments))  # type: ignore[call-overload]
        return AgentToolResult(content="ok")

    tool = AgentTool(name="echo", label="echo", description="d", parameters={}, execute_fn=executor)
    wrapped = runtime.compose_tools([tool])[0]
    await wrapped.execute("call-1", {"who": "world"})

    assert seen == [{}]


async def test_wrapped_tool_forwards_on_update(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    _register_inline_extension(runtime, "progress")

    received: list[tuple[str, object]] = []

    async def executor(
        tool_call_id: str,
        arguments: object,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        assert on_update is not None
        on_update(AgentToolResult(content="halfway", details={"pct": 50}))  # type: ignore[operator]
        return AgentToolResult(content="done")

    tool = AgentTool(name="work", label="work", description="d", parameters={}, execute_fn=executor)
    wrapped = runtime.compose_tools([tool])[0]

    def collect(result: AgentToolResult) -> None:
        received.append((result.text, result.details))

    result = await wrapped.execute("call-1", {}, on_update=collect)

    assert result.text == "done"
    assert received == [("halfway", {"pct": 50})]


async def test_wrapped_tool_drops_on_update_for_inner_without_seam(tmp_path: Path) -> None:
    # The wrapper always declares on_update, but the inner executor's own
    # inspect-gate must drop it so a classic (arguments, signal) tool still runs.
    runtime = ExtensionRuntime()
    _register_inline_extension(runtime, "plain")

    async def executor(
        tool_call_id: str, arguments: object, signal: object = None, on_update: object = None
    ) -> AgentToolResult:
        return AgentToolResult(content="ran")

    tool = AgentTool(
        name="plain", label="plain", description="d", parameters={}, execute_fn=executor
    )
    wrapped = runtime.compose_tools([tool])[0]

    def collect(message: str, data: object = None) -> None:
        raise AssertionError("on_update should not reach an executor without the seam")

    result = await wrapped.execute("call-1", {}, on_update=collect)

    assert result.text == "ran"


async def test_raising_tool_call_hook_blocks_fail_safe(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "raiser")

    def bad_hook(event: object, context: object) -> None:
        raise RuntimeError("hook exploded")

    api.on("tool_call", bad_hook)

    wrapped = runtime.compose_tools([_make_tool("x", content="ran")])[0]
    result = await wrapped.execute("call-1", {})

    assert result.text.startswith("Tool call blocked:")
    assert "hook failed" in result.text


async def test_tool_result_hook_transforms_result(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "transform")
    api.on("tool_result", lambda event, context: ToolResultHookResult(content="redacted"))

    wrapped = runtime.compose_tools([_make_tool("x", content="secret")])[0]
    result = await wrapped.execute("call-1", {})

    assert result.text == "redacted"


async def test_raising_tool_result_hook_keeps_result(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "raiser")

    def bad_hook(event: object, context: object) -> None:
        raise RuntimeError("result hook exploded")

    api.on("tool_result", bad_hook)

    wrapped = runtime.compose_tools([_make_tool("x", content="fine")])[0]
    result = await wrapped.execute("call-1", {})

    assert result.text == "fine"
    assert any("tool_result" in diag.message for diag in runtime.diagnostics)


async def test_input_hooks_chain_transforms(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "chain")
    api.on(
        "input",
        lambda event, context: InputHookResult(action="transform", text=event.text + " one"),
    )
    api.on(
        "input",
        lambda event, context: InputHookResult(action="transform", text=event.text + " two"),
    )

    outcome = await runtime.run_input_hooks("base")

    assert outcome.handled is False
    assert outcome.text == "base one two"


async def test_input_hook_handled_short_circuits(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "handler")
    api.on("input", lambda event, context: InputHookResult(action="handled", message="done"))
    api.on("input", lambda event, context: InputHookResult(action="transform", text="never"))

    outcome = await runtime.run_input_hooks("base")

    assert outcome.handled is True
    assert outcome.message == "done"


def test_input_event_defaults_backward_compatible() -> None:
    # Existing handlers construct/read InputEvent(text=...) with no metadata.
    event = InputEvent(text="hello")
    assert event.text == "hello"
    assert event.source == "interactive"
    assert event.streaming_behavior is None
    # Frozen-dataclass equality still holds across the new (defaulted) fields.
    assert event == InputEvent(text="hello")
    assert event == InputEvent(text="hello", source="interactive", streaming_behavior=None)
    assert event != InputEvent(text="hello", source="extension")


async def test_input_hook_defaults_to_interactive_idle(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "capture")
    seen: list[InputEvent] = []

    def _hook(event: InputEvent, context: object) -> None:
        seen.append(event)

    api.on("input", _hook)  # type: ignore[attr-defined]

    await runtime.run_input_hooks("hi")

    assert len(seen) == 1
    assert seen[0].source == "interactive"
    assert seen[0].streaming_behavior is None


async def test_input_hook_receives_source_and_streaming_behavior(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "capture")
    seen: list[InputEvent] = []
    api.on("input", lambda event, context: seen.append(event))  # type: ignore[attr-defined]

    await runtime.run_input_hooks("go", source="extension", streaming_behavior="steer")

    assert len(seen) == 1
    assert seen[0].source == "extension"
    assert seen[0].streaming_behavior == "steer"


async def test_agent_event_fan_out_and_wildcard(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "observer")
    specific: list[object] = []
    wildcard: list[object] = []
    api.on("tool_execution_start", lambda event, context: specific.append(event))
    api.on("agent_event", lambda event, context: wildcard.append(event))

    listeners: list[object] = []

    def subscribe(listener: object) -> object:
        listeners.append(listener)
        return lambda: listeners.remove(listener)

    runtime.attach_harness_listener(subscribe)  # type: ignore[arg-type]
    from tau_agent.events import ToolExecutionStartEvent, TurnStartEvent

    await listeners[0](ToolExecutionStartEvent(tool_call_id="1", tool_name="x", args={}))  # type: ignore[operator]
    await listeners[0](TurnStartEvent())  # type: ignore[operator]

    assert len(specific) == 1
    assert len(wildcard) == 2


async def test_extension_turn_events_include_pi_session_metadata(tmp_path: Path) -> None:
    from tau_agent.events import AgentStartEvent
    from tau_agent.events import TurnEndEvent as AgentTurnEndEvent
    from tau_agent.events import TurnStartEvent as AgentTurnStartEvent
    from tau_agent.messages import UserMessage
    from tau_coding.extensions import TurnEndEvent, TurnStartEvent

    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "turn_observer")
    specific: list[object] = []
    wildcard: list[object] = []
    api.on("turn_start", lambda event, context: specific.append(event))
    api.on("turn_end", lambda event, context: specific.append(event))
    api.on("agent_event", lambda event, context: wildcard.append(event))

    listeners: list[object] = []
    runtime.attach_harness_listener(  # type: ignore[arg-type]
        lambda listener: (listeners.append(listener), lambda: None)[1]
    )
    dispatch = listeners[0]
    message = UserMessage(content="done")

    await dispatch(AgentStartEvent())  # type: ignore[operator]
    before_ms = time.time_ns() // 1_000_000
    await dispatch(AgentTurnStartEvent())  # type: ignore[operator]
    await dispatch(AgentTurnEndEvent(message=message))  # type: ignore[operator]
    await dispatch(AgentTurnStartEvent())  # type: ignore[operator]

    first_start, first_end, second_start = specific
    assert isinstance(first_start, TurnStartEvent)
    assert before_ms <= first_start.timestamp <= time.time_ns() // 1_000_000
    assert first_start.turn_index == first_end.turn_index == 0
    assert isinstance(first_end, TurnEndEvent)
    assert first_end.message == message
    assert first_end.tool_results == []
    assert isinstance(second_start, TurnStartEvent)
    assert second_start.turn_index == 1
    assert wildcard[-3:] == specific


async def test_extension_turn_index_resets_for_each_agent_run(tmp_path: Path) -> None:
    from tau_agent.events import AgentStartEvent
    from tau_agent.events import TurnEndEvent as AgentTurnEndEvent
    from tau_agent.events import TurnStartEvent as AgentTurnStartEvent
    from tau_agent.messages import UserMessage
    from tau_coding.extensions import TurnStartEvent

    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "turn_observer")
    seen: list[TurnStartEvent] = []
    api.on("turn_start", lambda event, context: seen.append(event))
    listeners: list[object] = []
    runtime.attach_harness_listener(  # type: ignore[arg-type]
        lambda listener: (listeners.append(listener), lambda: None)[1]
    )
    dispatch = listeners[0]
    message = UserMessage(content="done")

    await dispatch(AgentStartEvent())  # type: ignore[operator]
    await dispatch(AgentTurnStartEvent())  # type: ignore[operator]
    await dispatch(AgentTurnEndEvent(message=message))  # type: ignore[operator]
    await dispatch(AgentStartEvent())  # type: ignore[operator]
    await dispatch(AgentTurnStartEvent())  # type: ignore[operator]

    assert [event.turn_index for event in seen] == [0, 0]


async def test_message_end_event_surfaces_provider_usage(tmp_path: Path) -> None:
    from typing import cast

    from tau_agent import Usage
    from tau_agent.events import MessageEndEvent
    from tau_coding.extensions.api import ExtensionAPI

    runtime = ExtensionRuntime()
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "usage_observer"))
    seen: list[object] = []
    api.on("message_end", lambda event, context: seen.append(event))

    listeners: list[object] = []

    def subscribe(listener: object) -> object:
        listeners.append(listener)
        return lambda: listeners.remove(listener)

    runtime.attach_harness_listener(subscribe)  # type: ignore[arg-type]

    usage = Usage(input=20, output=5, cache_read=10, reasoning=2, total_tokens=35)
    await listeners[0](  # type: ignore[operator]
        MessageEndEvent(message=AssistantMessage(content="done", usage=usage))
    )

    assert len(seen) == 1
    event = seen[0]
    assert isinstance(event, MessageEndEvent)
    assert isinstance(event.message, AssistantMessage)
    observed = event.message.usage
    assert observed is not None
    assert observed.input == 20
    assert observed.output == 5
    assert observed.cache_read == 10
    assert observed.reasoning == 2
    assert observed.total_tokens == 35


async def test_raising_event_handler_is_recorded(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "raiser")

    def handler(event: object, context: object) -> None:
        raise RuntimeError("listener exploded")

    api.on("turn_start", handler)
    listeners: list[object] = []
    runtime.attach_harness_listener(lambda fn: (listeners.append(fn), lambda: None)[1])  # type: ignore[arg-type]

    from tau_agent.events import TurnStartEvent

    await listeners[0](TurnStartEvent())  # type: ignore[operator]

    assert any("turn_start" in diag.message for diag in runtime.diagnostics)


# -- actions --------------------------------------------------------------------


def test_actions_raise_before_binding(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "early")

    with pytest.raises(ExtensionError):
        api.send_user_message("too early")


def test_send_user_message_steers_active_run(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "sender")
    session = RecordingSession(tmp_path, running=True)
    runtime.bind(session)

    api.send_user_message("steer it", deliver_as="steer")
    api.send_user_message("later")

    assert session.steered == ["steer it"]
    assert session.followed_up == ["later"]


def test_send_user_message_idle_uses_turn_callback(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "sender")
    session = RecordingSession(tmp_path, running=False)
    runtime.bind(session)
    delivered: list[tuple[str, str | None, dict[str, JSONValue] | None]] = []

    def record_turn(
        content: str,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        delivered.append((content, custom_type, details))

    runtime.set_turn_requested_callback(record_turn)

    api.send_user_message("run now")

    assert delivered == [("run now", None, None)]
    assert session.followed_up == []


def test_send_user_message_idle_without_callback_queues(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "sender")
    session = RecordingSession(tmp_path, running=False)
    runtime.bind(session)

    api.send_user_message("wait for next run")

    assert session.followed_up == ["wait for next run"]


async def test_append_entry_routes_to_session(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "persister")
    session = RecordingSession(tmp_path)
    runtime.bind(session)

    await api.append_entry("persister:record", {"value": 1})

    assert session.custom_entries == [("persister:record", {"value": 1})]


def test_transcript_is_empty_at_session_start(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "reader")
    session = RecordingSession(tmp_path)
    runtime.bind(session)

    assert api.context.transcript == ()  # type: ignore[attr-defined]


def test_transcript_exposes_prior_messages_in_order(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "reader")
    session = RecordingSession(tmp_path)
    session.messages = (
        UserMessage(content="what does foo do?"),
        AssistantMessage(content="foo returns bar"),
    )
    runtime.bind(session)

    transcript = api.context.transcript  # type: ignore[attr-defined]

    assert [message.role for message in transcript] == ["user", "assistant"]
    assert [message.text for message in transcript] == [
        "what does foo do?",
        "foo returns bar",
    ]


def test_transcript_returns_copies_so_mutation_cannot_corrupt_session(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "reader")
    session = RecordingSession(tmp_path)
    session.messages = (UserMessage(content="original"),)
    runtime.bind(session)

    transcript = api.context.transcript  # type: ignore[attr-defined]
    transcript[0].content = "tampered"

    assert session.messages[0].content == "original"


# -- UI dialogs ---------------------------------------------------------------


class RecordingUiBridge:
    """Test UI bridge that records dialog calls and returns canned answers.

    Satisfies the ``UiBridge`` protocol without a real frontend, so tests can
    exercise ``context.ui`` round-trips and cancel semantics deterministically.
    """

    def __init__(
        self,
        *,
        select_result: str | None = None,
        confirm_result: bool = False,
        input_result: str | None = None,
        has_ui: bool = True,
    ) -> None:
        self._select_result = select_result
        self._confirm_result = confirm_result
        self._input_result = input_result
        self._has_ui = has_ui
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.notifications: list[tuple[str, str]] = []
        self.interceptors: list[object] = []

    @property
    def has_ui(self) -> bool:
        return self._has_ui

    # -- component seam -------------------------------------------------------

    @property
    def supports_components(self) -> bool:
        return self._has_ui

    @property
    def theme(self):  # noqa: ANN202 - TuiTheme, imported lazily by the default
        from tau_coding.tui.config import TAU_DARK_THEME

        return TAU_DARK_THEME

    def get_prompt_text(self) -> str:
        return ""

    def request_render(self) -> None:
        self.calls.append(("request_render", (), {}))

    def set_slot_widget(self, key, factory, *, placement="above_prompt"):  # noqa: ANN001
        self.calls.append(("set_slot_widget", (key,), {"placement": placement}))

    def open_main_view(self, factory):  # noqa: ANN001
        self.calls.append(("open_main_view", (), {}))
        from tau_coding.extensions.api import _DeadMainViewHandle

        return _DeadMainViewHandle()

    def clear_components(self) -> None:
        self.calls.append(("clear_components", (), {}))

    def register_key_interceptor(self, handler):  # noqa: ANN001
        self.interceptors.append(handler)
        return lambda: self.interceptors.remove(handler)

    def notify(self, message: str, level: str = "info") -> None:
        self.notifications.append((message, level))

    async def select(
        self,
        title: str,
        options: object,
        *,
        timeout: float | None = None,
    ) -> str | None:
        self.calls.append(("select", (title, tuple(options)), {"timeout": timeout}))  # type: ignore[arg-type]
        return self._select_result

    async def confirm(
        self,
        title: str,
        message: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        self.calls.append(("confirm", (title, message), {"timeout": timeout}))
        return self._confirm_result

    async def input(
        self,
        title: str,
        placeholder: str = "",
        *,
        timeout: float | None = None,
    ) -> str | None:
        self.calls.append(("input", (title, placeholder), {"timeout": timeout}))
        return self._input_result


async def test_context_ui_round_trips_dialogs(tmp_path: Path) -> None:
    from typing import cast

    from tau_coding.extensions.api import ExtensionAPI

    ui = RecordingUiBridge(select_result="b", confirm_result=True, input_result="typed")
    runtime = ExtensionRuntime(ui=ui)
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "dialogs"))

    context = api.context
    assert context.ui.has_ui is True
    assert await context.ui.select("Pick", ["a", "b"], timeout=1.5) == "b"
    assert await context.ui.confirm("Sure?", "really") is True
    assert await context.ui.input("Name", "hint") == "typed"
    context.ui.notify("done", "warning")

    assert ui.calls == [
        ("select", ("Pick", ("a", "b")), {"timeout": 1.5}),
        ("confirm", ("Sure?", "really"), {"timeout": None}),
        ("input", ("Name", "hint"), {"timeout": None}),
    ]
    assert ui.notifications == [("done", "warning")]


async def test_context_ui_cancel_returns_pi_defaults(tmp_path: Path) -> None:
    from typing import cast

    from tau_coding.extensions.api import ExtensionAPI

    ui = RecordingUiBridge()  # select None, confirm False, input None
    runtime = ExtensionRuntime(ui=ui)
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "cancel"))

    assert await api.context.ui.select("t", ["x"]) is None
    assert await api.context.ui.confirm("t", "m") is False
    assert await api.context.ui.input("t") is None


async def test_headless_ui_bridges_return_pi_defaults(tmp_path: Path) -> None:
    from tau_coding.extensions import NullUiBridge, StderrUiBridge

    for bridge in (NullUiBridge(), StderrUiBridge()):
        assert bridge.has_ui is False
        assert await bridge.select("t", ["x"]) is None
        assert await bridge.confirm("t", "m") is False
        assert await bridge.input("t") is None


async def test_headless_ui_bridges_component_seam_are_noops(tmp_path: Path) -> None:
    from tau_coding.extensions import NullUiBridge, StderrUiBridge

    for bridge in (NullUiBridge(), StderrUiBridge()):
        assert bridge.supports_components is False
        # theme must return a usable default, never raise (print-mode may read).
        assert bridge.theme is not None
        assert bridge.theme.name
        assert bridge.get_prompt_text() == ""
        bridge.request_render()  # no-op, must not raise
        bridge.set_slot_widget("k", lambda theme: None, placement="above_prompt")
        handle = bridge.open_main_view(lambda h, theme: None)
        assert handle.is_open is False
        handle.close("ignored")  # accepts a result, does nothing with it
        handle.close()  # idempotent no-op
        # A dead handle never opens a view: wait() resolves to None at once
        # (never hangs) even though close was called.
        assert await asyncio.wait_for(handle.wait(), timeout=1.0) is None
        unsubscribe = bridge.register_key_interceptor(lambda event, text: False)
        unsubscribe()  # must not raise


async def test_context_ui_components_pass_through(tmp_path: Path) -> None:
    from typing import cast

    from tau_coding.extensions.api import ExtensionAPI

    ui = RecordingUiBridge()
    runtime = ExtensionRuntime(ui=ui)
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "components"))

    components = api.context.ui.components
    assert components is ui  # straight pass-through to the installed bridge
    assert components.supports_components is True

    components.set_slot_widget("fleet", lambda theme: None, placement="below_prompt")
    unsubscribe = components.register_key_interceptor(lambda event, text: False)
    assert ("set_slot_widget", ("fleet",), {"placement": "below_prompt"}) in ui.calls
    assert len(ui.interceptors) == 1
    unsubscribe()
    assert ui.interceptors == []


async def test_context_ui_components_headless_reports_unsupported(tmp_path: Path) -> None:
    from typing import cast

    from tau_coding.extensions.api import ExtensionAPI

    runtime = ExtensionRuntime()  # defaults to NullUiBridge
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "headless-components"))

    assert api.context.ui.components.supports_components is False


async def test_default_runtime_ui_is_headless(tmp_path: Path) -> None:
    from typing import cast

    from tau_coding.extensions.api import ExtensionAPI

    runtime = ExtensionRuntime()  # defaults to NullUiBridge
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "headless"))

    assert api.context.ui.has_ui is False
    assert await api.context.ui.select("t", ["a"]) is None
    assert await api.context.ui.confirm("t", "m") is False
    assert await api.context.ui.input("t") is None


async def test_sync_command_spawns_task_that_awaits_dialog(tmp_path: Path) -> None:
    """A sync /command drives an async dialog by spawning a loop task.

    Mirrors the adopted v1 pattern: command handlers stay sync, so a handler
    that needs a dialog schedules a coroutine on the running loop (available
    because handle_command runs on the event-loop thread) and returns at once.
    """
    import asyncio

    ui = RecordingUiBridge(select_result="deploy")
    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths),
        "menu_cmd",
        (
            "import asyncio\n\n\n"
            "def _handler(args, context):\n"
            "    async def _menu():\n"
            "        ui = context.api.context.ui\n"
            "        choice = await ui.select('Action', ['deploy', 'cancel'])\n"
            "        if choice is not None:\n"
            "            context.api.send_user_message(f'run {choice}')\n"
            "    asyncio.get_running_loop().create_task(_menu())\n"
            "    return 'opening menu...'\n\n\n"
            "def setup(tau):\n"
            "    tau.register_command('menu', _handler)\n"
        ),
    )

    runtime = ExtensionRuntime(ui=ui)
    runtime.load(paths)
    session = RecordingSession(tmp_path, running=True)
    runtime.bind(session)  # type: ignore[arg-type]
    registry = runtime.build_command_registry()

    command = registry.get("menu")
    assert command is not None

    result = command.handler(_command_context(registry, "/menu", "menu", ""))  # type: ignore[arg-type]
    assert result.handled is True
    assert result.message == "opening menu..."

    # Let the spawned dialog task run to completion.
    for _ in range(4):
        await asyncio.sleep(0)

    assert ui.calls == [("select", ("Action", ("deploy", "cancel")), {"timeout": None})]
    assert session.followed_up == ["run deploy"]


def _register_inline_extension(runtime: ExtensionRuntime, name: str) -> object:
    from tau_coding.extensions.loader import LoadedExtension

    captured: dict[str, object] = {}

    def setup(api: object) -> None:
        captured["api"] = api

    runtime._setup_extension(  # noqa: SLF001 - test seam
        LoadedExtension(name=name, path=Path(f"/virtual/{name}.py"), setup=setup)
    )
    return captured["api"]


def _make_tool(name: str, *, content: str) -> AgentTool:
    async def executor(
        tool_call_id: str, arguments: object, signal: object = None, on_update: object = None
    ) -> AgentToolResult:
        return AgentToolResult(content=content)

    return AgentTool(name=name, label=name, description="d", parameters={}, execute_fn=executor)


# -- coding-session integration ---------------------------------------------------


def _session_config(
    tmp_path: Path,
    provider: FakeProvider,
    *,
    extension_body: str | None = None,
) -> CodingSessionConfig:
    paths = _paths(tmp_path)
    if extension_body is not None:
        _write_extension(_user_extensions_dir(paths), "integration", extension_body)
    assert paths.cwd is not None
    paths.cwd.mkdir(parents=True, exist_ok=True)
    return CodingSessionConfig(
        provider=provider,
        model="fake",
        storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
        cwd=paths.cwd,
        resource_paths=paths,
    )


async def test_session_exposes_extension_tools_and_commands(tmp_path: Path) -> None:
    body = HELLO_TOOL_EXTENSION + (
        "\n\ndef _cmd(args, context):\n"
        "    return 'extension says hi'\n\n\n"
        "_original_setup = setup\n\n\n"
        "def setup(tau):\n"
        "    _original_setup(tau)\n"
        "    tau.register_command('hi', _cmd, description='Say hi.')\n"
    )
    session = await CodingSession.load(
        _session_config(tmp_path, FakeProvider([]), extension_body=body)
    )

    tool_names = [tool.name for tool in session.tools]
    assert "hello" in tool_names
    assert tool_names[:4] == ["read", "write", "edit", "bash"]

    result = session.handle_command("/hi")
    assert result.handled is True
    assert result.message == "extension says hi"

    assert "hello" in session.system_prompt


async def test_extension_guideline_reaches_system_prompt(tmp_path: Path) -> None:
    body = "def setup(tau):\n    tau.add_prompt_guideline('Never commit directly to main')\n"
    session = await CodingSession.load(
        _session_config(tmp_path, FakeProvider([]), extension_body=body)
    )

    assert "Never commit directly to main" in session.system_prompt


async def test_reload_picks_up_guideline_changes(tmp_path: Path) -> None:
    provider = FakeProvider([])
    session = await CodingSession.load(_session_config(tmp_path, provider))
    assert "Prefer uv over pip" not in session.system_prompt

    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths),
        "late_guideline",
        "def setup(tau):\n    tau.add_prompt_guideline('Prefer uv over pip')\n",
    )

    summary = await session.reload()

    assert summary.system_prompt_rebuilt is True
    assert "Prefer uv over pip" in session.system_prompt


async def test_session_start_deferred_until_host_emits(tmp_path: Path) -> None:
    body = (
        "EVENTS = []\n\n\n"
        "def setup(tau):\n"
        "    tau.on('session_start', lambda event, context: EVENTS.append(event.reason))\n"
    )
    session = await CodingSession.load(
        _session_config(tmp_path, FakeProvider([]), extension_body=body)
    )
    module = _loaded_extension_module("integration")

    # load defers session_start so hosts can attach a UI bridge first.
    assert module.EVENTS == []  # type: ignore[attr-defined]

    await session.emit_pending_session_start()
    assert module.EVENTS == ["startup"]  # type: ignore[attr-defined]

    # Idempotent: a second host call must not re-fire the event.
    await session.emit_pending_session_start()
    assert module.EVENTS == ["startup"]  # type: ignore[attr-defined]

    await session.aclose()
    assert module.EVENTS == ["startup", "quit"] or module.EVENTS == ["startup"]


async def test_session_start_handler_can_notify_through_attached_bridge(
    tmp_path: Path,
) -> None:
    body = (
        "def setup(tau):\n"
        "    tau.on('session_start', "
        "lambda event, context: tau.notify('loaded', 'info'))\n"
    )
    session = await CodingSession.load(
        _session_config(tmp_path, FakeProvider([]), extension_body=body)
    )
    ui = RecordingUiBridge()
    session.extension_runtime.set_ui_bridge(ui)

    await session.emit_pending_session_start()

    assert ui.notifications == [("loaded", "info")]
    await session.aclose()


async def test_input_handled_prevents_agent_run(tmp_path: Path) -> None:
    body = (
        "from tau_coding.extensions import InputHookResult\n\n\n"
        "def _hook(event, context):\n"
        "    if event.text.startswith('intercept'):\n"
        "        return InputHookResult(action='handled', message='caught')\n"
        "    return None\n\n\n"
        "def setup(tau):\n"
        "    tau.on('input', _hook)\n"
    )
    provider = FakeProvider([])
    session = await CodingSession.load(_session_config(tmp_path, provider, extension_body=body))

    events = [event async for event in session.prompt("intercept this")]

    assert events == []
    assert provider.calls == []
    assert session.messages == ()


async def test_prompt_input_hook_defaults_to_interactive(tmp_path: Path) -> None:
    # The plain session.prompt path (TUI idle submit, print mode) tags the
    # input hook with source="interactive" and no streaming behavior.
    body = (
        "from tau_coding.extensions import InputHookResult\n\n\n"
        "def _hook(event, context):\n"
        "    tag = f'{event.text}|src={event.source}|sb={event.streaming_behavior}'\n"
        "    return InputHookResult(action='transform', text=tag)\n\n\n"
        "def setup(tau):\n"
        "    tau.on('input', _hook)\n"
    )
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="ok")),
            ]
        ]
    )
    session = await CodingSession.load(_session_config(tmp_path, provider, extension_body=body))

    _ = [event async for event in session.prompt("hello")]

    user_messages = [m for m in session.messages if isinstance(m, UserMessage)]
    assert user_messages[0].content == "hello|src=interactive|sb=None"


async def test_prompt_input_hook_source_extension(tmp_path: Path) -> None:
    # source="extension" flows through prompt() to the input hook, mirroring the
    # TUI extension-initiated idle turn (send_user_message -> turn_requested).
    body = (
        "from tau_coding.extensions import InputHookResult\n\n\n"
        "def _hook(event, context):\n"
        "    return InputHookResult(action='transform', text=f'{event.text}|{event.source}')\n\n\n"
        "def setup(tau):\n"
        "    tau.on('input', _hook)\n"
    )
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="ok")),
            ]
        ]
    )
    session = await CodingSession.load(_session_config(tmp_path, provider, extension_body=body))

    _ = [event async for event in session.prompt("hello", source="extension")]

    user_messages = [m for m in session.messages if isinstance(m, UserMessage)]
    assert user_messages[0].content == "hello|extension"


async def test_agent_events_reach_extension_after_interrupted_tool_repair(
    tmp_path: Path,
) -> None:
    # Regression: load() attached the extension event fan-out to the local
    # harness it built before _persist_loaded_interrupted_tool_repairs() could
    # replace session._harness. Loading a session that died mid-tool-call then
    # left extensions subscribed to the discarded harness — they silently
    # received zero agent events for the whole session.
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

    body = (
        "EVENTS = []\n\n\n"
        "def setup(tau):\n"
        "    tau.on('agent_event', lambda event, context: EVENTS.append(event.type))\n"
    )
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="Recovered.")),
            ]
        ]
    )
    session = await CodingSession.load(_session_config(tmp_path, provider, extension_body=body))
    module = _loaded_extension_module("integration")

    _ = [event async for event in session.prompt("continue")]

    assert "agent_start" in module.EVENTS  # type: ignore[attr-defined]
    assert "agent_end" in module.EVENTS  # type: ignore[attr-defined]


async def test_extension_tool_call_block_reaches_model(tmp_path: Path) -> None:
    body = (
        "from tau_coding.extensions import ToolCallHookResult\n\n\n"
        "def _hook(event, context):\n"
        "    if event.tool_name == 'bash':\n"
        "        return ToolCallHookResult(block=True, reason='no shell today')\n"
        "    return None\n\n\n"
        "def setup(tau):\n"
        "    tau.on('tool_call', _hook)\n"
    )
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(
                    message=AssistantMessage(
                        content=assistant_content(
                            "", [ToolCall(id="call-1", name="bash", arguments={"command": "ls"})]
                        )
                    )
                ),
            ],
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="done")),
            ],
        ]
    )
    session = await CodingSession.load(_session_config(tmp_path, provider, extension_body=body))

    [event async for event in session.prompt("run ls")]

    tool_results = [
        message for message in session.messages if getattr(message, "role", None) == "toolResult"
    ]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is False
    assert "no shell today" in tool_results[0].text


async def test_custom_message_metadata_survives_session_reload(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [
                assistant_start(model="fake"),
                assistant_done(message=AssistantMessage(content="ack")),
            ],
        ]
    )
    session = await CodingSession.load(_session_config(tmp_path, provider))

    # A custom message that starts an idle session's turn persists its metadata.
    [
        event
        async for event in session.prompt(
            "<task-notification/>",
            custom_type="subagent-notification",
            details={"id": "run-1"},
        )
    ]
    await session.aclose()

    reopened = await CodingSession.load(_session_config(tmp_path, FakeProvider([])))
    custom = [
        message
        for message in reopened.messages
        if getattr(message, "role", None) == "custom"
        and message.custom_type == "subagent-notification"
    ]

    assert len(custom) == 1
    assert custom[0].details == {"id": "run-1"}


async def test_append_entry_persists_on_active_path(tmp_path: Path) -> None:
    body = HELLO_TOOL_EXTENSION
    provider = FakeProvider([])
    session = await CodingSession.load(_session_config(tmp_path, provider, extension_body=body))

    await session.append_custom_entry("test:records", {"value": 7})

    entries = await session.storage.read_all()
    custom = [entry for entry in entries if isinstance(entry, CustomEntry)]
    assert len(custom) == 1
    assert custom[0].namespace == "test:records"
    assert custom[0].data == {"value": 7}
    assert session.state.custom_entries


async def test_reload_picks_up_new_extension(tmp_path: Path) -> None:
    provider = FakeProvider([])
    config = _session_config(tmp_path, provider)
    session = await CodingSession.load(config)
    assert "hello" not in [tool.name for tool in session.tools]

    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "late_arrival", HELLO_TOOL_EXTENSION)

    summary = await session.reload()

    assert summary.extensions.after == 1
    assert "hello" in [tool.name for tool in session.tools]
    assert "hello" in session.system_prompt


async def test_runtime_survives_new_session_swap(tmp_path: Path) -> None:
    from tau_coding import SessionManager

    body = (
        "EVENTS = []\n\n\n"
        "def setup(tau):\n"
        "    tau.on('session_start', "
        "lambda event, context: EVENTS.append(('start', event.reason)))\n"
        "    tau.on('session_shutdown', "
        "lambda event, context: EVENTS.append(('stop', event.reason)))\n"
    )
    from dataclasses import replace as dataclass_replace

    from tau_coding import TauPaths

    provider = FakeProvider([])
    manager = SessionManager(
        TauPaths(home=tmp_path / "home-tau", agents_home=tmp_path / "home-agents")
    )
    config = _session_config(tmp_path, provider, extension_body=body)
    record = manager.create_session(cwd=config.cwd, model="fake")
    config = dataclass_replace(config, session_manager=manager, session_id=record.id)
    session = await CodingSession.load(config)
    runtime_before = session.extension_runtime

    await session.new_session()

    module = _loaded_extension_module("integration")
    assert session.extension_runtime is runtime_before
    assert ("stop", "new") in module.EVENTS
    assert ("start", "new") in module.EVENTS


async def test_session_swap_clears_host_extension_components(tmp_path: Path) -> None:
    # A session rebind (resume/new) must tear down extension-owned UI: slot
    # widgets, main views, and key interceptors from the previous session
    # otherwise survive the switch (session_start handlers can re-mount).
    from dataclasses import replace as dataclass_replace

    from tau_coding import SessionManager, TauPaths

    provider = FakeProvider([])
    manager = SessionManager(
        TauPaths(home=tmp_path / "home-tau", agents_home=tmp_path / "home-agents")
    )
    config = _session_config(tmp_path, provider)
    record = manager.create_session(cwd=config.cwd, model="fake")
    config = dataclass_replace(config, session_manager=manager, session_id=record.id)
    session = await CodingSession.load(config)
    ui = RecordingUiBridge()
    session.extension_runtime.set_ui_bridge(ui)

    await session.new_session()

    assert ("clear_components", (), {}) in ui.calls


def test_reset_for_reload_clears_host_extension_components() -> None:
    # /reload replaces the registration set; host-side extension UI (slot
    # widgets, main views, key interceptors) belongs to the stale generation
    # and must be torn down with it, or interceptors accumulate per reload.
    ui = RecordingUiBridge()
    runtime = ExtensionRuntime(ui=ui)
    _register_inline_extension(runtime, "old")

    runtime.reset_for_reload()

    assert ("clear_components", (), {}) in ui.calls


def test_reset_for_reload_invalidates_only_after_component_cleanup() -> None:
    runtime = ExtensionRuntime()
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "old"))
    observed_active: list[bool] = []

    class CleanupBridge(RecordingUiBridge):
        def clear_components(self) -> None:
            observed_active.append(api.name == "old")
            super().clear_components()

    runtime.set_ui_bridge(CleanupBridge())

    runtime.reset_for_reload()

    assert observed_active == [True]
    with pytest.raises(ExtensionError, match="stale after reload"):
        _ = api.name


# -- reload staleness guard (Pi's assertActive/invalidate) ---------------------


API_CAPTURING_EXTENSION = "APIS = []\n\n\ndef setup(tau):\n    APIS.append(tau)\n"


def test_reset_for_reload_invalidates_prior_api_actions(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "old"))
    runtime.bind(RecordingSession(tmp_path))

    runtime.reset_for_reload()

    with pytest.raises(ExtensionError, match="stale after reload"):
        api.send_user_message("zombie")
    with pytest.raises(ExtensionError, match="stale after reload"):
        api.register_tool(_make_tool("late", content="x"))
    with pytest.raises(ExtensionError, match="stale after reload"):
        api.on("input", lambda event, context: None)
    with pytest.raises(ExtensionError, match="stale after reload"):
        api.notify("zombie")


async def test_reset_for_reload_invalidates_prior_context_and_ui(tmp_path: Path) -> None:
    runtime = ExtensionRuntime(ui=RecordingUiBridge())
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "old"))
    runtime.bind(RecordingSession(tmp_path))
    context = api.context
    ui = context.ui
    assert context.cwd == tmp_path
    assert ui.has_ui is True

    runtime.reset_for_reload()

    with pytest.raises(ExtensionError, match="stale after reload"):
        _ = context.cwd
    with pytest.raises(ExtensionError, match="stale after reload"):
        _ = context.transcript
    # Trivial reads assert too (Pi asserts on everything).
    with pytest.raises(ExtensionError, match="stale after reload"):
        _ = context.has_ui
    with pytest.raises(ExtensionError, match="stale after reload"):
        ui.notify("zombie")
    with pytest.raises(ExtensionError, match="stale after reload"):
        await ui.select("Pick", ("a", "b"))
    # The component bridge is unreachable through a stale facade.
    with pytest.raises(ExtensionError, match="stale after reload"):
        _ = ui.components


async def test_reload_invalidates_old_instance_and_new_instance_works(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([])
    session = await CodingSession.load(
        _session_config(tmp_path, provider, extension_body=API_CAPTURING_EXTENSION)
    )
    old_module = _loaded_extension_module("integration")
    old_api = cast(ExtensionAPI, old_module.APIS[-1])  # type: ignore[attr-defined]
    assert old_api.context.cwd == session.cwd

    await session.reload()

    with pytest.raises(ExtensionError, match="stale after reload"):
        old_api.send_user_message("zombie")
    with pytest.raises(ExtensionError, match="stale after reload"):
        _ = old_api.context

    new_module = _loaded_extension_module("integration")
    new_api = cast(ExtensionAPI, new_module.APIS[-1])  # type: ignore[attr-defined]
    assert new_api is not old_api
    assert new_api.context.cwd == session.cwd
    new_api.send_user_message("fresh")  # the reloaded instance works normally


async def test_reload_awaits_shutdown_before_invalidation_and_starts_new_generation(
    tmp_path: Path,
) -> None:
    body = (
        "EVENTS = []\n\n"
        "def setup(tau):\n"
        "    @tau.on('session_shutdown')\n"
        "    async def stop(event, context):\n"
        "        EVENTS.append(('stop', event.reason, tau.name))\n"
        "    @tau.on('session_start')\n"
        "    async def start(event, context):\n"
        "        EVENTS.append(('start', event.reason, tau.name))\n"
        "        tau.context.ui.components.set_slot_widget('status', [event.reason])\n"
    )
    session = await CodingSession.load(
        _session_config(tmp_path, FakeProvider([]), extension_body=body)
    )
    ui = RecordingUiBridge()
    session.extension_runtime.set_ui_bridge(ui)
    old_module = _loaded_extension_module("integration")

    await session.emit_pending_session_start()
    assert old_module.EVENTS == [("start", "startup", "integration")]  # type: ignore[attr-defined]

    await session.reload()

    # The outgoing async shutdown completed while its API was active.
    assert old_module.EVENTS[-1] == ("stop", "reload", "integration")  # type: ignore[attr-defined]
    new_module = _loaded_extension_module("integration")
    assert new_module.EVENTS == [("start", "reload", "integration")]  # type: ignore[attr-defined]
    assert ui.calls[-2:] == [
        ("clear_components", (), {}),
        ("set_slot_widget", ("status",), {"placement": "above_prompt"}),
    ]


async def test_session_rebinding_does_not_invalidate_extension_instances(
    tmp_path: Path,
) -> None:
    from dataclasses import replace as dataclass_replace

    from tau_coding import SessionManager, TauPaths

    provider = FakeProvider([])
    manager = SessionManager(
        TauPaths(home=tmp_path / "home-tau", agents_home=tmp_path / "home-agents")
    )
    config = _session_config(tmp_path, provider, extension_body=API_CAPTURING_EXTENSION)
    record = manager.create_session(cwd=config.cwd, model="fake")
    config = dataclass_replace(config, session_manager=manager, session_id=record.id)
    session = await CodingSession.load(config)
    module = _loaded_extension_module("integration")
    api = cast(ExtensionAPI, module.APIS[-1])  # type: ignore[attr-defined]

    await session.new_session()

    # Same instance continues by design (setup did not re-run); its context
    # views simply reflect the newly bound session.
    assert len(module.APIS) == 1  # type: ignore[attr-defined]
    assert api.context.session_id == session.session_id
    api.send_user_message("still alive")  # must not raise


async def test_inflight_handler_touching_stale_api_records_diagnostic(
    tmp_path: Path,
) -> None:
    import asyncio

    runtime = ExtensionRuntime()
    api = cast(ExtensionAPI, _register_inline_extension(runtime, "background"))
    runtime.bind(RecordingSession(tmp_path))
    release = asyncio.Event()

    async def handler(event: object, context: object) -> None:
        del context
        await release.wait()
        api.send_user_message("zombie")  # generation went stale mid-flight

    api.on("input", handler)

    hooks = asyncio.ensure_future(runtime.run_input_hooks("hello"))
    await asyncio.sleep(0)  # let the handler start and park on the event
    runtime.reset_for_reload()
    release.set()
    outcome = await hooks

    # The ExtensionError is contained by the handler try/except and surfaces
    # as a normal runtime diagnostic instead of crashing dispatch.
    assert outcome.handled is False
    assert any("stale after reload" in diagnostic.message for diagnostic in runtime.diagnostics)


# -- custom message renderers -------------------------------------------------


def _inline_api(runtime: ExtensionRuntime, name: str) -> ExtensionAPI:
    return cast(ExtensionAPI, _register_inline_extension(runtime, name))


def test_render_custom_message_uses_registered_renderer(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "notifier")
    seen: list[tuple[CustomMessageView, MessageRenderOptions]] = []

    def render(view: CustomMessageView, options: MessageRenderOptions) -> str:
        seen.append((view, options))
        return f"[bold]{view.details['label'] if view.details else view.content}[/bold]"

    api.register_message_renderer("subagent-notification", render)

    markup = runtime.render_custom_message("subagent-notification", "raw", {"label": "done"}, True)

    assert markup == "[bold]done[/bold]"
    assert seen[0][0].custom_type == "subagent-notification"
    assert seen[0][0].content == "raw"
    assert seen[0][1].expanded is True


def test_render_custom_message_returns_none_when_unregistered(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    _inline_api(runtime, "notifier")

    assert runtime.render_custom_message("unknown", "raw", None, False) is None


def test_register_message_renderer_first_registration_wins(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    first = _inline_api(runtime, "first")
    second = _inline_api(runtime, "second")
    first.register_message_renderer("shared", lambda view, options: "first")
    second.register_message_renderer("shared", lambda view, options: "second")

    assert runtime.render_custom_message("shared", "x", None, False) == "first"


def test_render_custom_message_swallows_renderer_errors(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "boom")

    def render(view: CustomMessageView, options: MessageRenderOptions) -> str:
        raise RuntimeError("renderer exploded")

    api.register_message_renderer("boom", render)

    assert runtime.render_custom_message("boom", "raw content", None, False) is None
    assert any("message_renderer:boom" in d.message for d in runtime.diagnostics)


def test_render_custom_message_reports_failure_once_per_custom_type(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "boom")

    def render(view: CustomMessageView, options: MessageRenderOptions) -> str:
        raise RuntimeError("renderer exploded")

    api.register_message_renderer("boom", render)

    # Render paths re-run on every redraw; a persistently-broken renderer must
    # not grow the diagnostics list without bound.
    for _ in range(5):
        assert runtime.render_custom_message("boom", "raw", None, False) is None

    failures = [d for d in runtime.diagnostics if "message_renderer:boom" in d.message]
    assert len(failures) == 1


# -- tool-call renderers --------------------------------------------------------


async def _idle_executor(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
    return AgentToolResult(content="")


def _renderable_tool(name: str, render_call=None, render_result=None) -> AgentTool:  # noqa: ANN001
    return AgentTool(
        name=name,
        label=name,
        description="a tool",
        parameters={"type": "object"},
        execute_fn=_idle_executor,
        render_call=render_call,
        render_result=render_result,
    )


def test_render_tool_call_uses_tool_renderer(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "subagents")
    seen: list[dict[str, JSONValue]] = []

    def render(arguments) -> str:  # noqa: ANN001
        seen.append(dict(arguments))
        return f"▸ agent · {arguments.get('description')}"

    api.register_tool(_renderable_tool("agent", render))

    line = runtime.render_tool_call("agent", {"description": "Summarize codebase"})

    assert line == "▸ agent · Summarize codebase"
    assert seen == [{"description": "Summarize codebase"}]


def test_render_tool_call_returns_none_without_renderer(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "subagents")
    api.register_tool(_renderable_tool("agent"))

    assert runtime.render_tool_call("agent", {}) is None
    assert runtime.render_tool_call("unregistered", {}) is None


def test_render_tool_call_swallows_errors_and_reports_once(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "boom")

    def render(arguments) -> str:  # noqa: ANN001
        raise RuntimeError("renderer exploded")

    api.register_tool(_renderable_tool("boom-tool", render))

    for _ in range(5):
        assert runtime.render_tool_call("boom-tool", {}) is None

    failures = [d for d in runtime.diagnostics if "render_call:boom-tool" in d.message]
    assert len(failures) == 1


def test_render_tool_call_rejects_non_string_result(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "subagents")
    api.register_tool(_renderable_tool("agent", lambda arguments: 42))

    assert runtime.render_tool_call("agent", {}) is None
    assert any("render_call:agent" in d.message for d in runtime.diagnostics)


def _tool_result(name: str, *, ok: bool = True) -> AgentToolResult:
    return AgentToolResult(
        content="raw result",
        details={"description": "Summarize codebase"},
    )


def test_render_tool_result_uses_tool_renderer(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "subagents")
    seen: list[tuple[str, bool]] = []

    def render(result, *, expanded) -> str:  # noqa: ANN001
        seen.append((str(result.details["description"]), expanded))
        return "✓ completed · 3 tool uses"

    api.register_tool(_renderable_tool("agent", render_result=render))

    markup = runtime.render_tool_result("agent", _tool_result("agent"), False)

    assert markup == "✓ completed · 3 tool uses"
    assert seen == [("Summarize codebase", False)]
    assert runtime.render_tool_result("agent", _tool_result("agent"), True) is not None
    assert seen[-1] == ("Summarize codebase", True)


def test_render_tool_result_returns_none_without_renderer(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "subagents")
    api.register_tool(_renderable_tool("agent"))

    assert runtime.render_tool_result("agent", _tool_result("agent"), False) is None
    assert runtime.render_tool_result("unregistered", _tool_result("unregistered"), False) is None


def test_render_tool_result_swallows_errors_and_reports_once(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "boom")

    def render(result, *, expanded) -> str:  # noqa: ANN001
        raise RuntimeError("renderer exploded")

    api.register_tool(_renderable_tool("boom-tool", render_result=render))

    for _ in range(5):
        assert runtime.render_tool_result("boom-tool", _tool_result("boom-tool"), False) is None

    failures = [d for d in runtime.diagnostics if "render_result:boom-tool" in d.message]
    assert len(failures) == 1


def test_render_tool_result_rejects_non_string_result(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "subagents")
    api.register_tool(_renderable_tool("agent", render_result=lambda result, *, expanded: 42))

    assert runtime.render_tool_result("agent", _tool_result("agent"), False) is None
    assert any("render_result:agent" in d.message for d in runtime.diagnostics)


def test_render_tool_result_failures_do_not_shadow_render_call(tmp_path: Path) -> None:
    # The two renderers share the once-per-name dedup set; a broken
    # render_result must not swallow the diagnostic for a broken render_call.
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "boom")

    def bad_call(arguments) -> str:  # noqa: ANN001
        raise RuntimeError("call renderer exploded")

    def bad_result(result, *, expanded) -> str:  # noqa: ANN001
        raise RuntimeError("result renderer exploded")

    api.register_tool(_renderable_tool("agent", render_call=bad_call, render_result=bad_result))

    assert runtime.render_tool_result("agent", _tool_result("agent"), False) is None
    assert runtime.render_tool_call("agent", {}) is None
    assert any("render_result:agent" in d.message for d in runtime.diagnostics)
    assert any("render_call:agent" in d.message for d in runtime.diagnostics)


def test_render_custom_message_rejects_non_string_result(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "wrong")
    # A renderer that returns a non-string (e.g. a widget) must not reach the UI.
    api.register_message_renderer(
        "wrong",
        cast("object", lambda view, options: 123),  # type: ignore[arg-type]
    )

    assert runtime.render_custom_message("wrong", "raw", None, False) is None


def test_message_renderers_cleared_on_reload(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "notifier")
    api.register_message_renderer("t", lambda view, options: "rendered")
    assert runtime.render_custom_message("t", "x", None, False) == "rendered"

    runtime.reset_for_reload()

    assert runtime.render_custom_message("t", "x", None, False) is None


def test_send_custom_message_queues_metadata_while_running(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "notifier")
    session = RecordingSession(tmp_path, running=True)
    runtime.bind(session)

    api.send_custom_message(
        "<task-notification/>",
        custom_type="subagent-notification",
        details={"id": "run-1"},
    )

    assert session.queued_custom == [
        ("<task-notification/>", "subagent-notification", {"id": "run-1"})
    ]


def test_send_custom_message_idle_delivers_metadata_via_turn_callback(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "notifier")
    session = RecordingSession(tmp_path, running=False)
    runtime.bind(session)
    delivered: list[tuple[str, str | None, dict[str, JSONValue] | None]] = []

    def record_turn(
        content: str,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        delivered.append((content, custom_type, details))

    runtime.set_turn_requested_callback(record_turn)

    api.send_custom_message("body", custom_type="c", details={"n": 1})

    assert delivered == [("body", "c", {"n": 1})]


def test_send_custom_message_without_trigger_turn_queues(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _inline_api(runtime, "notifier")
    session = RecordingSession(tmp_path, running=False)
    runtime.bind(session)

    def record_turn(
        content: str,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        raise AssertionError("turn callback should not fire when trigger_turn=False")

    runtime.set_turn_requested_callback(record_turn)

    api.send_custom_message("body", custom_type="c", trigger_turn=False)

    assert session.queued_custom == [("body", "c", None)]


def _loaded_extension_module(name: str) -> object:
    candidates = [
        module
        for module_name, module in sys.modules.items()
        if module_name.startswith(f"tau_extension_{name}") and "." not in module_name
    ]
    assert candidates, f"extension module {name} not loaded"
    return candidates[-1]
