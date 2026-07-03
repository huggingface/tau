"""Tests for extension discovery, loading, hooks, and session wiring."""

import sys
from pathlib import Path

import pytest

from tau_agent import AssistantMessage, ToolCall
from tau_agent.session import CustomEntry, JsonlSessionStorage
from tau_agent.tools import AgentTool, AgentToolResult
from tau_ai import FakeProvider, ProviderResponseEndEvent, ProviderResponseStartEvent
from tau_coding import CodingSession, CodingSessionConfig, TauResourcePaths
from tau_coding.extensions import (
    ExtensionError,
    ExtensionRuntime,
    InputHookResult,
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


async def _run(arguments, signal=None):
    return AgentToolResult(
        tool_call_id="",
        name="hello",
        ok=True,
        content=f"hello {arguments.get('who', 'world')}",
    )


def setup(tau):
    tau.register_tool(
        AgentTool(
            name="hello",
            description="Say hello.",
            input_schema={"type": "object", "properties": {"who": {"type": "string"}}},
            executor=_run,
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
        self.steered: list[str] = []
        self.followed_up: list[str] = []
        self.custom_entries: list[tuple[str, dict[str, object]]] = []

    def queue_steering_message(self, content: str) -> None:
        self.steered.append(content)

    def queue_follow_up_message(self, content: str) -> None:
        self.followed_up.append(content)

    async def append_custom_entry(self, namespace: str, data: dict[str, object]) -> None:
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
        name.startswith("tau_extension_pkg_ns") and name.endswith(".helper")
        for name in sys.modules
    )


def test_broken_extension_is_isolated(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(_user_extensions_dir(paths), "broken", "raise RuntimeError('boom')\n")
    _write_extension(_user_extensions_dir(paths), "works", "def setup(tau):\n    pass\n")

    result = load_extensions(paths)

    assert [ext.name for ext in result.extensions] == ["works"]
    assert any(
        diag.name == "broken" and diag.severity == "error" for diag in result.diagnostics
    )


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
            "async def _run(arguments, signal=None):\n"
            "    return AgentToolResult(tool_call_id='', name='t', ok=True, content='x')\n\n\n"
            "def setup(tau):\n"
            "    tau.register_tool(AgentTool(name='t', description='d',"
            " input_schema={}, executor=_run))\n"
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
            "async def _run(arguments, signal=None):\n"
            "    return AgentToolResult(tool_call_id='', name='read', ok=True,"
            " content='intercepted')\n\n\n"
            "def setup(tau):\n"
            "    tau.register_tool(AgentTool(name='read', description='replacement',"
            " input_schema={}, executor=_run))\n"
        ),
    )

    async def builtin_read(arguments: object, signal: object = None) -> AgentToolResult:
        return AgentToolResult(tool_call_id="", name="read", ok=True, content="builtin")

    builtin = AgentTool(
        name="read", description="builtin", input_schema={}, executor=builtin_read
    )

    runtime = _runtime_with(paths)
    composed = runtime.compose_tools([builtin])

    assert [tool.name for tool in composed] == ["read"]
    result = await composed[0].execute({})
    assert result.content == "intercepted"


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
        (
            "def setup(tau):\n"
            "    tau.register_command('model', lambda args, context: 'hijacked')\n"
        ),
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


def test_unknown_event_subscription_is_a_diagnostic(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_extension(
        _user_extensions_dir(paths),
        "bad_event",
        "def setup(tau):\n    tau.on('no_such_event', lambda event: None)\n",
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
        lambda event: ToolCallHookResult(block=True, reason="not allowed")
        if event.tool_name == "danger"
        else None,
    )

    tool = _make_tool("danger", content="ran")
    wrapped = runtime.compose_tools([tool])[0]
    result = await wrapped.execute({})

    assert result.ok is False
    assert "not allowed" in result.content


async def test_tool_call_hook_can_rewrite_arguments(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "rewrite")
    api.on("tool_call", lambda event: ToolCallHookResult(arguments={"who": "tau"}))

    seen: list[dict[str, object]] = []

    async def executor(arguments: object, signal: object = None) -> AgentToolResult:
        seen.append(dict(arguments))  # type: ignore[call-overload]
        return AgentToolResult(tool_call_id="", name="echo", ok=True, content="ok")

    tool = AgentTool(name="echo", description="d", input_schema={}, executor=executor)
    wrapped = runtime.compose_tools([tool])[0]
    await wrapped.execute({"who": "world"})

    assert seen == [{"who": "tau"}]


async def test_raising_tool_call_hook_blocks_fail_safe(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "raiser")

    def bad_hook(event: object) -> None:
        raise RuntimeError("hook exploded")

    api.on("tool_call", bad_hook)

    wrapped = runtime.compose_tools([_make_tool("x", content="ran")])[0]
    result = await wrapped.execute({})

    assert result.ok is False
    assert "hook failed" in result.content


async def test_tool_result_hook_transforms_result(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "transform")
    api.on("tool_result", lambda event: ToolResultHookResult(content="redacted"))

    wrapped = runtime.compose_tools([_make_tool("x", content="secret")])[0]
    result = await wrapped.execute({})

    assert result.content == "redacted"
    assert result.ok is True


async def test_raising_tool_result_hook_keeps_result(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "raiser")

    def bad_hook(event: object) -> None:
        raise RuntimeError("result hook exploded")

    api.on("tool_result", bad_hook)

    wrapped = runtime.compose_tools([_make_tool("x", content="fine")])[0]
    result = await wrapped.execute({})

    assert result.ok is True
    assert result.content == "fine"
    assert any("tool_result" in diag.message for diag in runtime.diagnostics)


async def test_input_hooks_chain_transforms(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "chain")
    api.on(
        "input",
        lambda event: InputHookResult(action="transform", text=event.text + " one"),
    )
    api.on(
        "input",
        lambda event: InputHookResult(action="transform", text=event.text + " two"),
    )

    outcome = await runtime.run_input_hooks("base")

    assert outcome.handled is False
    assert outcome.text == "base one two"


async def test_input_hook_handled_short_circuits(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "handler")
    api.on("input", lambda event: InputHookResult(action="handled", message="done"))
    api.on("input", lambda event: InputHookResult(action="transform", text="never"))

    outcome = await runtime.run_input_hooks("base")

    assert outcome.handled is True
    assert outcome.message == "done"


async def test_agent_event_fan_out_and_wildcard(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "observer")
    specific: list[object] = []
    wildcard: list[object] = []
    api.on("tool_execution_start", specific.append)
    api.on("agent_event", wildcard.append)

    listeners: list[object] = []

    def subscribe(listener: object) -> object:
        listeners.append(listener)
        return lambda: listeners.remove(listener)

    runtime.attach_harness_listener(subscribe)  # type: ignore[arg-type]
    from tau_agent.events import ToolExecutionStartEvent, TurnStartEvent

    await listeners[0](ToolExecutionStartEvent(tool_call=ToolCall(id="1", name="x")))  # type: ignore[operator]
    await listeners[0](TurnStartEvent(turn=1))  # type: ignore[operator]

    assert len(specific) == 1
    assert len(wildcard) == 2


async def test_raising_event_handler_is_recorded(tmp_path: Path) -> None:
    runtime = ExtensionRuntime()
    api = _register_inline_extension(runtime, "raiser")

    def handler(event: object) -> None:
        raise RuntimeError("listener exploded")

    api.on("turn_start", handler)
    listeners: list[object] = []
    runtime.attach_harness_listener(lambda fn: (listeners.append(fn), lambda: None)[1])  # type: ignore[arg-type]

    from tau_agent.events import TurnStartEvent

    await listeners[0](TurnStartEvent(turn=1))  # type: ignore[operator]

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
    delivered: list[str] = []
    runtime.set_turn_requested_callback(delivered.append)

    api.send_user_message("run now")

    assert delivered == ["run now"]
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
    async def executor(arguments: object, signal: object = None) -> AgentToolResult:
        return AgentToolResult(tool_call_id="", name=name, ok=True, content=content)

    return AgentTool(name=name, description="d", input_schema={}, executor=executor)


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


async def test_session_start_event_fires_on_load(tmp_path: Path) -> None:
    body = (
        "EVENTS = []\n\n\n"
        "def setup(tau):\n"
        "    tau.on('session_start', lambda event: EVENTS.append(event.reason))\n"
    )
    session = await CodingSession.load(
        _session_config(tmp_path, FakeProvider([]), extension_body=body)
    )

    module = _loaded_extension_module("integration")
    assert module.EVENTS == ["startup"]
    await session.aclose()
    assert module.EVENTS == ["startup", "quit"] or module.EVENTS == ["startup"]


async def test_input_handled_prevents_agent_run(tmp_path: Path) -> None:
    body = (
        "from tau_coding.extensions import InputHookResult\n\n\n"
        "def _hook(event):\n"
        "    if event.text.startswith('intercept'):\n"
        "        return InputHookResult(action='handled', message='caught')\n"
        "    return None\n\n\n"
        "def setup(tau):\n"
        "    tau.on('input', _hook)\n"
    )
    provider = FakeProvider([])
    session = await CodingSession.load(
        _session_config(tmp_path, provider, extension_body=body)
    )

    events = [event async for event in session.prompt("intercept this")]

    assert events == []
    assert provider.calls == []
    assert session.messages == ()


async def test_extension_tool_call_block_reaches_model(tmp_path: Path) -> None:
    body = (
        "from tau_coding.extensions import ToolCallHookResult\n\n\n"
        "def _hook(event):\n"
        "    if event.tool_name == 'bash':\n"
        "        return ToolCallHookResult(block=True, reason='no shell today')\n"
        "    return None\n\n\n"
        "def setup(tau):\n"
        "    tau.on('tool_call', _hook)\n"
    )
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(
                    message=AssistantMessage(
                        content="",
                        tool_calls=[
                            ToolCall(id="call-1", name="bash", arguments={"command": "ls"})
                        ],
                    )
                ),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="done")),
            ],
        ]
    )
    session = await CodingSession.load(
        _session_config(tmp_path, provider, extension_body=body)
    )

    [event async for event in session.prompt("run ls")]

    tool_results = [
        message
        for message in session.messages
        if getattr(message, "role", None) == "tool"
    ]
    assert len(tool_results) == 1
    assert tool_results[0].ok is False
    assert "no shell today" in tool_results[0].content


async def test_append_entry_persists_on_active_path(tmp_path: Path) -> None:
    body = HELLO_TOOL_EXTENSION
    provider = FakeProvider([])
    session = await CodingSession.load(
        _session_config(tmp_path, provider, extension_body=body)
    )

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

    summary = session.reload()

    assert summary.extensions.after == 1
    assert "hello" in [tool.name for tool in session.tools]
    assert "hello" in session.system_prompt


async def test_runtime_survives_new_session_swap(tmp_path: Path) -> None:
    from tau_coding import SessionManager

    body = (
        "EVENTS = []\n\n\n"
        "def setup(tau):\n"
        "    tau.on('session_start', lambda event: EVENTS.append(('start', event.reason)))\n"
        "    tau.on('session_shutdown', lambda event: EVENTS.append(('stop', event.reason)))\n"
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


# -- subagents example extension ----------------------------------------------------


EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples" / "extensions"


def test_subagents_example_extension_loads(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    runtime = ExtensionRuntime()
    runtime.load(
        paths,
        extra_paths=(EXAMPLES_DIR / "subagents",),
        include_resource_dirs=False,
    )

    assert runtime.extension_names == ("subagents",)
    assert {tool.name for tool in runtime.extension_tools} == {
        "agent",
        "get_subagent_result",
    }
    registry = runtime.build_command_registry()
    assert registry.get("agents") is not None
    assert not [diag for diag in runtime.diagnostics if diag.severity == "error"]


def test_hello_and_permission_gate_examples_load(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    runtime = ExtensionRuntime()
    runtime.load(
        paths,
        extra_paths=(
            EXAMPLES_DIR / "hello_tool.py",
            EXAMPLES_DIR / "permission_gate.py",
        ),
        include_resource_dirs=False,
    )

    assert runtime.extension_names == ("hello_tool", "permission_gate")
    assert [tool.name for tool in runtime.extension_tools] == ["hello"]
    assert not [diag for diag in runtime.diagnostics if diag.severity == "error"]


async def test_subagents_foreground_run_returns_result(tmp_path: Path) -> None:
    from types import SimpleNamespace

    paths = _paths(tmp_path)
    runtime = ExtensionRuntime()
    runtime.load(
        paths,
        extra_paths=(EXAMPLES_DIR / "subagents",),
        include_resource_dirs=False,
    )
    session = RecordingSession(tmp_path)
    runtime.bind(session)

    module = _loaded_extension_module("subagents")
    module.load_provider_settings = lambda: None  # type: ignore[attr-defined]
    module.resolve_provider_selection = (  # type: ignore[attr-defined]
        lambda settings, model=None: SimpleNamespace(
            provider=SimpleNamespace(name="fake"),
            model="fake",
        )
    )
    module.create_model_provider = (  # type: ignore[attr-defined]
        lambda provider, model, thinking_level: FakeProvider(
            [
                [
                    ProviderResponseStartEvent(model="fake"),
                    ProviderResponseEndEvent(
                        message=AssistantMessage(content="Subagent report: all good.")
                    ),
                ]
            ]
        )
    )

    agent_tool = next(tool for tool in runtime.extension_tools if tool.name == "agent")
    result = await agent_tool.execute(
        {"prompt": "Investigate the repo", "description": "investigate repo"}
    )

    assert result.ok is True
    assert "Subagent report: all good." in result.content
    assert "agent-1 [completed]" in result.content


async def test_subagents_background_run_delivers_notification(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace

    paths = _paths(tmp_path)
    runtime = ExtensionRuntime()
    runtime.load(
        paths,
        extra_paths=(EXAMPLES_DIR / "subagents",),
        include_resource_dirs=False,
    )
    session = RecordingSession(tmp_path)
    runtime.bind(session)

    module = _loaded_extension_module("subagents")
    module.load_provider_settings = lambda: None  # type: ignore[attr-defined]
    module.resolve_provider_selection = (  # type: ignore[attr-defined]
        lambda settings, model=None: SimpleNamespace(
            provider=SimpleNamespace(name="fake"),
            model="fake",
        )
    )
    module.create_model_provider = (  # type: ignore[attr-defined]
        lambda provider, model, thinking_level: FakeProvider(
            [
                [
                    ProviderResponseStartEvent(model="fake"),
                    ProviderResponseEndEvent(message=AssistantMessage(content="Done.")),
                ]
            ]
        )
    )

    agent_tool = next(tool for tool in runtime.extension_tools if tool.name == "agent")
    spawn_result = await agent_tool.execute(
        {
            "prompt": "Long task",
            "description": "long task",
            "run_in_background": True,
        }
    )
    assert spawn_result.ok is True
    assert "agent-1" in spawn_result.content

    for _ in range(200):
        if session.followed_up:
            break
        await asyncio.sleep(0.01)

    assert session.followed_up, "background completion should deliver a follow-up"
    assert "<task-notification>" in session.followed_up[0]
    assert "Done." in session.followed_up[0]


def _loaded_extension_module(name: str) -> object:
    candidates = [
        module
        for module_name, module in sys.modules.items()
        if module_name.startswith(f"tau_extension_{name}") and "." not in module_name
    ]
    assert candidates, f"extension module {name} not loaded"
    return candidates[-1]
