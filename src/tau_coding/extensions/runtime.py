"""Extension runtime: hook dispatch, tool wrapping, and session binding."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import Literal, Protocol

from tau_agent.events import AgentEvent
from tau_agent.messages import AgentMessage
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue
from tau_coding.commands import (
    CommandContext,
    CommandRegistry,
    CommandResult,
    SlashCommand,
    create_default_command_registry,
)
from tau_coding.extensions.api import (
    AGENT_EVENT_TYPES,
    AGENT_EVENT_WILDCARD,
    LIFECYCLE_EVENT_TYPES,
    CustomMessageView,
    ExtensionAPI,
    ExtensionCommandContext,
    ExtensionCommandHandler,
    ExtensionError,
    ExtensionGeneration,
    ExtensionHandler,
    InputEvent,
    InputHookResult,
    MessageRenderer,
    MessageRenderOptions,
    NullUiBridge,
    RegisteredExtension,
    SessionLifecycleReason,
    SessionShutdownEvent,
    SessionStartEvent,
    ToolCallHookEvent,
    ToolCallHookResult,
    ToolResultHookEvent,
    ToolResultHookResult,
    UiBridge,
)
from tau_coding.extensions.loader import (
    LoadedExtension,
    load_extensions,
    unload_extension_modules,
)
from tau_coding.resources import ResourceDiagnostic, TauResourcePaths

# Host callback that delivers a message through the frontend's serialized run
# path when the session is idle. Carries the same presentation metadata as a
# queued message so custom messages render correctly whether they trigger a new
# turn or are injected into a running one.
TurnRequestedCallback = Callable[[str, "str | None", "dict[str, JSONValue] | None"], None]


class BoundSession(Protocol):
    """The slice of `CodingSession` the extension runtime binds to."""

    @property
    def cwd(self) -> Path: ...

    @property
    def model(self) -> str: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def system_prompt(self) -> str: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def messages(self) -> tuple[AgentMessage, ...]: ...

    def queue_steering_message(
        self,
        content: str,
        *,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None: ...

    def queue_follow_up_message(
        self,
        content: str,
        *,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None: ...

    async def append_custom_entry(self, namespace: str, data: dict[str, JSONValue]) -> None: ...


@dataclass(frozen=True, slots=True)
class ExtensionCommand:
    """A slash command registered by an extension."""

    extension: str
    name: str
    description: str
    usage: str
    aliases: tuple[str, ...]
    handler: ExtensionCommandHandler


@dataclass(frozen=True, slots=True)
class RegisteredExtensionTool:
    """A tool registered by an extension."""

    extension: str
    tool: AgentTool


@dataclass(frozen=True, slots=True)
class InputHookOutcome:
    """Combined outcome of running all `input` hooks over prompt text."""

    handled: bool
    text: str
    message: str | None = None


class ExtensionRuntime:
    """Owns loaded extensions and dispatches events between them and a session.

    The runtime outlives any single `CodingSession`: session replacement flows
    (resume, new, branch) re-bind the same runtime rather than re-running
    extension discovery and `setup`. `/reload`, by contrast, replaces the
    registration set and invalidates the previous extension generation (see
    `reset_for_reload`), so pre-reload API objects fail loudly.
    """

    def __init__(self, *, ui: UiBridge | None = None) -> None:
        self._extensions: list[RegisteredExtension] = []
        self._tools: dict[str, RegisteredExtensionTool] = {}
        self._commands: dict[str, ExtensionCommand] = {}
        self._prompt_guidelines: list[tuple[str, str]] = []
        self._message_renderers: dict[str, tuple[str, MessageRenderer]] = {}
        self._renderer_failures_reported: set[str] = set()
        self._load_diagnostics: list[ResourceDiagnostic] = []
        self._runtime_diagnostics: list[ResourceDiagnostic] = []
        self._session: BoundSession | None = None
        self._ui: UiBridge = ui or NullUiBridge()
        self._turn_requested: TurnRequestedCallback | None = None
        self._harness_unsubscribe: Callable[[], None] | None = None
        self._generation = ExtensionGeneration()

    # -- loading -----------------------------------------------------------

    def load(
        self,
        paths: TauResourcePaths,
        *,
        extra_paths: Sequence[Path] = (),
        include_resource_dirs: bool = True,
        include_project_dir: bool = False,
    ) -> None:
        """Discover extensions and run each `setup` with an isolated API."""
        result = load_extensions(
            paths,
            extra_paths=extra_paths,
            include_resource_dirs=include_resource_dirs,
            include_project_dir=include_project_dir,
        )
        self._load_diagnostics.extend(result.diagnostics)
        for extension in result.extensions:
            self._setup_extension(extension)

    def reset_for_reload(self) -> None:
        """Drop all registrations and imported modules ahead of a re-load.

        Also invalidates the current extension generation (Pi's ``invalidate``
        parity): any `tau` API object, context, or ui facade captured before
        the reload — including one held by a still-running background task —
        raises :class:`ExtensionError` on its next use instead of acting
        against the fresh registration set. Session rebinding does not come
        through here and never invalidates.
        """
        # Host-side extension UI (slot widgets, main views, key interceptors)
        # belongs to the outgoing generation. Tear it down while that generation
        # is still active so host cleanup triggered by component disposal can
        # safely use its API; only then make every captured API/context stale.
        self.clear_ui_components()
        self._generation.invalidate()
        self._generation = ExtensionGeneration()
        if self._harness_unsubscribe is not None:
            self._harness_unsubscribe()
            self._harness_unsubscribe = None
        self._extensions.clear()
        self._tools.clear()
        self._commands.clear()
        self._prompt_guidelines.clear()
        self._message_renderers.clear()
        self._renderer_failures_reported.clear()
        self._load_diagnostics.clear()
        self._runtime_diagnostics.clear()
        unload_extension_modules()

    def _setup_extension(self, extension: LoadedExtension) -> None:
        api = ExtensionAPI(self, extension.name, self._generation)
        registered = RegisteredExtension(name=extension.name, path=extension.path, api=api)
        self._extensions.append(registered)
        try:
            extension.setup(api)
        except Exception as exc:  # noqa: BLE001 - extensions are an isolation boundary
            self._extensions.remove(registered)
            self._remove_registrations(extension.name)
            self._load_diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    name=extension.name,
                    path=extension.path,
                    message=f"setup failed: {exc!r}",
                    severity="error",
                )
            )

    def _remove_registrations(self, extension_name: str) -> None:
        self._tools = {
            name: registration
            for name, registration in self._tools.items()
            if registration.extension != extension_name
        }
        self._commands = {
            name: command
            for name, command in self._commands.items()
            if command.extension != extension_name
        }
        self._prompt_guidelines = [
            (extension, guideline)
            for extension, guideline in self._prompt_guidelines
            if extension != extension_name
        ]
        self._message_renderers = {
            custom_type: registration
            for custom_type, registration in self._message_renderers.items()
            if registration[0] != extension_name
        }

    # -- registration (called through ExtensionAPI) -------------------------

    def register_tool(self, extension_name: str, tool: AgentTool) -> None:
        """Register an extension tool; first registration per name wins."""
        existing = self._tools.get(tool.name)
        if existing is not None:
            self._load_diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    name=extension_name,
                    message=(
                        f"tool `{tool.name}` already registered by extension"
                        f" `{existing.extension}`; ignoring duplicate"
                    ),
                )
            )
            return
        self._tools[tool.name] = RegisteredExtensionTool(extension=extension_name, tool=tool)

    def register_command(
        self,
        extension_name: str,
        name: str,
        handler: ExtensionCommandHandler,
        *,
        description: str = "",
        usage: str | None = None,
        aliases: tuple[str, ...] = (),
    ) -> None:
        """Register an extension slash command; first registration wins."""
        normalized = name.strip().removeprefix("/").lower()
        existing = self._commands.get(normalized)
        if existing is not None:
            self._load_diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    name=extension_name,
                    message=(
                        f"command `/{normalized}` already registered by extension"
                        f" `{existing.extension}`; ignoring duplicate"
                    ),
                )
            )
            return
        self._commands[normalized] = ExtensionCommand(
            extension=extension_name,
            name=normalized,
            description=description,
            usage=usage or f"/{normalized}",
            aliases=aliases,
            handler=handler,
        )

    def register_message_renderer(
        self,
        extension_name: str,
        custom_type: str,
        renderer: MessageRenderer,
    ) -> None:
        """Register a custom-message renderer; first registration per type wins."""
        normalized = custom_type.strip()
        if not normalized:
            self._load_diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    name=extension_name,
                    message="empty custom_type for message renderer ignored",
                )
            )
            return
        existing = self._message_renderers.get(normalized)
        if existing is not None:
            self._load_diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    name=extension_name,
                    message=(
                        f"message renderer for `{normalized}` already registered by"
                        f" extension `{existing[0]}`; ignoring duplicate"
                    ),
                )
            )
            return
        self._message_renderers[normalized] = (extension_name, renderer)

    def render_custom_message(
        self,
        custom_type: str,
        content: str,
        details: Mapping[str, JSONValue] | None,
        expanded: bool,
    ) -> str | None:
        """Render a custom message to markup, or ``None`` to fall back to raw text.

        Installed into every render path (TUI state, print transcript). A missing
        renderer or a renderer that raises or returns a non-string yields
        ``None`` so the frontend renders the raw ``content`` instead of crashing.
        Failures are diagnosed once per ``custom_type`` (render paths re-run on
        every redraw, which would otherwise grow diagnostics without bound).
        """
        registration = self._message_renderers.get(custom_type)
        if registration is None:
            return None
        extension_name, renderer = registration
        view = CustomMessageView(custom_type=custom_type, content=content, details=details)
        options = MessageRenderOptions(expanded=expanded)
        try:
            markup = renderer(view, options)
        except Exception as exc:  # noqa: BLE001 - a renderer must never crash the frontend
            if custom_type not in self._renderer_failures_reported:
                self._renderer_failures_reported.add(custom_type)
                self._record_runtime_failure(extension_name, f"message_renderer:{custom_type}", exc)
            return None
        if not isinstance(markup, str):
            if custom_type not in self._renderer_failures_reported:
                self._renderer_failures_reported.add(custom_type)
                self._record_bad_result(extension_name, f"message_renderer:{custom_type}", markup)
            return None
        return markup

    def render_tool_call(
        self,
        name: str,
        arguments: Mapping[str, JSONValue],
    ) -> str | None:
        """Render a tool call via its tool's `render_call`, or ``None``.

        Installed into frontends as the tool-call display resolver. A tool
        without a `render_call`, or a renderer that raises or returns a
        non-string, yields ``None`` so the frontend falls back to its
        generic invocation formatting. Failures are diagnosed once per tool
        name (render paths re-run on every redraw).
        """
        registered = self._tools.get(name)
        if registered is None or registered.tool.render_call is None:
            return None
        try:
            line = registered.tool.render_call(arguments)
        except Exception as exc:  # noqa: BLE001 - a renderer must never crash the frontend
            if name not in self._renderer_failures_reported:
                self._renderer_failures_reported.add(name)
                self._record_runtime_failure(registered.extension, f"render_call:{name}", exc)
            return None
        if line is not None and not isinstance(line, str):
            if name not in self._renderer_failures_reported:
                self._renderer_failures_reported.add(name)
                self._record_bad_result(registered.extension, f"render_call:{name}", line)
            return None
        return line

    def render_tool_result(
        self,
        result: AgentToolResult,
        expanded: bool,
    ) -> str | None:
        """Render a tool result via its tool's `render_result`, or ``None``.

        Installed into frontends as the tool-result display resolver, the
        counterpart of `render_tool_call` for the other end of the row's
        lifecycle. A tool without a `render_result`, or a renderer that raises
        or returns a non-string, yields ``None`` so the frontend falls back to
        its generic result formatting. Failures are diagnosed once per tool
        name (render paths re-run on every redraw).
        """
        registered = self._tools.get(result.name)
        if registered is None or registered.tool.render_result is None:
            return None
        failure_key = f"render_result:{result.name}"
        try:
            markup = registered.tool.render_result(result, expanded=expanded)
        except Exception as exc:  # noqa: BLE001 - a renderer must never crash the frontend
            if failure_key not in self._renderer_failures_reported:
                self._renderer_failures_reported.add(failure_key)
                self._record_runtime_failure(
                    registered.extension, f"render_result:{result.name}", exc
                )
            return None
        if markup is not None and not isinstance(markup, str):
            if failure_key not in self._renderer_failures_reported:
                self._renderer_failures_reported.add(failure_key)
                self._record_bad_result(
                    registered.extension, f"render_result:{result.name}", markup
                )
            return None
        return markup

    def register_prompt_guideline(self, extension_name: str, guideline: str) -> None:
        """Register a standalone system-prompt guideline line."""
        normalized = guideline.strip()
        if not normalized:
            self._load_diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    name=extension_name,
                    message="empty prompt guideline ignored",
                )
            )
            return
        self._prompt_guidelines.append((extension_name, normalized))

    def subscribe(self, extension_name: str, event: str, handler: ExtensionHandler) -> None:
        """Subscribe an extension handler to a named event."""
        known = (
            event in AGENT_EVENT_TYPES
            or event in LIFECYCLE_EVENT_TYPES
            or event == AGENT_EVENT_WILDCARD
        )
        if not known:
            self._load_diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    name=extension_name,
                    message=f"unknown event `{event}`; handler ignored",
                )
            )
            return
        extension = self._extension_by_name(extension_name)
        if extension is None:
            raise ExtensionError(f"unknown extension: {extension_name}")
        extension.handlers.setdefault(event, []).append(handler)

    # -- binding -------------------------------------------------------------

    def bind(self, session: BoundSession) -> None:
        """Bind (or re-bind) the runtime to a coding session."""
        self._session = session

    def attach_harness_listener(
        self,
        subscribe: Callable[[Callable[[AgentEvent], Awaitable[None] | None]], Callable[[], None]],
    ) -> None:
        """Subscribe the event fan-out to a harness, replacing any prior one."""
        if self._harness_unsubscribe is not None:
            self._harness_unsubscribe()
        self._harness_unsubscribe = subscribe(self._on_agent_event)

    def set_ui_bridge(self, ui: UiBridge) -> None:
        """Install the frontend UI bridge (TUI, print-mode fallback, or test)."""
        self._ui = ui

    def clear_ui_components(self) -> None:
        """Ask the host frontend to tear down all extension-owned UI.

        Invoked on `/reload` (via ``reset_for_reload``) and by session
        replacement flows (resume/new) before ``session_start`` fires, so
        widgets and key interceptors never outlive the world that mounted
        them while handlers keep the chance to re-mount.
        """
        self._ui.clear_components()

    def set_turn_requested_callback(self, callback: TurnRequestedCallback | None) -> None:
        """Install the host callback used to deliver messages while idle.

        The callback receives the message content plus optional custom-message
        metadata and is expected to submit it through the host's serialized run
        path (the TUI uses the same exclusive worker as user submissions, so
        extension turns cannot race user runs).
        """
        self._turn_requested = callback

    @property
    def ui(self) -> UiBridge:
        """Return the active UI bridge."""
        return self._ui

    @property
    def session_view(self) -> BoundSession:
        """Return the bound session, raising if the runtime is unbound."""
        if self._session is None:
            raise ExtensionError(
                "extension API used before the session was bound; "
                "register handlers in setup() and act on events instead"
            )
        return self._session

    @property
    def extension_names(self) -> tuple[str, ...]:
        """Return loaded extension names in load order."""
        return tuple(extension.name for extension in self._extensions)

    @property
    def diagnostics(self) -> tuple[ResourceDiagnostic, ...]:
        """Return load-time and runtime diagnostics."""
        return tuple(self._load_diagnostics) + tuple(self._runtime_diagnostics)

    @property
    def extension_tools(self) -> tuple[AgentTool, ...]:
        """Return extension-registered tools in registration order."""
        return tuple(registration.tool for registration in self._tools.values())

    @property
    def prompt_guidelines(self) -> tuple[str, ...]:
        """Return standalone guideline lines in registration order."""
        return tuple(guideline for _, guideline in self._prompt_guidelines)

    # -- actions (called through ExtensionAPI) --------------------------------

    def send_user_message(self, content: str, *, deliver_as: str = "follow_up") -> None:
        """Deliver a user message into the active run, or start one when idle."""
        self._deliver_message(content, deliver_as=deliver_as, trigger_turn=True)

    def send_custom_message(
        self,
        content: str,
        *,
        custom_type: str,
        details: dict[str, JSONValue] | None = None,
        deliver_as: str = "follow_up",
        trigger_turn: bool = True,
    ) -> None:
        """Deliver a custom message carrying render metadata through the pipeline."""
        self._deliver_message(
            content,
            deliver_as=deliver_as,
            trigger_turn=trigger_turn,
            custom_type=custom_type,
            details=details,
        )

    def _deliver_message(
        self,
        content: str,
        *,
        deliver_as: str,
        trigger_turn: bool,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        session = self.session_view
        if session.is_running:
            if deliver_as == "steer":
                session.queue_steering_message(content, custom_type=custom_type, details=details)
            else:
                session.queue_follow_up_message(content, custom_type=custom_type, details=details)
            return
        if trigger_turn and self._turn_requested is not None:
            self._turn_requested(content, custom_type, details)
            return
        # No host run-path registered (print mode, tests) or trigger_turn=False:
        # queue for whichever run happens next.
        session.queue_follow_up_message(content, custom_type=custom_type, details=details)

    async def append_custom_entry(self, namespace: str, data: dict[str, JSONValue]) -> None:
        """Persist a `CustomEntry` through the bound session."""
        await self.session_view.append_custom_entry(namespace, data)

    # -- tools ----------------------------------------------------------------

    def compose_tools(self, builtin_tools: Sequence[AgentTool]) -> list[AgentTool]:
        """Merge built-in and extension tools, then wrap all with hook seams.

        Extension tools override built-ins with the same name in place;
        extension-only tools append in registration order.
        """
        merged: list[AgentTool] = []
        extension_tools = dict(self._tools)
        for tool in builtin_tools:
            override = extension_tools.pop(tool.name, None)
            merged.append(override.tool if override is not None else tool)
        merged.extend(registration.tool for registration in extension_tools.values())
        return [self._wrap_tool(tool) for tool in merged]

    def _wrap_tool(self, tool: AgentTool) -> AgentTool:
        async def executor(
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            *,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            call_outcome = await self._run_tool_call_hooks(tool.name, arguments)
            if call_outcome.block:
                reason = call_outcome.reason or "blocked by an extension"
                message = f"Tool call blocked: {reason}"
                return AgentToolResult(
                    tool_call_id="",
                    name=tool.name,
                    ok=False,
                    content=message,
                    error=message,
                )
            effective_arguments = (
                call_outcome.arguments if call_outcome.arguments is not None else arguments
            )
            # The wrapper always declares `on_update`; the inner tool's own
            # inspect-gate drops it for executors that do not accept it.
            result = await tool.execute(effective_arguments, signal=signal, on_update=on_update)
            return await self._run_tool_result_hooks(tool.name, effective_arguments, result)

        return AgentTool(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            executor=executor,
            prompt_snippet=tool.prompt_snippet,
            prompt_guidelines=tool.prompt_guidelines,
        )

    async def _run_tool_call_hooks(
        self,
        tool_name: str,
        arguments: Mapping[str, JSONValue],
    ) -> ToolCallHookResult:
        effective: Mapping[str, JSONValue] = arguments
        for extension, handler in self._handlers_for("tool_call"):
            event = ToolCallHookEvent(tool_name=tool_name, arguments=effective)
            try:
                result = await _resolve(handler(event))
            except Exception as exc:  # noqa: BLE001 - fail-safe: an error blocks the tool
                self._record_runtime_failure(extension, "tool_call", exc)
                return ToolCallHookResult(
                    block=True,
                    reason=f"extension `{extension}` tool_call hook failed: {exc}",
                )
            if result is None:
                continue
            if not isinstance(result, ToolCallHookResult):
                self._record_bad_result(extension, "tool_call", result)
                continue
            if result.block:
                return ToolCallHookResult(block=True, reason=result.reason)
            if result.arguments is not None:
                effective = result.arguments
        if effective is arguments:
            return ToolCallHookResult()
        return ToolCallHookResult(arguments=effective)

    async def _run_tool_result_hooks(
        self,
        tool_name: str,
        arguments: Mapping[str, JSONValue],
        result: AgentToolResult,
    ) -> AgentToolResult:
        current = result
        for extension, handler in self._handlers_for("tool_result"):
            event = ToolResultHookEvent(tool_name=tool_name, arguments=arguments, result=current)
            try:
                outcome = await _resolve(handler(event))
            except Exception as exc:  # noqa: BLE001 - result hooks are observational-ish
                self._record_runtime_failure(extension, "tool_result", exc)
                continue
            if outcome is None:
                continue
            if not isinstance(outcome, ToolResultHookResult):
                self._record_bad_result(extension, "tool_result", outcome)
                continue
            updates: dict[str, object] = {}
            if outcome.content is not None:
                updates["content"] = outcome.content
            if outcome.ok is not None:
                updates["ok"] = outcome.ok
            if outcome.details is not None:
                updates["details"] = outcome.details
            if updates:
                current = current.model_copy(update=updates)
        return current

    # -- commands ---------------------------------------------------------------

    def build_command_registry(self) -> CommandRegistry:
        """Build a session command registry: defaults plus extension commands."""
        registry = create_default_command_registry()
        for command in self._commands.values():
            slash_command = SlashCommand(
                name=command.name,
                description=command.description or f"Extension command ({command.extension}).",
                usage=command.usage,
                handler=self._command_handler(command),
                aliases=command.aliases,
                search_terms=(command.extension, "extension"),
            )
            try:
                registry.register(slash_command)
            except ValueError as exc:
                self._load_diagnostics.append(
                    ResourceDiagnostic(
                        kind="extension",
                        name=command.extension,
                        message=f"could not register command `/{command.name}`: {exc}",
                    )
                )
        return registry

    def _command_handler(
        self, command: ExtensionCommand
    ) -> Callable[[CommandContext], CommandResult]:
        def handler(context: CommandContext) -> CommandResult:
            extension_context = ExtensionCommandContext(
                name=command.name,
                args=context.args,
                api=self._api_for(command.extension),
            )
            try:
                message = command.handler(context.args, extension_context)
            except Exception as exc:  # noqa: BLE001 - extensions are an isolation boundary
                self._record_runtime_failure(command.extension, f"command:/{command.name}", exc)
                return CommandResult(
                    handled=True,
                    message=f"Extension command /{command.name} failed: {exc}",
                )
            return CommandResult(handled=True, message=message)

        return handler

    # -- event dispatch -----------------------------------------------------------

    async def emit_session_start(self, reason: SessionLifecycleReason) -> None:
        """Dispatch `session_start` to subscribed extensions."""
        await self._emit_lifecycle("session_start", SessionStartEvent(reason=reason))

    async def emit_session_shutdown(self, reason: SessionLifecycleReason) -> None:
        """Dispatch `session_shutdown` to subscribed extensions."""
        await self._emit_lifecycle("session_shutdown", SessionShutdownEvent(reason=reason))

    async def run_input_hooks(
        self,
        text: str,
        *,
        source: Literal["interactive", "extension"] = "interactive",
        streaming_behavior: Literal["steer", "follow_up"] | None = None,
    ) -> InputHookOutcome:
        """Run `input` hooks over prompt text; transforms chain, handled wins.

        `source`/`streaming_behavior` are surfaced to handlers on the
        `InputEvent` payload; they do not change chaining semantics.
        """
        current = text
        for extension, handler in self._handlers_for("input"):
            try:
                result = await _resolve(
                    handler(
                        InputEvent(
                            text=current,
                            source=source,
                            streaming_behavior=streaming_behavior,
                        )
                    )
                )
            except Exception as exc:  # noqa: BLE001 - extensions are an isolation boundary
                self._record_runtime_failure(extension, "input", exc)
                continue
            if result is None:
                continue
            if not isinstance(result, InputHookResult):
                self._record_bad_result(extension, "input", result)
                continue
            if result.action == "handled":
                return InputHookOutcome(handled=True, text=current, message=result.message)
            if result.action == "transform" and result.text is not None:
                current = result.text
        return InputHookOutcome(handled=False, text=current)

    async def _on_agent_event(self, event: AgentEvent) -> None:
        handlers = list(self._handlers_for(event.type))
        handlers.extend(self._handlers_for(AGENT_EVENT_WILDCARD))
        for extension, handler in handlers:
            try:
                await _resolve(handler(event))
            except Exception as exc:  # noqa: BLE001 - extensions are an isolation boundary
                self._record_runtime_failure(extension, event.type, exc)

    async def _emit_lifecycle(self, event_name: str, payload: object) -> None:
        for extension, handler in self._handlers_for(event_name):
            try:
                await _resolve(handler(payload))
            except Exception as exc:  # noqa: BLE001 - extensions are an isolation boundary
                self._record_runtime_failure(extension, event_name, exc)

    # -- internals -------------------------------------------------------------

    def _handlers_for(self, event: str) -> Iterator[tuple[str, ExtensionHandler]]:
        for extension in self._extensions:
            for handler in extension.handlers.get(event, ()):
                yield extension.name, handler

    def _extension_by_name(self, name: str) -> RegisteredExtension | None:
        for extension in self._extensions:
            if extension.name == name:
                return extension
        return None

    def _api_for(self, extension_name: str) -> ExtensionAPI:
        extension = self._extension_by_name(extension_name)
        if extension is None:
            raise ExtensionError(f"unknown extension: {extension_name}")
        return extension.api

    def _record_runtime_failure(self, extension: str, event: str, exc: Exception) -> None:
        self._runtime_diagnostics.append(
            ResourceDiagnostic(
                kind="extension",
                name=extension,
                message=f"handler for `{event}` raised: {exc!r}",
                severity="error",
            )
        )

    def _record_bad_result(self, extension: str, event: str, result: object) -> None:
        self._runtime_diagnostics.append(
            ResourceDiagnostic(
                kind="extension",
                name=extension,
                message=(
                    f"handler for `{event}` returned unsupported"
                    f" result type {type(result).__name__}; ignored"
                ),
            )
        )


async def _resolve(value: object) -> object:
    if isawaitable(value):
        return await value
    return value
