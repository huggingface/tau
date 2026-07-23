"""Persistent coding-session wrapper built on AgentHarness."""

from __future__ import annotations

import string
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from tau_agent.events import AgentEndEvent, MessageEndEvent, ToolExecutionEndEvent
from tau_agent.harness import AgentHarness, AgentHarnessConfig, QueuedMessages
from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    CustomMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
    message_text,
)
from tau_agent.provider import ModelProvider
from tau_agent.provider_events import AssistantDoneEvent, AssistantErrorEvent, TextDeltaEvent
from tau_agent.session import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    JsonlSessionStorage,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionInfoEntry,
    SessionState,
    SessionStorage,
    ThinkingLevelChangeEntry,
)
from tau_agent.session.entries import SessionEntry
from tau_agent.session.jsonl import entry_to_json_line
from tau_agent.session.tree import SessionTreeError, path_to_entry
from tau_agent.tools import AgentTool
from tau_agent.types import JSONValue
from tau_ai.model_limits import ModelLimitsProvider, RuntimeModelLimits
from tau_coding.branch_summary import summarize_branch_messages_with_model
from tau_coding.commands import CommandRegistry, CommandResult, create_default_command_registry
from tau_coding.context import discover_project_context_with_diagnostics
from tau_coding.context_window import (
    DEFAULT_COMPACTION_KEEP_RECENT_TOKENS,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    SUMMARIZATION_SYSTEM_PROMPT,
    ContextUsageEstimate,
    auto_compaction_threshold_for_context_window,
    build_compaction_summary_prompt,
    estimate_context_usage,
    estimate_message_tokens,
    summarize_messages_for_compaction,
)
from tau_coding.credentials import FileCredentialStore, credentials_path
from tau_coding.diagnostics import (
    AgentCallDiagnosticContext,
    AgentCallDiagnosticLogger,
    new_agent_call_run_id,
)
from tau_coding.events import (
    AgentSettledEvent,
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    CodingSessionEvent,
    CompactionEndEvent,
    CompactionStartEvent,
    QueueUpdateEvent,
    SessionAgentEndEvent,
)
from tau_coding.extensions.runtime import ExtensionRuntime
from tau_coding.paths import TauPaths
from tau_coding.prompt_templates import (
    PromptTemplate,
    expand_prompt_template_command,
    load_prompt_templates_with_diagnostics,
)
from tau_coding.provider_config import (
    ProviderConfig,
    ProviderConfigError,
    ProviderSettings,
    load_provider_settings,
    provider_default_thinking_level,
    provider_has_usable_credentials,
    provider_thinking_levels,
    provider_thinking_unavailable_reason,
    resolve_provider_selection,
    save_default_provider_model,
    save_provider_thinking_level,
    toggle_saved_scoped_model,
    validate_provider_model,
)
from tau_coding.provider_runtime import ClosableModelProvider, create_model_provider
from tau_coding.reload import CodingReloadSummary, ReloadCategorySummary
from tau_coding.resources import (
    ResourceDiagnostic,
    ResourceError,
    TauResourcePaths,
    resource_paths_with_cwd,
)
from tau_coding.session_export import (
    default_session_export_artifact_path,
    export_session_artifact,
    normalize_export_format,
)
from tau_coding.session_manager import SessionManager
from tau_coding.session_stats import SessionStats, calculate_session_stats
from tau_coding.skills import Skill, expand_skill_command, load_skills_with_diagnostics
from tau_coding.system_prompt import (
    BuildSystemPromptOptions,
    ProjectContextFile,
    build_system_prompt,
)
from tau_coding.thinking import (
    DEFAULT_THINKING_LEVEL,
    THINKING_LEVELS,
    ThinkingLevel,
    next_thinking_level,
    normalize_thinking_level,
)
from tau_coding.tools import create_bash_tool, create_coding_tools

StreamingBehavior = Literal["steer", "follow_up"]
SESSION_NAME_SYSTEM_PROMPT = (
    "You write concise coding-agent session names. Reply with only a short title, "
    "maximum four words, no quotes, no punctuation-only output."
)
TREE_RUNNING_MESSAGE = "Tau is still working. Press Escape to interrupt before using /tree."


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """A selectable model and the provider that serves it."""

    provider_name: str
    model: str


@dataclass(frozen=True, slots=True)
class TerminalCommandResult:
    """Result of an input-bar terminal command."""

    command: str
    output: str
    exit_code: int | None
    ok: bool
    added_to_context: bool


@dataclass(frozen=True, slots=True)
class SessionTreeChoice:
    """One branchable entry in the active session tree."""

    entry_id: str
    label: str
    active: bool = False
    is_tool_call: bool = False


@dataclass(frozen=True, slots=True)
class SessionTreeBranchResult:
    """Result of moving the active session tree leaf."""

    message: str
    input_prefill: str | None = None


@dataclass(frozen=True, slots=True)
class TerminalCommandRequest:
    """Parsed input-bar terminal command request."""

    command: str
    add_to_context: bool


@dataclass(frozen=True, slots=True)
class SessionResources:
    """Tau-owned resources loaded around a coding session."""

    skills: tuple[Skill, ...]
    prompt_templates: tuple[PromptTemplate, ...]
    context_files: tuple[ProjectContextFile, ...]
    diagnostics: tuple[ResourceDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    """Prepared active-context entries for a compaction run."""

    replace_entry_ids: tuple[str, ...]
    messages_to_summarize: tuple[AgentMessage, ...]


@dataclass(frozen=True, slots=True)
class CodingSessionConfig:
    """Configuration for a persistent coding session."""

    provider: ModelProvider
    model: str
    storage: SessionStorage
    cwd: Path
    system: str | None = None
    custom_system_prompt: str | None = None
    append_system_prompt: str | None = None
    context_files: tuple[ProjectContextFile, ...] = ()
    tools: list[AgentTool] | None = None
    resource_paths: TauResourcePaths | None = None
    session_id: str | None = None
    session_manager: SessionManager | None = None
    command_registry: CommandRegistry | None = None
    provider_name: str = "openai"
    provider_settings: ProviderSettings | None = None
    runtime_provider_config: ProviderConfig | None = None
    auto_compact_token_threshold: int | None = None
    auto_compact_enabled: bool = True
    thinking_level: ThinkingLevel = DEFAULT_THINKING_LEVEL
    index_on_first_persist: bool = False
    shell_command_prefix: str | None = None
    skills_enabled: bool = True
    """Whether skill discovery is enabled for this session.

    When ``True`` (the default), skills are discovered from the resource paths
    and their index is injected into the system prompt as ``<available_skills>``,
    and ``/skill:`` commands expand against them. When ``False``, skill discovery
    is suppressed for the whole session: no skills load, the ``<available_skills>``
    index is omitted, and ``/skill:`` commands find nothing to expand. This mirrors
    Pi's loader-level ``noSkills`` flag, which suppresses only skill discovery and
    leaves prompt templates and project context files (AGENTS.md) unaffected. It is
    the seam hosts use to construct skill-less sessions (e.g. a subagent type that
    gets no skills).
    """
    extension_paths: tuple[Path, ...] = ()
    extensions_enabled: bool = True
    project_extensions_enabled: bool = False
    extension_runtime: ExtensionRuntime | None = None


class CodingSession:
    """Tau's coding-agent environment wrapper.

    `AgentHarness` owns the in-memory agent brain. `CodingSession` owns the
    coding-session environment around it: durable session entries, default coding
    tools, and a small command seam for later phases.
    """

    def __init__(
        self,
        config: CodingSessionConfig,
        *,
        state: SessionState,
        harness: AgentHarness,
        last_parent_id: str | None,
        skills: tuple[Skill, ...] = (),
        prompt_templates: tuple[PromptTemplate, ...] = (),
        context_files: tuple[ProjectContextFile, ...] = (),
        resource_diagnostics: tuple[ResourceDiagnostic, ...] = (),
        command_registry: CommandRegistry | None = None,
        pending_initial_entries: tuple[SessionEntry, ...] = (),
        extension_runtime: ExtensionRuntime | None = None,
    ) -> None:
        self._config = config
        self._state = state
        self._harness = harness
        self._extension_runtime = extension_runtime or ExtensionRuntime()
        self._session_start_pending = False
        self._last_parent_id = last_parent_id
        self._pending_initial_entries = pending_initial_entries
        self._skills = skills
        self._prompt_templates = prompt_templates
        self._context_files = context_files
        self._resource_diagnostics = resource_diagnostics
        self._command_registry = command_registry or create_default_command_registry()
        self._provider_name = config.provider_name
        self._provider_settings = config.provider_settings
        self._runtime_provider_config = config.runtime_provider_config
        self._resource_paths = resource_paths_with_cwd(config.resource_paths, config.cwd)
        self._auto_compact_token_threshold = config.auto_compact_token_threshold
        self._auto_compact_enabled = config.auto_compact_enabled
        self._thinking_level = _state_thinking_level(
            state,
            default=_default_thinking_level_for_active_model(self),
        )
        self._context_usage_cache: ContextUsageEstimate | None = None
        self._owned_providers: list[ClosableModelProvider] = []
        self._diagnostic_logger = AgentCallDiagnosticLogger.from_paths(self._resource_paths.paths)
        self._credential_store = FileCredentialStore(
            credentials_path(self._resource_paths.paths) if self._resource_paths.paths else None
        )
        self._last_diagnostic_log_path: Path | None = None
        self._runtime_model_limits: RuntimeModelLimits | None = None
        self._runtime_model_limits_key: tuple[str, str] | None = None
        self._model_limits_discovery_error: str | None = None

    @classmethod
    async def load(cls, config: CodingSessionConfig) -> CodingSession:
        """Load a coding session from append-only storage."""
        entries = await config.storage.read_all()
        pending_initial_entries: tuple[SessionEntry, ...] = ()
        if not entries:
            info = SessionInfoEntry(cwd=str(config.cwd))
            initial_model = _initial_model_for_config(config)
            model = ModelChangeEntry(
                parent_id=info.id,
                model=initial_model,
            )
            thinking = ThinkingLevelChangeEntry(
                parent_id=model.id,
                thinking_level=_initial_thinking_level_for_config(config, model=initial_model),
            )
            entries = [info, model, thinking]
            pending_initial_entries = (info, model, thinking)
        else:
            entries = _detach_missing_parents(entries)

        linear_state = SessionState.from_entries(entries)
        latest_leaf = _latest_leaf_entry(entries)
        state = (
            SessionState.from_entries(entries, leaf_id=latest_leaf.entry_id)
            if latest_leaf is not None
            else linear_state
        )
        resource_paths = resource_paths_with_cwd(config.resource_paths, config.cwd)
        resources = _load_session_resources(
            resource_paths,
            config.context_files,
            skills_enabled=config.skills_enabled,
        )

        extension_runtime = config.extension_runtime
        fresh_extension_runtime = extension_runtime is None
        if extension_runtime is None:
            extension_runtime = ExtensionRuntime()
            if config.extensions_enabled or config.extension_paths:
                extension_runtime.load(
                    resource_paths,
                    extra_paths=config.extension_paths,
                    include_resource_dirs=config.extensions_enabled,
                    include_project_dir=config.project_extensions_enabled,
                )

        base_tools = (
            config.tools
            if config.tools is not None
            else create_coding_tools(
                cwd=config.cwd,
                shell_command_prefix=config.shell_command_prefix,
            )
        )
        tools = extension_runtime.compose_tools(base_tools)
        system = (
            config.system
            if config.system is not None
            else build_system_prompt(
                BuildSystemPromptOptions(
                    cwd=config.cwd,
                    tools=tools,
                    skills=resources.skills,
                    custom_prompt=config.custom_system_prompt,
                    append_system_prompt=config.append_system_prompt,
                    context_files=resources.context_files,
                    extra_guidelines=extension_runtime.prompt_guidelines,
                )
            )
        )
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=config.provider,
                model=_runtime_model_for_state(config, state),
                system=system,
                tools=tools,
            ),
            messages=state.messages,
        )
        session = cls(
            config,
            state=state,
            harness=harness,
            last_parent_id=_last_parent_id_from_state(state),
            skills=resources.skills,
            prompt_templates=resources.prompt_templates,
            context_files=resources.context_files,
            resource_diagnostics=resources.diagnostics,
            command_registry=config.command_registry or extension_runtime.build_command_registry(),
            pending_initial_entries=pending_initial_entries,
            extension_runtime=extension_runtime,
        )
        await session._persist_loaded_interrupted_tool_repairs()
        session._sync_thinking_level_to_active_model()
        session._refresh_runtime_provider()
        await session._refresh_runtime_model_limits()
        if fresh_extension_runtime:
            extension_runtime.bind(session)
            # Attach to session._harness, not the local `harness`:
            # _persist_loaded_interrupted_tool_repairs() above may have
            # replaced the harness, and listeners on the discarded one would
            # never see an agent event.
            extension_runtime.attach_harness_listener(session._harness.subscribe)
            # session_start is deferred: hosts emit it via
            # emit_pending_session_start() after installing their UI bridge,
            # so handlers can use notifications and dialogs (Pi starts the UI
            # before initializing extensions for the same reason).
            session._session_start_pending = True
        return session

    @property
    def cwd(self) -> Path:
        """Return the session working directory."""
        return self._config.cwd

    @property
    def model(self) -> str:
        """Return the active model for this session."""
        return self._harness.config.model

    @property
    def provider_name(self) -> str:
        """Return the active provider name."""
        return self._provider_name

    @property
    def available_providers(self) -> tuple[str, ...]:
        """Return provider names Tau can call with available credentials."""
        if self._provider_settings is None:
            return (self._provider_name,)
        return tuple(provider.name for provider in self._usable_provider_configs())

    @property
    def available_models(self) -> tuple[str, ...]:
        """Return model names for the active provider when it is usable."""
        if self._provider_settings is None:
            return (self.model,)
        try:
            provider = self._provider_settings.get_provider(self._provider_name)
        except ProviderConfigError:
            return (self.model,)
        if not self._provider_is_usable(provider):
            return ()
        return provider.models

    @property
    def available_model_choices(self) -> tuple[ModelChoice, ...]:
        """Return provider/model choices Tau can call with available credentials."""
        if self._provider_settings is None:
            return (ModelChoice(provider_name=self._provider_name, model=self.model),)
        return tuple(
            ModelChoice(provider_name=provider.name, model=model)
            for provider in self._usable_provider_configs()
            for model in provider.models
        )

    @property
    def scoped_model_choices(self) -> tuple[ModelChoice, ...]:
        """Return configured quick-switch model choices that are currently usable."""
        if self._provider_settings is None:
            return ()
        available = set(self.available_model_choices)
        return tuple(
            choice
            for choice in (
                ModelChoice(provider_name=item.provider, model=item.model)
                for item in self._provider_settings.scoped_models
            )
            if choice in available
        )

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        """Return the tools available to the agent."""
        return tuple(self._harness.config.tools)

    @property
    def extension_tool_sources(self) -> dict[str, str]:
        """Map active extension-provided tools to their owning extension."""
        return self._extension_runtime.extension_tool_sources

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        """Return the restored/current transcript."""
        return self._harness.messages

    @property
    def state(self) -> SessionState:
        """Return the last replayed durable session state."""
        return self._state

    async def tree_choices(self) -> tuple[SessionTreeChoice, ...]:
        """Return branchable session entries for a tree picker."""
        entries = await self._read_session_entries()
        branch_indents = _tree_branch_indents(entries)
        return tuple(
            SessionTreeChoice(
                entry_id=entry.id,
                label=_tree_choice_label(entry, branch_indent=branch_indents.get(entry.id, 0)),
                active=entry.id == self._state.active_leaf_id,
                is_tool_call=_is_tool_call_tree_entry(entry),
            )
            for entry in _ordered_tree_entries(entries)
            if _is_branchable_tree_entry(entry)
        )

    async def branch_to_entry(
        self,
        entry_id: str,
        *,
        summarize: bool = False,
        custom_instructions: str | None = None,
        replace_instructions: bool = False,
    ) -> SessionTreeBranchResult:
        """Move the active leaf to a previous entry, preserving existing history."""
        if self._harness.is_running:
            raise RuntimeError(TREE_RUNNING_MESSAGE)
        entries = await self._read_session_entries()
        by_id = {entry.id: entry for entry in entries}
        if entry_id not in by_id:
            raise ValueError(f"Unknown session entry: {entry_id}")
        selected_entry = by_id[entry_id]
        if not _is_branchable_tree_entry(selected_entry):
            raise ValueError(f"Session entry cannot be branched from: {entry_id}")

        target_id: str | None = entry_id
        input_prefill: str | None = None
        summary_entry: BranchSummaryEntry | None = None
        if summarize:
            abandoned_messages = _messages_after_entry_on_active_path(
                entries,
                entry_id,
                self._last_parent_id,
            )
            if abandoned_messages:
                summary = await self._summarize_branch_messages(
                    abandoned_messages,
                    custom_instructions=custom_instructions,
                    replace_instructions=replace_instructions,
                )
                summary_entry = BranchSummaryEntry(
                    parent_id=entry_id,
                    branch_root_id=entry_id,
                    summary=summary,
                )
                await self._append_session_entry(summary_entry)
                target_id = summary_entry.id
        elif selected_entry.type == "message" and isinstance(selected_entry.message, UserMessage):
            target_id = selected_entry.parent_id
            input_prefill = selected_entry.message.text

        leaf = LeafEntry(parent_id=target_id, entry_id=target_id)
        await self._append_session_entry(leaf)
        self._last_parent_id = target_id

        await self._refresh_persisted_state(leaf_id=target_id)
        self._harness.replace_messages(self._state.messages)
        self._invalidate_context_usage_cache()
        self._thinking_level = _state_thinking_level(
            self._state,
            default=_default_thinking_level_for_active_model(self),
        )
        self._sync_thinking_level_to_active_model()
        self._refresh_runtime_provider()
        suffix = " with branch summary" if summary_entry is not None else ""
        if input_prefill is not None:
            return SessionTreeBranchResult(
                message=f"Branched session before {entry_id}.",
                input_prefill=input_prefill,
            )
        return SessionTreeBranchResult(message=f"Branched session at {target_id}{suffix}.")

    @property
    def thinking_level(self) -> ThinkingLevel:
        """Return the active thinking mode for future turns."""
        return self._thinking_level

    @property
    def available_thinking_levels(self) -> tuple[ThinkingLevel, ...]:
        """Return thinking modes supported by the active provider/model."""
        if self._provider_settings is None:
            return THINKING_LEVELS
        provider = self._active_provider_config()
        if provider is None:
            return ()
        return provider_thinking_levels(provider, model=self.model)

    @property
    def thinking_unavailable_reason(self) -> str | None:
        """Return why thinking controls are unavailable for the active model."""
        if self.available_thinking_levels:
            return None
        provider = self._active_provider_config()
        if provider is None:
            return "Active provider settings are not available"
        return provider_thinking_unavailable_reason(provider, model=self.model)

    @property
    def storage(self) -> SessionStorage:
        """Return the backing session storage."""
        return self._config.storage

    async def export(
        self,
        destination: Path | None = None,
        *,
        format: str | None = None,
    ) -> Path:
        """Export the current session to a user-facing artifact."""
        entries = await self._read_session_entries()
        session_path = _storage_path(self._config.storage)
        export_format = normalize_export_format(
            format or (destination.suffix.removeprefix(".") if destination else "html")
        )
        output_path = _resolve_export_destination(
            destination,
            cwd=self.cwd,
            session_path=session_path,
            format=export_format,
        )
        return export_session_artifact(
            entries,
            output_path,
            title=_session_export_title(self),
            source=str(session_path) if session_path is not None else self.session_id,
            format=export_format,
        )

    @property
    def skills(self) -> tuple[Skill, ...]:
        """Return loaded skills."""
        return self._skills

    @property
    def prompt_templates(self) -> tuple[PromptTemplate, ...]:
        """Return loaded prompt templates."""
        return self._prompt_templates

    @property
    def context_files(self) -> tuple[ProjectContextFile, ...]:
        """Return active project context files."""
        return self._context_files

    @property
    def context_token_estimate(self) -> int:
        """Return a rough token estimate for the active provider context."""
        return self.context_usage.total_tokens

    @property
    def context_usage(self) -> ContextUsageEstimate:
        """Return structured context accounting for the active provider context."""
        if self._context_usage_cache is None:
            self._context_usage_cache = estimate_context_usage(
                system=self._harness.config.system,
                messages=self._harness.messages,
                tools=tuple(self._harness.config.tools),
            )
        return self._context_usage_cache

    @property
    def system_prompt(self) -> str:
        """Return the effective system prompt sent to the model."""
        return self._harness.config.system

    @property
    def auto_compact_token_threshold(self) -> int | None:
        """Return the effective automatic compaction threshold, if any."""
        if not self._auto_compact_enabled:
            return None
        if self._auto_compact_token_threshold is not None:
            return self._auto_compact_token_threshold
        if self._runtime_model_limits_key == (self.provider_name, self.model):
            limits = self._runtime_model_limits
            if limits is not None:
                return limits.effective_auto_compact_token_limit
        return auto_compaction_threshold_for_context_window(self.context_window_tokens)

    @property
    def context_window_tokens(self) -> int:
        """Return the active model's discovered or configured context window."""
        if self._runtime_model_limits_key == (self.provider_name, self.model):
            limits = self._runtime_model_limits
            if limits is not None:
                return limits.context_window
        provider = self._active_provider_config()
        if provider is None:
            return DEFAULT_CONTEXT_WINDOW_TOKENS
        return provider.context_windows.get(self.model, DEFAULT_CONTEXT_WINDOW_TOKENS)

    @property
    def context_window_source(self) -> str:
        """Return where the active context-window limit came from."""
        if (
            self._runtime_model_limits_key == (self.provider_name, self.model)
            and self._runtime_model_limits is not None
        ):
            return "provider live catalog"
        return "configured catalog"

    @property
    def model_limits_discovery_error(self) -> str | None:
        """Return the last non-fatal live model-limit discovery error."""
        return self._model_limits_discovery_error

    @property
    def command_registry(self) -> CommandRegistry:
        """Return the slash-command registry used by this session."""
        return self._command_registry

    @property
    def resource_diagnostics(self) -> tuple[ResourceDiagnostic, ...]:
        """Return non-fatal resource and extension diagnostics."""
        return self._resource_diagnostics + self._extension_runtime.diagnostics

    @property
    def extension_runtime(self) -> ExtensionRuntime:
        """Return the extension runtime bound to this session."""
        return self._extension_runtime

    @property
    def extension_names(self) -> tuple[str, ...]:
        """Return loaded extension names in load order."""
        return self._extension_runtime.extension_names

    @property
    def session_stats(self) -> SessionStats:
        """Return cumulative activity and billed usage for the active branch."""
        return calculate_session_stats(
            self._state.entries,
            pricing=self._pricing_for_response,
        )

    def _pricing_for_response(
        self,
        provider_name: str,
        model: str,
        input_tokens: int,
    ) -> dict[str, float] | None:
        provider = _provider_config_for_name(self._config, provider_name)
        if (
            provider is None
            or provider.name != provider_name
            or not hasattr(provider, "model_metadata")
        ):
            return None
        metadata = provider.model_metadata.get(model)
        if metadata is None:
            return None
        for tier in metadata.cost_tiers:
            if tier.max_input_tokens is None or input_tokens <= tier.max_input_tokens:
                return dict(tier.cost)
        return dict(metadata.cost) if metadata.cost else None

    async def emit_pending_session_start(self) -> None:
        """Emit the `session_start` deferred by `load`, once per session.

        Hosts call this after installing their UI bridge so `session_start`
        handlers can use notifications and dialogs (Pi's ordering: the UI
        starts before extensions initialize). Idempotent; a no-op for
        sessions that adopted an already-started extension runtime.
        """
        if not self._session_start_pending:
            return
        self._session_start_pending = False
        await self._extension_runtime.emit_session_start("startup")

    def queue_steering_message(
        self,
        content: str,
        *,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        """Queue a steering user message (extension runtime seam)."""
        message: AgentMessage = (
            CustomMessage(custom_type=custom_type, content=content, details=details)
            if custom_type is not None
            else UserMessage(content=content)
        )
        self._harness.steer_message(message)

    def queue_follow_up_message(
        self,
        content: str,
        *,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        """Queue a follow-up user message (extension runtime seam)."""
        message: AgentMessage = (
            CustomMessage(custom_type=custom_type, content=content, details=details)
            if custom_type is not None
            else UserMessage(content=content)
        )
        self._harness.follow_up_message(message)

    async def append_custom_entry(self, namespace: str, data: dict[str, JSONValue]) -> None:
        """Persist an extension-owned custom entry on the active branch path.

        The entry advances the append-only tree parent chain so it stays on the
        replayed root-to-leaf path (off-path custom entries would be invisible
        to `SessionState` after resume).
        """
        entry = CustomEntry(parent_id=self._last_parent_id, namespace=namespace, data=data)
        await self._append_session_entry(entry)
        self._last_parent_id = entry.id
        leaf = LeafEntry(parent_id=entry.id, entry_id=entry.id)
        await self._append_session_entry(leaf)
        await self._refresh_persisted_state(leaf_id=entry.id)

    @property
    def session_id(self) -> str | None:
        """Return this session's manager id, if indexed."""
        return self._config.session_id

    @property
    def session_title(self) -> str | None:
        """Return this session's indexed human-friendly title, if named."""
        if self._config.session_id is None or self._config.session_manager is None:
            return None
        record = self._config.session_manager.get_session(self._config.session_id)
        if record is None:
            return None
        return record.title

    @property
    def session_manager(self) -> SessionManager | None:
        """Return the session manager, if available."""
        return self._config.session_manager

    @property
    def is_running(self) -> bool:
        """Return whether this session currently has an active agent run."""
        return self._harness.is_running

    @property
    def queued_messages(self) -> QueuedMessages:
        """Return queued steering and follow-up messages."""
        return self._harness.queued_messages

    @property
    def queued_steering_messages(self) -> tuple[str, ...]:
        """Return queued steering message text for UI display."""
        return tuple(message_text(message) for message in self._harness.queued_messages.steering)

    @property
    def queued_follow_up_messages(self) -> tuple[str, ...]:
        """Return queued follow-up message text for UI display."""
        return tuple(message_text(message) for message in self._harness.queued_messages.follow_up)

    @property
    def last_diagnostic_log_path(self) -> Path | None:
        """Return the last diagnostic log path written by this session."""
        return self._last_diagnostic_log_path

    def cancel(self) -> None:
        """Cancel the currently running agent turn, if any."""
        self._harness.cancel()

    def queue_update_event(self) -> QueueUpdateEvent:
        """Return the current queue state as a coding-session event."""
        return QueueUpdateEvent(
            steering=self.queued_steering_messages,
            follow_up=self.queued_follow_up_messages,
        )

    def clear_queued_messages(self) -> QueuedMessages:
        """Clear queued steering and follow-up messages."""
        return self._harness.clear_queues()

    def pop_latest_follow_up_message(self) -> str | None:
        """Remove and return the most recently queued follow-up message."""
        message = self._harness.pop_latest_follow_up()
        return None if message is None else message_text(message)

    def pop_latest_steering_message(self) -> str | None:
        """Remove and return the most recently queued steering message."""
        message = self._harness.pop_latest_steering()
        return None if message is None else message_text(message)

    def set_model(self, model: str) -> None:
        """Switch the active model for future turns and make it the default."""
        provider = self._active_provider_config()
        if provider is not None:
            validate_provider_model(provider, model)
        self._harness.config.model = model
        self._sync_thinking_level_to_active_model()
        self._refresh_runtime_provider()
        self._persist_default_model_choice()
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(
                self._config.session_id,
                model=model,
                provider_name=self.provider_name,
            )

    def set_model_choice(self, choice: ModelChoice) -> None:
        """Switch provider/model as one operation."""
        if choice.provider_name == self.provider_name:
            self.set_model(choice.model)
            return
        self._set_provider_model(choice.provider_name, choice.model)

    def is_scoped_model(self, choice: ModelChoice) -> bool:
        """Return whether a provider/model pair is in the scoped model list."""
        return choice in self.scoped_model_choices

    def toggle_scoped_model(self, choice: ModelChoice) -> tuple[ModelChoice, ...]:
        """Add or remove a model from the persisted scoped model list."""
        if self._provider_settings is None:
            raise ProviderConfigError("Provider settings are not available for this session")
        available = set(self.available_model_choices)
        if choice not in available:
            raise ProviderConfigError(
                f"Model is not available: {choice.provider_name}:{choice.model}"
            )

        self._provider_settings = toggle_saved_scoped_model(
            provider_name=choice.provider_name,
            model=choice.model,
            paths=self._resource_paths.paths,
            fallback_settings=self._provider_settings,
        )
        self._sync_thinking_level_to_active_model()
        return self.scoped_model_choices

    def cycle_scoped_model(self, *, reverse: bool = False) -> ModelChoice:
        """Switch to the next configured scoped model."""
        scoped = self.scoped_model_choices
        if not scoped:
            raise ProviderConfigError("No scoped models configured.")
        current = ModelChoice(provider_name=self.provider_name, model=self.model)
        try:
            current_index = scoped.index(current)
        except ValueError:
            current_index = -1 if not reverse else 0
        delta = -1 if reverse else 1
        choice = scoped[(current_index + delta) % len(scoped)]
        self.set_model_choice(choice)
        return choice

    def set_provider(self, provider_name: str, *, persist_default: bool = True) -> None:
        """Switch the active provider and reset to that provider's default model."""
        if self._provider_settings is None:
            raise ProviderConfigError("Provider settings are not available for this session")
        provider_config = self._provider_settings.get_provider(provider_name)
        self._set_provider_model(
            provider_name,
            provider_config.default_model,
            persist_default=persist_default,
        )

    def _set_provider_model(
        self,
        provider_name: str,
        model: str,
        *,
        persist_default: bool = True,
    ) -> None:
        """Switch active provider/model without constructing an intermediate provider."""
        if self._provider_settings is None:
            raise ProviderConfigError("Provider settings are not available for this session")

        provider_config = self._provider_settings.get_provider(provider_name)
        if model not in provider_config.models:
            raise ProviderConfigError(f"Model is not configured: {provider_name}:{model}")
        thinking_level = _coerced_thinking_level(
            provider_config,
            model=model,
            current=self._thinking_level,
        )
        try:
            provider = create_model_provider(
                provider_config,
                credential_store=self._credential_store,
                model=model,
                thinking_level=thinking_level,
            )
        except RuntimeError as exc:
            raise ProviderConfigError(str(exc)) from exc
        self._owned_providers.append(provider)
        self._harness.config.provider = provider
        self._provider_name = provider_config.name
        self._runtime_provider_config = provider_config
        self._invalidate_runtime_model_limits()
        self._harness.config.model = model
        self._thinking_level = thinking_level
        if persist_default:
            self._persist_default_model_choice()
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(
                self._config.session_id,
                model=model,
                provider_name=self.provider_name,
            )

    async def set_thinking_level(self, level: str) -> str:
        """Persist and activate a thinking mode for future turns."""
        normalized = normalize_thinking_level(level)
        available = self.available_thinking_levels
        if not available:
            raise ValueError(_unavailable_thinking_message(self))
        if normalized not in available:
            modes = ", ".join(available)
            raise ValueError(
                f"Thinking mode {normalized} is not available for "
                f"{self._provider_name}:{self.model}. Available modes: {modes}"
            )
        if normalized == self._thinking_level:
            return f"Thinking mode: {normalized}"

        previous = self._thinking_level
        self._thinking_level = normalized
        try:
            self._refresh_runtime_provider()
        except ProviderConfigError:
            self._thinking_level = previous
            raise

        entry = ThinkingLevelChangeEntry(
            parent_id=self._last_parent_id,
            thinking_level=normalized,
        )
        await self._append_session_entry(entry)
        leaf = LeafEntry(parent_id=entry.id, entry_id=entry.id)
        await self._append_session_entry(leaf)
        self._last_parent_id = entry.id

        self._persist_thinking_level_choice()
        await self._refresh_persisted_state(leaf_id=entry.id)
        return f"Thinking mode: {normalized}"

    async def cycle_thinking_level(self) -> str:
        """Cycle to the next supported thinking mode and persist it."""
        return await self.set_thinking_level(
            next_thinking_level(
                self._thinking_level,
                available=self.available_thinking_levels,
            )
        )

    def _active_provider_config(self) -> ProviderConfig | None:
        if self._provider_settings is None:
            return None
        try:
            return self._provider_settings.get_provider(self._provider_name)
        except ProviderConfigError:
            return None

    def _sync_thinking_level_to_active_model(self) -> None:
        provider = self._active_provider_config()
        if provider is None:
            return
        self._thinking_level = _coerced_thinking_level(
            provider,
            model=self.model,
            current=self._thinking_level,
            preferred=provider.thinking_defaults.get(self.model),
        )

    def _persist_default_model_choice(self) -> None:
        if self._provider_settings is None:
            return
        self._provider_settings = save_default_provider_model(
            provider_name=self.provider_name,
            model=self.model,
            paths=self._resource_paths.paths,
            fallback_settings=self._provider_settings,
        )
        self._sync_thinking_level_to_active_model()

    def _persist_thinking_level_choice(self) -> None:
        if self._provider_settings is None:
            return
        provider = self._active_provider_config()
        if provider is None or self._thinking_level not in provider_thinking_levels(
            provider,
            model=self.model,
        ):
            return
        try:
            self._provider_settings = save_provider_thinking_level(
                provider_name=self.provider_name,
                model=self.model,
                thinking_level=self._thinking_level,
                paths=self._resource_paths.paths,
                fallback_settings=self._provider_settings,
            )
        except ProviderConfigError:
            return

    def _refresh_runtime_provider(self) -> None:
        if self._runtime_provider_config is None:
            return
        provider_config = self._active_provider_config() or self._runtime_provider_config
        validate_provider_model(provider_config, self.model)
        try:
            provider = create_model_provider(
                provider_config,
                credential_store=self._credential_store,
                model=self.model,
                thinking_level=self._thinking_level,
            )
        except RuntimeError as exc:
            raise ProviderConfigError(str(exc)) from exc
        self._owned_providers.append(provider)
        self._harness.config.provider = provider
        self._runtime_provider_config = provider_config
        self._invalidate_runtime_model_limits()

    def _invalidate_runtime_model_limits(self) -> None:
        self._runtime_model_limits = None
        self._runtime_model_limits_key = None
        self._model_limits_discovery_error = None

    async def _refresh_runtime_model_limits(self) -> None:
        key = (self.provider_name, self.model)
        if self._runtime_model_limits_key == key:
            return
        self._runtime_model_limits = None
        self._runtime_model_limits_key = key
        self._model_limits_discovery_error = None
        provider = self._harness.config.provider
        if not isinstance(provider, ModelLimitsProvider):
            return
        try:
            self._runtime_model_limits = await provider.discover_model_limits(self.model)
        except Exception as exc:  # noqa: BLE001 - static catalog remains the safe fallback
            self._model_limits_discovery_error = f"{type(exc).__name__}: {exc}"

    async def reload(self) -> CodingReloadSummary:
        """Reload resources and extensions with an awaited lifecycle boundary.

        Outgoing handlers finish ``session_shutdown(reason="reload")`` while
        their API generation is still active. The runtime then clears UI,
        invalidates/reloads registrations, and awaits the new generation's
        ``session_start(reason="reload")`` so startup-mounted UI is restored
        before the command reports completion.
        """
        await self._extension_runtime.emit_session_shutdown("reload")
        before_skills = _skill_signatures(self._skills)
        before_prompt_templates = _prompt_template_signatures(self._prompt_templates)
        before_context_files = _context_file_signatures(self._context_files)
        before_diagnostics = _diagnostic_signatures(self.resource_diagnostics)
        before_system_prompt_inputs = _system_prompt_resource_signatures(
            skills=self._skills,
            context_files=self._context_files,
        )
        before_extensions = _extension_signatures(self._extension_runtime)
        before_tool_names = tuple(tool.name for tool in self._harness.config.tools)
        before_guidelines = self._extension_runtime.prompt_guidelines

        resources = _load_session_resources(
            self._resource_paths,
            self._config.context_files,
            skills_enabled=self._config.skills_enabled,
        )
        self._reload_extensions()

        after_skills = _skill_signatures(resources.skills)
        after_prompt_templates = _prompt_template_signatures(resources.prompt_templates)
        after_context_files = _context_file_signatures(resources.context_files)
        after_system_prompt_inputs = _system_prompt_resource_signatures(
            skills=resources.skills,
            context_files=resources.context_files,
        )
        after_extensions = _extension_signatures(self._extension_runtime)
        after_tool_names = tuple(tool.name for tool in self._harness.config.tools)
        after_guidelines = self._extension_runtime.prompt_guidelines

        rebuilt_system_prompt: str | None = None
        system_prompt_rebuilt = False
        if self._config.system is None and (
            before_system_prompt_inputs != after_system_prompt_inputs
            or before_tool_names != after_tool_names
            or before_guidelines != after_guidelines
        ):
            rebuilt_system_prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    cwd=self._config.cwd,
                    tools=self._harness.config.tools,
                    skills=resources.skills,
                    custom_prompt=self._config.custom_system_prompt,
                    append_system_prompt=self._config.append_system_prompt,
                    context_files=resources.context_files,
                    extra_guidelines=after_guidelines,
                )
            )
            system_prompt_rebuilt = True

        self._skills = resources.skills
        self._prompt_templates = resources.prompt_templates
        self._context_files = resources.context_files
        self._resource_diagnostics = resources.diagnostics
        after_diagnostics = _diagnostic_signatures(self.resource_diagnostics)
        if rebuilt_system_prompt is not None:
            self._harness.config.system = rebuilt_system_prompt
            self._invalidate_context_usage_cache()

        await self._extension_runtime.emit_session_start("reload")

        return CodingReloadSummary(
            skills=_category_summary(before_skills, after_skills),
            prompt_templates=_category_summary(
                before_prompt_templates,
                after_prompt_templates,
            ),
            context_files=_category_summary(before_context_files, after_context_files),
            extensions=_category_summary(before_extensions, after_extensions),
            diagnostics=_category_summary(before_diagnostics, after_diagnostics),
            system_prompt_rebuilt=system_prompt_rebuilt,
        )

    def _reload_extensions(self) -> None:
        """Re-discover extensions and rebuild dependent tools and commands.

        Extension `setup` re-runs against freshly imported modules. Wrapped
        tools are rebuilt in place on the live harness config, the session
        command registry is rebuilt (unless the caller supplied its own), and
        the harness event fan-out is re-subscribed.
        """
        self._extension_runtime.reset_for_reload()
        if self._config.extensions_enabled or self._config.extension_paths:
            self._extension_runtime.load(
                self._resource_paths,
                extra_paths=self._config.extension_paths,
                include_resource_dirs=self._config.extensions_enabled,
                include_project_dir=self._config.project_extensions_enabled,
            )
        base_tools = (
            self._config.tools
            if self._config.tools is not None
            else create_coding_tools(
                cwd=self._config.cwd,
                shell_command_prefix=self._config.shell_command_prefix,
            )
        )
        self._harness.config.tools = self._extension_runtime.compose_tools(base_tools)
        if self._config.command_registry is None:
            self._command_registry = self._extension_runtime.build_command_registry()
        self._extension_runtime.attach_harness_listener(self._harness.subscribe)

    def reload_provider_settings(self) -> None:
        """Reload provider settings for login and model-selection flows."""
        if self._provider_settings is None:
            return
        previous_settings = self._provider_settings
        previous_thinking_level = self._thinking_level
        self._provider_settings = load_provider_settings(self._resource_paths.paths)
        try:
            self._sync_thinking_level_to_active_model()
            self._refresh_runtime_provider()
        except ProviderConfigError:
            self._provider_settings = previous_settings
            self._thinking_level = previous_thinking_level
            raise

    async def resume(self, session_id: str) -> str:
        """Replace this session's active state with another indexed session."""
        manager = self._config.session_manager
        if manager is None:
            raise ValueError("Session manager is not available")
        record = manager.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")

        provider_name = self._provider_name
        runtime_provider_config = self._runtime_provider_config
        model = self.model
        restore_record_model = False
        if record.provider_name:
            if self._provider_settings is None:
                raise ProviderConfigError(
                    "Cannot resume session provider without provider settings: "
                    f"{record.provider_name}"
                )
            try:
                runtime_provider_config = self._provider_settings.get_provider(record.provider_name)
            except ProviderConfigError as exc:
                raise ProviderConfigError(
                    f"Session provider is not configured: {record.provider_name}"
                ) from exc
            provider_name = runtime_provider_config.name
            model = record.model
            restore_record_model = True
            validate_provider_model(runtime_provider_config, model)

        replacement = await type(self).load(
            CodingSessionConfig(
                provider=self._harness.config.provider,
                model=model,
                cwd=record.cwd,
                storage=jsonl_session_storage(record.path),
                system=self._config.system,
                custom_system_prompt=self._config.custom_system_prompt,
                append_system_prompt=self._config.append_system_prompt,
                context_files=self._config.context_files,
                resource_paths=self._config.resource_paths,
                session_id=record.id,
                session_manager=manager,
                command_registry=self._config.command_registry,
                provider_name=provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=runtime_provider_config,
                auto_compact_token_threshold=self._auto_compact_token_threshold,
                auto_compact_enabled=self._auto_compact_enabled,
                thinking_level=self._thinking_level,
                shell_command_prefix=self._config.shell_command_prefix,
                skills_enabled=self._config.skills_enabled,
                extension_paths=self._config.extension_paths,
                extensions_enabled=self._config.extensions_enabled,
                project_extensions_enabled=self._config.project_extensions_enabled,
                extension_runtime=self._extension_runtime,
            )
        )
        if restore_record_model:
            if runtime_provider_config is None:
                raise ProviderConfigError(f"Session provider is not configured: {provider_name}")
            validate_provider_model(runtime_provider_config, replacement.model)
        else:
            replacement._harness.config.model = self.model
            replacement._sync_thinking_level_to_active_model()
            replacement._refresh_runtime_provider()
        await self._adopt_replacement(replacement, reason="resume")
        return f"Resumed session: {record.id}"

    async def new_session(self) -> str:
        """Replace this session's active state with a pending unindexed session."""
        manager = self._config.session_manager
        if manager is None:
            raise ValueError("Session manager is not available")

        provider_name = self._provider_name
        model = self.model
        runtime_provider_config = self._runtime_provider_config
        thinking_level = self._thinking_level
        if self._provider_settings is not None:
            selection = resolve_provider_selection(self._provider_settings)
            provider_name = selection.provider.name
            model = selection.model
            runtime_provider_config = selection.provider
            thinking_level = _coerced_thinking_level(
                selection.provider,
                model=model,
                current=self._thinking_level,
            )

        record = manager.prepare_session(
            cwd=self.cwd,
            model=model,
            provider_name=provider_name,
        )
        replacement = await type(self).load(
            replace(
                self._config,
                provider=self._harness.config.provider,
                model=record.model or model,
                cwd=record.cwd,
                storage=jsonl_session_storage(record.path),
                session_id=record.id,
                provider_name=provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=runtime_provider_config,
                thinking_level=thinking_level,
                index_on_first_persist=True,
                extension_runtime=self._extension_runtime,
            )
        )
        await self._adopt_replacement(replacement, reason="new")
        return f"Started new session: {record.id}"

    async def _adopt_replacement(
        self,
        replacement: CodingSession,
        *,
        reason: Literal["new", "resume", "branch"],
    ) -> None:
        """Adopt a replacement session's state and re-bind the extension runtime.

        The extension runtime is long-lived and shared with the replacement; it
        must be re-bound to this outer session object because later state
        (transcript persistence, parent ids) mutates here, not on the discarded
        replacement instance.
        """
        await self._extension_runtime.emit_session_shutdown(reason)
        # Tear down extension-owned UI (slot widgets, main views, key
        # interceptors) from the outgoing session after its shutdown handlers
        # run and before session_start fires, so start handlers can re-mount
        # into a clean frontend.
        self._extension_runtime.clear_ui_components()
        self._config = replacement._config
        self._state = replacement._state
        self._harness = replacement._harness
        self._invalidate_context_usage_cache()
        self._last_parent_id = replacement._last_parent_id
        self._skills = replacement._skills
        self._prompt_templates = replacement._prompt_templates
        self._context_files = replacement._context_files
        self._resource_diagnostics = replacement._resource_diagnostics
        self._command_registry = replacement._command_registry
        self._provider_name = replacement._provider_name
        self._provider_settings = replacement._provider_settings
        self._runtime_provider_config = replacement._runtime_provider_config
        self._resource_paths = replacement._resource_paths
        self._auto_compact_token_threshold = replacement._auto_compact_token_threshold
        self._auto_compact_enabled = replacement._auto_compact_enabled
        self._thinking_level = replacement._thinking_level
        self._pending_initial_entries = replacement._pending_initial_entries
        self._extension_runtime = replacement._extension_runtime
        self._extension_runtime.bind(self)
        self._extension_runtime.attach_harness_listener(self._harness.subscribe)
        await self._extension_runtime.emit_session_start(reason)

    async def compact(self, instructions: str | None = None) -> str:
        """Generate a manual compaction summary and rebuild active context."""
        plan = self._manual_compaction_plan()
        summary = await self._generate_compaction_summary(
            plan.messages_to_summarize,
            custom_instructions=instructions,
        )
        compaction = await self._append_compaction(
            summary,
            replace_entry_ids=plan.replace_entry_ids,
        )
        return f"Compacted {len(compaction.replaces_entry_ids)} context entries."

    async def aclose(self) -> None:
        """Close runtime providers created by this coding session."""
        await self._extension_runtime.emit_session_shutdown("quit")
        for provider in self._owned_providers:
            await provider.aclose()
        self._owned_providers.clear()

    def handle_command(self, text: str) -> CommandResult:
        """Handle coding-session slash commands.

        Prompt-template slash commands are expansion directives, so they remain
        unhandled here and flow through `prompt()` for on-the-fly replacement.
        """
        if expand_prompt_template_command(text, self._prompt_templates) is not None:
            return CommandResult(handled=False)
        return self._command_registry.execute(self, text)

    def ensure_session_indexed(self) -> None:
        """Persist pending session metadata and add this session to the resume index."""
        if self._config.session_id is None or self._config.session_manager is None:
            return
        if self._config.session_manager.get_session(self._config.session_id) is None:
            self._config.session_manager.create_session(
                cwd=self.cwd,
                model=self.model,
                provider_name=self.provider_name,
                session_id=self._config.session_id,
            )
        self._config = replace(self._config, index_on_first_persist=False)
        self._ensure_session_file_initialized()

    def expand_prompt_text(self, text: str) -> str:
        """Expand prompt text using loaded markdown resources."""
        expanded_prompt = expand_prompt_template_command(text, self._prompt_templates)
        if expanded_prompt is not None:
            return expanded_prompt
        expanded_skill = expand_skill_command(text, self._skills)
        return expanded_skill if expanded_skill is not None else text

    async def run_terminal_command(
        self,
        command: str,
        *,
        add_to_context: bool,
    ) -> TerminalCommandResult:
        """Run a shell command in the session cwd, optionally adding output to context."""
        normalized_command = command.strip()
        if not normalized_command:
            raise ValueError("Terminal command cannot be empty")

        bash_tool = create_bash_tool(
            cwd=self.cwd,
            shell_command_prefix=self._config.shell_command_prefix,
        )
        result = await bash_tool.execute("terminal-command", {"command": normalized_command})
        exit_code = None
        if isinstance(result.details, dict):
            raw_exit_code = result.details.get("exit_code")
            exit_code = raw_exit_code if isinstance(raw_exit_code, int) else None

        if add_to_context:
            before_count = len(self._harness.messages)
            self._harness.append_message(
                UserMessage(
                    content=_terminal_command_context_message(
                        normalized_command,
                        result.text,
                    )
                )
            )
            self._invalidate_context_usage_cache()
            await self._persist_messages_since(before_count)

        return TerminalCommandResult(
            command=normalized_command,
            output=result.text,
            exit_code=exit_code,
            ok=exit_code == 0,
            added_to_context=add_to_context,
        )

    async def prompt(
        self,
        content: str,
        *,
        streaming_behavior: StreamingBehavior | None = None,
        source: Literal["interactive", "extension"] = "interactive",
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> AsyncIterator[CodingSessionEvent]:
        """Append a user prompt, run the agent, and persist new messages.

        ``custom_type``/``details`` attach custom-message render metadata to the
        appended ``UserMessage`` (used when an extension delivers a custom
        message that starts an idle session's turn). ``source`` marks who
        initiated the turn for the `input` hook (``"extension"`` when an
        extension started it, ``"interactive"`` otherwise).
        """
        context = self._diagnostic_context()
        input_outcome = await self._extension_runtime.run_input_hooks(
            content, source=source, streaming_behavior=streaming_behavior
        )
        if input_outcome.handled:
            if input_outcome.message:
                self._extension_runtime.ui.notify(input_outcome.message)
            return
        content = input_outcome.text
        try:
            expanded_content = self.expand_prompt_text(content)
        except ResourceError:
            raise
        except Exception as exc:
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="expand_prompt",
                exc=exc,
            )
            raise

        if self._harness.is_running:
            if streaming_behavior == "steer":
                self._harness.steer(expanded_content)
                session_event_0 = self.queue_update_event()
                await self._extension_runtime.emit_event(session_event_0)
                yield session_event_0
                return
            if streaming_behavior == "follow_up":
                self._harness.follow_up(expanded_content)
                session_event_0 = self.queue_update_event()
                await self._extension_runtime.emit_event(session_event_0)
                yield session_event_0
                return
            raise RuntimeError(
                "CodingSession is already running; pass streaming_behavior to queue a message."
            )

        await self._refresh_runtime_model_limits()
        await self._try_auto_compact(context=context, phase="auto_compact_before_prompt")
        persisted_count = len(self._harness.messages)
        auto_name_attempted = False
        overflow_message: AssistantMessage | None = None
        try:
            prompt_message: AgentMessage
            if custom_type is not None:
                prompt_message = CustomMessage(
                    custom_type=custom_type,
                    content=expanded_content,
                    display=True,
                    details=details,
                )
            else:
                prompt_message = UserMessage(content=expanded_content)
            events = self._harness.prompt_message(prompt_message)
            self._invalidate_context_usage_cache()
            async for event in events:
                auto_name_message: str | None = None
                if isinstance(event, MessageEndEvent):
                    persisted_count = await self._persist_messages_since(persisted_count)
                    if not auto_name_attempted and isinstance(event.message, UserMessage):
                        auto_name_attempted = True
                        auto_name_message = event.message.text
                if isinstance(event, ToolExecutionEndEvent):
                    self._invalidate_context_usage_cache()
                if (
                    isinstance(event, MessageEndEvent)
                    and isinstance(event.message, AssistantMessage)
                    and event.message.stop_reason == "error"
                ):
                    self._last_diagnostic_log_path = self._diagnostic_logger.log_assistant_error(
                        context=context,
                        phase="agent_loop",
                        message=event.message,
                    )
                    if is_context_overflow_error(event.message):
                        overflow_message = event.message
                if isinstance(event, AgentEndEvent):
                    yield SessionAgentEndEvent(messages=event.messages, will_retry=False)
                else:
                    yield event
                # Let frontends render the confirmed, expanded prompt before
                # session naming performs its separate provider request.
                if auto_name_message is not None:
                    await self._try_auto_name_session(auto_name_message, context=context)
            persisted_count = await self._persist_messages_since(persisted_count)
            if overflow_message is not None:
                session_event_1 = CompactionStartEvent(reason="overflow")
                await self._extension_runtime.emit_event(session_event_1)
                yield session_event_1
                compacted = await self._try_overflow_compact(context=context)
                compaction_end = CompactionEndEvent(
                    reason="overflow",
                    result=None,
                    aborted=not compacted,
                    will_retry=compacted,
                    error_message=None if compacted else "Overflow compaction failed",
                )
                await self._extension_runtime.emit_event(compaction_end)
                yield compaction_end
                if compacted:
                    retry_start = AutoRetryStartEvent(
                        attempt=1,
                        max_attempts=1,
                        delay_ms=0,
                        error_message=overflow_message.error_message or "Context overflow",
                    )
                    await self._extension_runtime.emit_event(retry_start)
                    yield retry_start
                    retry_persisted_count = len(self._harness.messages)
                    retry_events = self._harness.continue_()
                    self._invalidate_context_usage_cache()
                    async for retry_event in retry_events:
                        if isinstance(retry_event, MessageEndEvent):
                            retry_persisted_count = await self._persist_messages_since(
                                retry_persisted_count
                            )
                        if isinstance(retry_event, ToolExecutionEndEvent):
                            self._invalidate_context_usage_cache()
                        if (
                            isinstance(retry_event, MessageEndEvent)
                            and isinstance(retry_event.message, AssistantMessage)
                            and retry_event.message.stop_reason == "error"
                        ):
                            self._last_diagnostic_log_path = (
                                self._diagnostic_logger.log_assistant_error(
                                    context=context,
                                    phase="agent_loop_retry",
                                    message=retry_event.message,
                                )
                            )
                        if isinstance(retry_event, AgentEndEvent):
                            yield SessionAgentEndEvent(
                                messages=retry_event.messages,
                                will_retry=False,
                            )
                        else:
                            yield retry_event
                    await self._persist_messages_since(retry_persisted_count)
                    session_event_4 = AutoRetryEndEvent(success=True, attempt=1, final_error=None)
                    await self._extension_runtime.emit_event(session_event_4)
                    yield session_event_4
                session_event_5 = AgentSettledEvent()
                await self._extension_runtime.emit_event(session_event_5)
                yield session_event_5
                return
            await self._try_auto_compact(context=context, phase="auto_compact_after_prompt")
            session_event_5 = AgentSettledEvent()
            await self._extension_runtime.emit_event(session_event_5)
            yield session_event_5
        except Exception as exc:
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="agent_loop",
                exc=exc,
            )
            raise

    async def continue_(self) -> AsyncIterator[CodingSessionEvent]:
        """Continue the agent from restored state and persist new messages."""
        context = self._diagnostic_context()
        await self._refresh_runtime_model_limits()
        persisted_count = len(self._harness.messages)
        try:
            events = self._harness.continue_()
            self._invalidate_context_usage_cache()
            async for event in events:
                if isinstance(event, MessageEndEvent):
                    persisted_count = await self._persist_messages_since(persisted_count)
                if isinstance(event, ToolExecutionEndEvent):
                    self._invalidate_context_usage_cache()
                if (
                    isinstance(event, MessageEndEvent)
                    and isinstance(event.message, AssistantMessage)
                    and event.message.stop_reason == "error"
                ):
                    self._last_diagnostic_log_path = self._diagnostic_logger.log_assistant_error(
                        context=context,
                        phase="agent_loop",
                        message=event.message,
                    )
                if isinstance(event, AgentEndEvent):
                    yield SessionAgentEndEvent(messages=event.messages, will_retry=False)
                else:
                    yield event
            await self._persist_messages_since(persisted_count)
            await self._try_auto_compact(context=context, phase="auto_compact_after_continue")
            session_event_5 = AgentSettledEvent()
            await self._extension_runtime.emit_event(session_event_5)
            yield session_event_5
        except Exception as exc:
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="agent_loop",
                exc=exc,
            )
            raise

    def _diagnostic_context(self) -> AgentCallDiagnosticContext:
        return AgentCallDiagnosticContext(
            provider_name=self._provider_name,
            model=self.model,
            cwd=self.cwd,
            session_id=self.session_id,
            run_id=new_agent_call_run_id(),
        )

    async def _persist_loaded_interrupted_tool_repairs(self) -> None:
        """Persist repairs for loaded sessions with dangling tool calls.

        Older Tau builds repaired interrupted tool-call transcripts only in the
        in-memory harness. If the app was later resumed from JSONL, the synthetic
        tool result was absent and providers rejected the whole transcript. Repair
        the active branch on load so resume/tree branches are durable and
        provider-safe.
        """
        repair = _interrupted_tool_repair_plan(
            self._state.messages,
            context_entry_ids=self._state.context_entry_ids,
        )
        if repair is None:
            return

        parent_id, suffix = repair
        for message in suffix:
            entry = MessageEntry(parent_id=parent_id, message=message)
            await self._append_session_entry(entry)
            parent_id = entry.id
        leaf = LeafEntry(parent_id=parent_id, entry_id=parent_id)
        await self._append_session_entry(leaf)
        self._last_parent_id = parent_id
        await self._refresh_persisted_state(leaf_id=parent_id)
        self._harness = AgentHarness(
            AgentHarnessConfig(
                provider=self._harness.config.provider,
                model=self._harness.config.model,
                system=self._harness.config.system,
                tools=self._harness.config.tools,
                max_turns=self._harness.config.max_turns,
                queue_mode=self._harness.config.queue_mode,
            ),
            messages=self._state.messages,
        )

    async def _persist_messages_since(self, persisted_count: int) -> int:
        """Persist completed harness messages after ``persisted_count``.

        Message lifecycle events are the durable-message boundary. Each persisted
        message advances the append-only tree and records a leaf pointer so tree
        navigation can observe the current branch while a run is still active.
        """
        new_messages = self._harness.messages[persisted_count:]
        if not new_messages:
            return persisted_count

        for message in new_messages:
            entry = MessageEntry(parent_id=self._last_parent_id, message=message)
            await self._append_session_entry(entry)
            self._last_parent_id = entry.id
            leaf = LeafEntry(parent_id=entry.id, entry_id=entry.id)
            await self._append_session_entry(leaf)

        await self._refresh_persisted_state(leaf_id=self._last_parent_id)
        self._invalidate_context_usage_cache()
        return persisted_count + len(new_messages)

    def _invalidate_context_usage_cache(self) -> None:
        """Mark context accounting dirty after transcript/system/tool changes."""
        self._context_usage_cache = None

    async def _refresh_persisted_state(self, *, leaf_id: str | None) -> None:
        entries = await self._read_session_entries()
        self._state = SessionState.from_entries(entries, leaf_id=leaf_id)
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(
                self._config.session_id,
                model=self.model,
                provider_name=self.provider_name,
            )

    async def _read_session_entries(self) -> list[SessionEntry]:
        """Read stored entries, detaching roots imported from external history."""
        return _detach_missing_parents(await self._config.storage.read_all())

    async def _append_session_entry(self, entry: SessionEntry) -> None:
        """Append one durable entry after flushing deferred session metadata."""
        await self._ensure_session_initialized()
        await self._config.storage.append(entry)

    async def _ensure_session_initialized(self) -> None:
        if not self._pending_initial_entries:
            return
        await self._write_pending_initial_entries()
        if self._config.index_on_first_persist:
            self._index_current_session()

    async def _write_pending_initial_entries(self) -> None:
        for entry in self._pending_initial_entries:
            await self._config.storage.append(entry)
        self._pending_initial_entries = ()

    def _ensure_session_file_initialized(self) -> None:
        if not self._pending_initial_entries:
            return
        for entry in self._pending_initial_entries:
            _append_session_entry_sync(self._config.storage, entry)
        self._pending_initial_entries = ()

    def _index_current_session(self) -> None:
        if self._config.session_id is None or self._config.session_manager is None:
            return
        existing = self._config.session_manager.get_session(self._config.session_id)
        if existing is not None:
            return
        self._config.session_manager.create_session(
            cwd=self.cwd,
            model=self.model,
            provider_name=self.provider_name,
            session_id=self._config.session_id,
        )

    async def _try_auto_compact(
        self,
        *,
        context: AgentCallDiagnosticContext,
        phase: str,
    ) -> bool:
        try:
            return await self._maybe_auto_compact()
        except Exception as exc:  # noqa: BLE001 - automatic compaction must not lose a turn
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase=phase,
                exc=exc,
            )
            return False

    async def _try_overflow_compact(
        self,
        *,
        context: AgentCallDiagnosticContext,
    ) -> bool:
        try:
            plan = self._recent_preserving_compaction_plan()
            if plan is None:
                return False
            summary = await self._generate_compaction_summary(plan.messages_to_summarize)
            await self._append_compaction(summary, replace_entry_ids=plan.replace_entry_ids)
            return True
        except Exception as exc:  # noqa: BLE001 - the original overflow remains visible
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="overflow_compact",
                exc=exc,
            )
            return False

    async def _try_auto_name_session(
        self,
        first_message: str,
        *,
        context: AgentCallDiagnosticContext,
    ) -> None:
        if not self._should_auto_name_session():
            return
        try:
            title = await self._generate_session_name(first_message)
        except Exception as exc:  # noqa: BLE001 - naming must not interrupt the agent turn
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="auto_name_session",
                exc=exc,
            )
            title = _fallback_session_name(first_message)
        if title is None:
            title = _fallback_session_name(first_message)
        if title is None:
            return
        self._set_auto_session_title(title)

    def _should_auto_name_session(self) -> bool:
        if self._config.session_id is None or self._config.session_manager is None:
            return False
        record = self._config.session_manager.get_session(self._config.session_id)
        if record is not None and record.title:
            return False
        return sum(isinstance(message, UserMessage) for message in self._harness.messages) == 1

    async def _generate_session_name(self, first_message: str) -> str | None:
        prompt = (
            "Create a concise session name for this first user message. "
            "Use at most four words.\n\n"
            f"User message:\n{first_message}"
        )
        text_parts: list[str] = []
        final_text: str | None = None
        async for event in self._harness.config.provider.stream_response(
            model=self.model,
            system=SESSION_NAME_SYSTEM_PROMPT,
            messages=[UserMessage(content=prompt)],
            tools=[],
        ):
            if isinstance(event, TextDeltaEvent):
                text_parts.append(event.delta)
            elif isinstance(event, AssistantDoneEvent):
                final_text = event.message.text
            elif isinstance(event, AssistantErrorEvent):
                raise RuntimeError(
                    f"Session naming failed: {event.error.error_message or event.reason}"
                )
        return _sanitize_session_name(final_text if final_text is not None else "".join(text_parts))

    def _set_auto_session_title(self, title: str) -> None:
        if self._config.session_id is None or self._config.session_manager is None:
            return
        existing = self._config.session_manager.get_session(self._config.session_id)
        if existing is not None and existing.title:
            return
        self._config.session_manager.touch_session(
            self._config.session_id,
            model=self.model,
            provider_name=self.provider_name,
            title=title,
        )

    def _provider_is_usable(self, provider: ProviderConfig) -> bool:
        return provider_has_usable_credentials(
            provider,
            credential_reader=self._credential_store,
        )

    def _usable_provider_configs(self) -> tuple[ProviderConfig, ...]:
        if self._provider_settings is None:
            return ()
        return tuple(
            provider
            for provider in self._provider_settings.providers
            if self._provider_is_usable(provider)
        )

    async def _maybe_auto_compact(self) -> bool:
        threshold = self.auto_compact_token_threshold
        if threshold is None or threshold <= 0:
            return False
        if len(self._state.context_entry_ids) < 2:
            return False
        if self.context_token_estimate <= threshold:
            return False
        plan = self._recent_preserving_compaction_plan()
        if plan is None:
            return False
        summary = await self._generate_compaction_summary(plan.messages_to_summarize)
        await self._append_compaction(summary, replace_entry_ids=plan.replace_entry_ids)
        return True

    async def _generate_compaction_summary(
        self,
        messages: tuple[AgentMessage, ...],
        *,
        custom_instructions: str | None = None,
    ) -> str:
        prompt = build_compaction_summary_prompt(
            messages,
            custom_instructions=custom_instructions,
        )
        text_parts: list[str] = []
        final_text: str | None = None
        summary_messages: list[AgentMessage] = [UserMessage(content=prompt)]
        async for event in self._harness.config.provider.stream_response(
            model=self.model,
            system=SUMMARIZATION_SYSTEM_PROMPT,
            messages=summary_messages,
            tools=[],
        ):
            if isinstance(event, TextDeltaEvent):
                text_parts.append(event.delta)
            elif isinstance(event, AssistantDoneEvent):
                final_text = event.message.text
            elif isinstance(event, AssistantErrorEvent):
                raise RuntimeError(
                    f"Compaction summarization failed: {event.error.error_message or event.reason}"
                )

        summary = (final_text if final_text is not None else "".join(text_parts)).strip()
        if not summary:
            raise RuntimeError("Compaction summarization returned an empty summary")
        return summary

    async def _summarize_branch_messages(
        self,
        messages: tuple[AgentMessage, ...],
        *,
        custom_instructions: str | None = None,
        replace_instructions: bool = False,
    ) -> str:
        try:
            summary = await summarize_branch_messages_with_model(
                provider=self._harness.config.provider,
                model=self.model,
                messages=messages,
                custom_instructions=custom_instructions,
                replace_instructions=replace_instructions,
            )
        except Exception:
            summary = None
        return summary or summarize_messages_for_compaction(messages)

    def _manual_compaction_plan(self) -> CompactionPlan:
        rows = self._active_context_rows()
        if not rows:
            raise ValueError("No active context messages to compact")
        return CompactionPlan(
            replace_entry_ids=tuple(entry_id for entry_id, _message in rows),
            messages_to_summarize=tuple(message for _entry_id, message in rows),
        )

    def _recent_preserving_compaction_plan(self) -> CompactionPlan | None:
        rows = self._active_context_rows()
        if len(rows) < 2:
            return None

        first_kept_index = _first_recent_context_index(
            rows,
            keep_recent_tokens=DEFAULT_COMPACTION_KEEP_RECENT_TOKENS,
        )
        if first_kept_index <= 0:
            return None

        replaced = rows[:first_kept_index]
        if not replaced:
            return None
        return CompactionPlan(
            replace_entry_ids=tuple(entry_id for entry_id, _message in replaced),
            messages_to_summarize=tuple(message for _entry_id, message in replaced),
        )

    def _active_context_rows(self) -> tuple[tuple[str, AgentMessage], ...]:
        return tuple(zip(self._state.context_entry_ids, self._state.messages, strict=True))

    async def _append_compaction(
        self,
        summary: str,
        *,
        replace_entry_ids: tuple[str, ...],
    ) -> CompactionEntry:
        if not replace_entry_ids:
            raise ValueError("No active context messages to compact")

        compaction = CompactionEntry(
            parent_id=self._last_parent_id,
            summary=summary,
            replaces_entry_ids=list(replace_entry_ids),
        )
        await self._append_session_entry(compaction)
        leaf = LeafEntry(parent_id=compaction.id, entry_id=compaction.id)
        await self._append_session_entry(leaf)
        self._last_parent_id = compaction.id

        await self._refresh_persisted_state(leaf_id=compaction.id)
        self._harness.replace_messages(self._state.messages)
        self._invalidate_context_usage_cache()
        return compaction


def _first_recent_context_index(
    rows: tuple[tuple[str, AgentMessage], ...],
    *,
    keep_recent_tokens: int,
) -> int:
    if keep_recent_tokens <= 0:
        return len(rows)

    accumulated_tokens = 0
    candidate_index: int | None = None
    for index in range(len(rows) - 1, -1, -1):
        _entry_id, message = rows[index]
        accumulated_tokens += estimate_message_tokens(message)
        if accumulated_tokens >= keep_recent_tokens:
            candidate_index = index
            break

    if candidate_index is None:
        return 0

    candidate_message = rows[candidate_index][1]
    if candidate_message.role == "user":
        if candidate_index > 0:
            return candidate_index
        next_user_index = _next_user_message_index(rows, start=1)
        return next_user_index if next_user_index is not None else 0

    next_user_index = _next_user_message_index(rows, start=candidate_index + 1)
    if next_user_index is not None:
        return next_user_index

    for index in range(candidate_index, len(rows)):
        if rows[index][1].role != "toolResult":
            return index
    return len(rows)


def _next_user_message_index(
    rows: tuple[tuple[str, AgentMessage], ...],
    *,
    start: int,
) -> int | None:
    for index in range(start, len(rows)):
        if rows[index][1].role == "user":
            return index
    return None


def is_context_overflow_error(message: AssistantMessage) -> bool:
    """Return True when an assistant error looks like a context overflow."""
    text = message.error_message or ""
    normalized = text.lower()
    markers = (
        "context length",
        "context window",
        "context limit",
        "maximum context",
        "max context",
        "input is too long",
        "input length",
        "prompt is too long",
        "too many tokens",
        "token limit",
        "exceeds the limit",
        "exceeded the limit",
    )
    return any(marker in normalized for marker in markers)


def _detach_missing_parents(entries: list[SessionEntry]) -> list[SessionEntry]:
    """Return entries with dangling parent pointers detached from external history."""
    entry_ids = {entry.id for entry in entries}
    return [
        entry.model_copy(update={"parent_id": None})
        if entry.parent_id is not None and entry.parent_id not in entry_ids
        else entry
        for entry in entries
    ]


def _last_parent_id_from_state(state: SessionState) -> str | None:
    if state.active_leaf_id is not None:
        return state.active_leaf_id
    if state.entries:
        return state.entries[-1].id
    return None


def _latest_leaf_entry(entries: list[SessionEntry]) -> LeafEntry | None:
    for entry in reversed(entries):
        if isinstance(entry, LeafEntry):
            return entry
    return None


def _is_branchable_tree_entry(entry: SessionEntry) -> bool:
    if entry.type in {"compaction", "branch_summary"}:
        return True
    if entry.type != "message":
        return False
    return isinstance(entry.message, UserMessage | AssistantMessage)


def _tree_choice_label(entry: SessionEntry, *, branch_indent: int = 0) -> str:
    prefix = "  " * branch_indent
    return f"{prefix}{_tree_entry_title(entry)}"


def _tree_branch_indents(entries: list[SessionEntry]) -> dict[str, int]:
    children_by_parent: dict[str | None, list[str]] = {}
    for entry in entries:
        if entry.type != "leaf":
            children_by_parent.setdefault(entry.parent_id, []).append(entry.id)

    sibling_indexes = {
        child_id: index
        for children in children_by_parent.values()
        for index, child_id in enumerate(children)
    }
    indents: dict[str, int] = {}
    for entry in entries:
        if entry.type == "leaf":
            continue
        parent_indent = indents.get(entry.parent_id, 0) if entry.parent_id is not None else 0
        sibling_index = sibling_indexes.get(entry.id, 0)
        indents[entry.id] = parent_indent + (1 if sibling_index > 0 else 0)
    return indents


def _ordered_tree_entries(entries: list[SessionEntry]) -> tuple[SessionEntry, ...]:
    children_by_parent: dict[str | None, list[SessionEntry]] = {}
    for entry in entries:
        if entry.type != "leaf":
            children_by_parent.setdefault(entry.parent_id, []).append(entry)

    ordered: list[SessionEntry] = []
    seen: set[str] = set()
    expanded: set[str | None] = set()

    def append_descendants(root_parent_id: str | None) -> None:
        # Iterative depth-first walk rather than recursion so a long session (a
        # deep root-to-leaf entry chain) cannot exceed Python's recursion limit.
        # `expanded` also makes a malformed parent cycle terminate instead of
        # recursing forever. Emitting a node's direct children before descending,
        # and pushing them reversed so the first child is processed next,
        # preserves the original traversal order.
        stack: list[str | None] = [root_parent_id]
        while stack:
            parent_id = stack.pop()
            if parent_id in expanded:
                continue
            expanded.add(parent_id)
            children = children_by_parent.get(parent_id, [])
            for child in children:
                if child.id not in seen:
                    ordered.append(child)
                    seen.add(child.id)
            for child in reversed(children):
                stack.append(child.id)

    append_descendants(None)
    for entry in entries:
        if entry.type != "leaf" and entry.id not in seen:
            ordered.append(entry)
            seen.add(entry.id)
            append_descendants(entry.id)
    return tuple(ordered)


def _is_tool_call_tree_entry(entry: SessionEntry) -> bool:
    return (
        entry.type == "message"
        and isinstance(entry.message, AssistantMessage)
        and bool(entry.message.tool_calls)
    )


def _tree_entry_title(entry: SessionEntry) -> str:
    match entry.type:
        case "message":
            message = entry.message
            if (
                isinstance(message, AssistantMessage)
                and message.tool_calls
                and not message.text.strip()
            ):
                tool_names = ", ".join(call.name for call in message.tool_calls)
                return f"tool call: {tool_names}"
            return f"{message.role}: {_message_text_preview(message)}"
        case "compaction":
            return f"compaction summary: {_short_preview(entry.summary)}"
        case "branch_summary":
            return f"branch summary: {_short_preview(entry.summary)}"
        case _:
            return entry.type


def _message_text_preview(message: AgentMessage) -> str:
    return _short_preview(message_text(message))


def _short_preview(text: str, *, limit: int = 72) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized or "(empty)"
    return f"{normalized[: limit - 1]}..."


def _messages_after_entry_on_active_path(
    entries: list[SessionEntry],
    entry_id: str,
    active_leaf_id: str | None,
) -> tuple[AgentMessage, ...]:
    if active_leaf_id is None:
        return ()
    try:
        active_path = path_to_entry(entries, active_leaf_id)
    except SessionTreeError:
        return ()
    try:
        target_index = next(
            index for index, entry in enumerate(active_path) if entry.id == entry_id
        )
    except StopIteration:
        return ()
    return tuple(
        entry.message for entry in active_path[target_index + 1 :] if entry.type == "message"
    )


def _storage_path(storage: SessionStorage) -> Path | None:
    path = getattr(storage, "path", None)
    return path if isinstance(path, Path) else None


def _resolve_export_destination(
    destination: Path | None,
    *,
    cwd: Path,
    session_path: Path | None,
    format: str,
) -> Path:
    if destination is None:
        if session_path is not None:
            return default_session_export_artifact_path(
                session_path,
                destination_dir=cwd,
                format=format,
            )
        return cwd / f"tau-session.{format}"

    resolved = destination if destination.is_absolute() else cwd / destination
    if resolved.suffix:
        return resolved
    name = session_path.stem if session_path is not None else "tau-session"
    return default_session_export_artifact_path(
        Path(name),
        destination_dir=resolved,
        format=format,
    )


def _session_export_title(session: CodingSession) -> str:
    manager = session.session_manager
    session_id = session.session_id
    if manager is not None and session_id is not None:
        record = manager.get_session(session_id)
        if record is not None and record.title:
            return record.title
    return f"Tau session {session_id}" if session_id is not None else "Tau Session Export"


def _initial_model_for_config(config: CodingSessionConfig) -> str:
    if config.provider_settings is None or config.runtime_provider_config is None:
        return config.model
    provider = _provider_config_for_name(config, config.provider_name)
    if provider is None:
        return config.model
    try:
        validate_provider_model(provider, config.model)
    except ProviderConfigError:
        return provider.default_model
    return config.model


def _runtime_model_for_state(config: CodingSessionConfig, state: SessionState) -> str:
    state_model = state.model or config.model
    if config.provider_settings is None or config.runtime_provider_config is None:
        return state_model
    provider = _provider_config_for_name(config, config.provider_name)
    if provider is None:
        return state_model
    try:
        validate_provider_model(provider, state_model)
    except ProviderConfigError:
        return config.model if config.model in provider.models else provider.default_model
    return state_model


def _initial_thinking_level_for_config(
    config: CodingSessionConfig,
    *,
    model: str,
) -> ThinkingLevel:
    provider = _provider_config_for_name(config, config.provider_name)
    if provider is None:
        return config.thinking_level
    return _preferred_thinking_level_for_model(
        provider,
        model=model,
        fallback=config.thinking_level,
    )


def _provider_config_for_name(
    config: CodingSessionConfig,
    provider_name: str,
) -> ProviderConfig | None:
    if config.provider_settings is not None:
        try:
            return config.provider_settings.get_provider(provider_name)
        except ProviderConfigError:
            pass
    if config.runtime_provider_config is not None:
        return config.runtime_provider_config
    return None


def _state_thinking_level(
    state: SessionState,
    default: ThinkingLevel,
) -> ThinkingLevel:
    thinking_level = getattr(state, "thinking_level", None)
    if thinking_level is None:
        return default
    return normalize_thinking_level(thinking_level)


def _default_thinking_level_for_active_model(session: CodingSession) -> ThinkingLevel:
    provider = session._active_provider_config()
    if provider is None:
        return session._config.thinking_level
    return _preferred_thinking_level_for_model(
        provider,
        model=session.model,
        fallback=session._config.thinking_level,
    )


def _preferred_thinking_level_for_model(
    provider: ProviderConfig,
    *,
    model: str,
    fallback: ThinkingLevel,
) -> ThinkingLevel:
    levels = provider_thinking_levels(provider, model=model)
    preferred = provider.thinking_defaults.get(model)
    if preferred in levels:
        return preferred
    if fallback in levels or not levels:
        return fallback
    default = provider_default_thinking_level(provider, model=model)
    return default or levels[0]


def _coerced_thinking_level(
    provider: ProviderConfig,
    *,
    model: str,
    current: ThinkingLevel,
    preferred: ThinkingLevel | None = None,
) -> ThinkingLevel:
    levels = provider_thinking_levels(provider, model=model)
    if not levels or current in levels:
        return current
    if preferred in levels:
        return preferred
    default = provider_default_thinking_level(provider, model=model)
    return default or levels[0]


def _unavailable_thinking_message(session: CodingSession) -> str:
    message = f"Thinking controls are unavailable for {session.provider_name}:{session.model}"
    reason = session.thinking_unavailable_reason
    if reason:
        return f"{message}: {reason}"
    return message


def _sanitize_session_name(text: str) -> str | None:
    cleaned = " ".join(text.split()).strip()
    cleaned = cleaned.strip("\"'`“”‘’")
    cleaned = cleaned.strip(string.punctuation + " ")
    words = [word.strip(string.punctuation + "\"'`“”‘’") for word in cleaned.split()]
    words = [word for word in words if word]
    if not words:
        return None
    return " ".join(words[:4])


def _fallback_session_name(first_message: str) -> str | None:
    return _sanitize_session_name(first_message)


def _terminal_command_context_message(command: str, output: str) -> str:
    return (
        "Terminal command executed by the user.\n\n"
        f"Command:\n```bash\n{command}\n```\n\n"
        f"Output:\n```text\n{output}\n```"
    )


def parse_terminal_command(text: str) -> TerminalCommandRequest | None:
    """Parse input-bar terminal command syntax."""
    stripped = text.strip()
    if stripped.startswith("!!"):
        command = stripped[2:].strip()
        if not command:
            return None
        return TerminalCommandRequest(command=command, add_to_context=False)
    if stripped.startswith("!"):
        command = stripped[1:].strip()
        if not command:
            return None
        return TerminalCommandRequest(command=command, add_to_context=True)
    return None


def _category_summary(
    before: tuple[tuple[object, ...], ...],
    after: tuple[tuple[object, ...], ...],
) -> ReloadCategorySummary:
    return ReloadCategorySummary(
        before=len(before),
        after=len(after),
        changed=before != after,
    )


def _skill_signatures(skills: tuple[Skill, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (skill.name, str(skill.path), skill.description, skill.content) for skill in skills
    )


def _prompt_template_signatures(
    prompt_templates: tuple[PromptTemplate, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (template.name, str(template.path), template.description, template.content)
        for template in prompt_templates
    )


def _context_file_signatures(
    context_files: tuple[ProjectContextFile, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple((context_file.path, context_file.content) for context_file in context_files)


def _diagnostic_signatures(
    diagnostics: tuple[ResourceDiagnostic, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            diagnostic.kind,
            diagnostic.message,
            str(diagnostic.path) if diagnostic.path is not None else None,
            diagnostic.name,
            diagnostic.severity,
        )
        for diagnostic in diagnostics
    )


def _extension_signatures(runtime: ExtensionRuntime) -> tuple[tuple[object, ...], ...]:
    return tuple((name,) for name in runtime.extension_names)


def _system_prompt_resource_signatures(
    *,
    skills: tuple[Skill, ...],
    context_files: tuple[ProjectContextFile, ...],
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    prompt_skills = tuple(
        (skill.name, str(skill.path), skill.description)
        for skill in sorted(skills, key=lambda item: item.name)
    )
    return (prompt_skills, _context_file_signatures(context_files))


def _load_session_resources(
    resource_paths: TauResourcePaths,
    explicit_context_files: tuple[ProjectContextFile, ...],
    *,
    skills_enabled: bool = True,
) -> SessionResources:
    loaded_skills: list[Skill]
    skill_diagnostics: list[ResourceDiagnostic]
    if skills_enabled:
        loaded_skills, skill_diagnostics = load_skills_with_diagnostics(resource_paths)
    else:
        loaded_skills, skill_diagnostics = [], []
    loaded_prompt_templates, prompt_diagnostics = load_prompt_templates_with_diagnostics(
        resource_paths
    )
    discovered_context, context_diagnostics = discover_project_context_with_diagnostics(
        resource_paths
    )
    return SessionResources(
        skills=tuple(loaded_skills),
        prompt_templates=tuple(loaded_prompt_templates),
        context_files=_merge_context_files(explicit_context_files, discovered_context),
        diagnostics=tuple([*skill_diagnostics, *prompt_diagnostics, *context_diagnostics]),
    )


def _merge_context_files(
    explicit: tuple[ProjectContextFile, ...],
    discovered: tuple[ProjectContextFile, ...],
) -> tuple[ProjectContextFile, ...]:
    merged: list[ProjectContextFile] = []
    seen: set[str] = set()
    for context_file in (*explicit, *discovered):
        if context_file.path in seen:
            continue
        seen.add(context_file.path)
        merged.append(context_file)
    return tuple(merged)


def _interrupted_tool_repair_plan(
    messages: tuple[AgentMessage, ...],
    *,
    context_entry_ids: tuple[str, ...],
) -> tuple[str, tuple[AgentMessage, ...]] | None:
    repaired: list[AgentMessage] = []
    returned_ids = {
        message.tool_call_id for message in messages if isinstance(message, ToolResultMessage)
    }
    for message in messages:
        repaired.append(message)
        if not isinstance(message, AssistantMessage):
            continue
        for tool_call in message.tool_calls:
            if tool_call.id in returned_ids:
                continue
            returned_ids.add(tool_call.id)
            content = "Tool call interrupted by user"
            repaired.append(
                ToolResultMessage(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=[TextContent(text=content)],
                    is_error=True,
                )
            )

    if tuple(repaired) == messages:
        return None

    common_prefix_length = 0
    for old_message, repaired_message in zip(messages, repaired, strict=False):
        if old_message != repaired_message:
            break
        common_prefix_length += 1
    if common_prefix_length == 0:
        return None
    return context_entry_ids[common_prefix_length - 1], tuple(repaired[common_prefix_length:])


def default_session_path(cwd: Path) -> Path:
    """Return Tau's default user-home session path for a project cwd."""
    return TauPaths().default_session_path(cwd)


def jsonl_session_storage(path: str | Path) -> JsonlSessionStorage:
    """Convenience factory for local JSONL coding-session storage."""
    return JsonlSessionStorage(path)


def _append_session_entry_sync(storage: SessionStorage, entry: SessionEntry) -> None:
    """Append an entry synchronously for slash commands that cannot await storage."""
    if isinstance(storage, JsonlSessionStorage):
        storage.path.parent.mkdir(parents=True, exist_ok=True)
        with storage.path.open("a", encoding="utf-8") as file:
            file.write(entry_to_json_line(entry))
        return
    raise RuntimeError("Session storage does not support synchronous initialization")
