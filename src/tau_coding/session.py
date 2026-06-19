"""Persistent coding-session wrapper built on AgentHarness."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from tau_agent import (
    AgentEvent,
    AgentHarness,
    AgentHarnessConfig,
    ErrorEvent,
    QueuedMessages,
    QueueUpdateEvent,
)
from tau_agent.messages import AgentMessage, AssistantMessage, UserMessage
from tau_agent.session import (
    BranchSummaryEntry,
    CompactionEntry,
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
from tau_agent.session.tree import SessionTreeError, path_to_entry
from tau_agent.tools import AgentTool
from tau_ai import ModelProvider
from tau_coding.commands import CommandRegistry, CommandResult, create_default_command_registry
from tau_coding.context import discover_project_context_with_diagnostics
from tau_coding.context_window import (
    ContextUsageEstimate,
    estimate_context_usage,
    summarize_messages_for_compaction,
)
from tau_coding.credentials import FileCredentialStore, credentials_path
from tau_coding.diagnostics import (
    AgentCallDiagnosticContext,
    AgentCallDiagnosticLogger,
    new_agent_call_run_id,
)
from tau_coding.paths import TauPaths
from tau_coding.prompt_templates import (
    PromptTemplate,
    load_prompt_templates_with_diagnostics,
)
from tau_coding.provider_config import (
    ProviderConfig,
    ProviderConfigError,
    ProviderSettings,
    ScopedModelConfig,
    load_provider_settings,
    provider_default_thinking_level,
    provider_has_usable_credentials,
    provider_thinking_levels,
    save_provider_settings,
)
from tau_coding.provider_runtime import ClosableModelProvider, create_model_provider
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
    thinking_level: ThinkingLevel = DEFAULT_THINKING_LEVEL


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
    ) -> None:
        self._config = config
        self._state = state
        self._harness = harness
        self._last_parent_id = last_parent_id
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
        self._thinking_level = _state_thinking_level(state, config.thinking_level)
        self._owned_providers: list[ClosableModelProvider] = []
        self._diagnostic_logger = AgentCallDiagnosticLogger.from_paths(self._resource_paths.paths)
        self._credential_store = FileCredentialStore(
            credentials_path(self._resource_paths.paths) if self._resource_paths.paths else None
        )
        self._last_diagnostic_log_path: Path | None = None

    @classmethod
    async def load(cls, config: CodingSessionConfig) -> CodingSession:
        """Load a coding session from append-only storage."""
        entries = await config.storage.read_all()
        if not entries:
            info = SessionInfoEntry(cwd=str(config.cwd))
            model = ModelChangeEntry(parent_id=info.id, model=config.model)
            thinking = ThinkingLevelChangeEntry(
                parent_id=model.id,
                thinking_level=config.thinking_level,
            )
            await config.storage.append(info)
            await config.storage.append(model)
            await config.storage.append(thinking)
            entries = [info, model, thinking]

        linear_state = SessionState.from_entries(entries)
        state = (
            SessionState.from_entries(entries, leaf_id=linear_state.active_leaf_id)
            if linear_state.active_leaf_id is not None
            else linear_state
        )
        tools = config.tools if config.tools is not None else create_coding_tools(cwd=config.cwd)
        resource_paths = resource_paths_with_cwd(config.resource_paths, config.cwd)
        resources = _load_session_resources(resource_paths, config.context_files)
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
                )
            )
        )
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=config.provider,
                model=state.model or config.model,
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
            command_registry=config.command_registry,
        )
        session._sync_thinking_level_to_active_model()
        session._refresh_runtime_provider()
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
    def messages(self) -> tuple[AgentMessage, ...]:
        """Return the restored/current transcript."""
        return self._harness.messages

    @property
    def state(self) -> SessionState:
        """Return the last replayed durable session state."""
        return self._state

    async def tree_choices(self) -> tuple[SessionTreeChoice, ...]:
        """Return branchable session entries for a tree picker."""
        entries = await self._config.storage.read_all()
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

    async def branch_to_entry(self, entry_id: str, *, summarize: bool = False) -> str:
        """Move the active leaf to a previous entry, preserving existing history."""
        entries = await self._config.storage.read_all()
        by_id = {entry.id: entry for entry in entries}
        if entry_id not in by_id:
            raise ValueError(f"Unknown session entry: {entry_id}")
        if not _is_branchable_tree_entry(by_id[entry_id]):
            raise ValueError(f"Session entry cannot be branched from: {entry_id}")

        target_id = entry_id
        summary_entry: BranchSummaryEntry | None = None
        if summarize:
            abandoned_messages = _messages_after_entry_on_active_path(
                entries,
                entry_id,
                self._last_parent_id,
            )
            if abandoned_messages:
                summary_entry = BranchSummaryEntry(
                    parent_id=entry_id,
                    branch_root_id=entry_id,
                    summary=summarize_messages_for_compaction(abandoned_messages),
                )
                await self._config.storage.append(summary_entry)
                target_id = summary_entry.id

        leaf = LeafEntry(parent_id=target_id, entry_id=target_id)
        await self._config.storage.append(leaf)
        self._last_parent_id = target_id

        entries = await self._config.storage.read_all()
        self._state = SessionState.from_entries(entries, leaf_id=target_id)
        self._harness.replace_messages(self._state.messages)
        self._harness.config.model = self._state.model or self._config.model
        self._thinking_level = _state_thinking_level(self._state, self._config.thinking_level)
        self._sync_thinking_level_to_active_model()
        self._refresh_runtime_provider()
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(self._config.session_id, model=self.model)
        suffix = " with branch summary" if summary_entry is not None else ""
        return f"Branched session at {target_id}{suffix}."

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
        entries = await self._config.storage.read_all()
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
        return estimate_context_usage(
            system=self._harness.config.system,
            messages=self._harness.messages,
            tools=tuple(self._harness.config.tools),
        )

    @property
    def auto_compact_token_threshold(self) -> int | None:
        """Return the configured automatic compaction threshold, if any."""
        return self._auto_compact_token_threshold

    @property
    def command_registry(self) -> CommandRegistry:
        """Return the slash-command registry used by this session."""
        return self._command_registry

    @property
    def resource_diagnostics(self) -> tuple[ResourceDiagnostic, ...]:
        """Return non-fatal resource discovery diagnostics."""
        return self._resource_diagnostics

    @property
    def session_id(self) -> str | None:
        """Return this session's manager id, if indexed."""
        return self._config.session_id

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
        return tuple(message.content for message in self._harness.queued_messages.steering)

    @property
    def queued_follow_up_messages(self) -> tuple[str, ...]:
        """Return queued follow-up message text for UI display."""
        return tuple(message.content for message in self._harness.queued_messages.follow_up)

    @property
    def last_diagnostic_log_path(self) -> Path | None:
        """Return the last diagnostic log path written by this session."""
        return self._last_diagnostic_log_path

    def cancel(self) -> None:
        """Cancel the currently running agent turn, if any."""
        self._harness.cancel()

    def queue_update_event(self) -> QueueUpdateEvent:
        """Return the current queue state as an agent event."""
        return self._harness.queue_update_event()

    def clear_queued_messages(self) -> QueuedMessages:
        """Clear queued steering and follow-up messages."""
        return self._harness.clear_queues()

    def pop_latest_follow_up_message(self) -> str | None:
        """Remove and return the most recently queued follow-up message."""
        message = self._harness.pop_latest_follow_up()
        return None if message is None else message.content

    def set_model(self, model: str) -> None:
        """Switch the active model for future turns in this process."""
        self._harness.config.model = model
        self._sync_thinking_level_to_active_model()
        self._refresh_runtime_provider()
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(self._config.session_id, model=model)

    def set_model_choice(self, choice: ModelChoice) -> None:
        """Switch provider/model as one operation."""
        if choice.provider_name != self.provider_name:
            self.set_provider(choice.provider_name)
        self.set_model(choice.model)

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

        existing = list(self._provider_settings.scoped_models)
        target = ScopedModelConfig(provider=choice.provider_name, model=choice.model)
        if target in existing:
            existing = [item for item in existing if item != target]
        else:
            existing.append(target)
        self._provider_settings = replace(self._provider_settings, scoped_models=tuple(existing))
        save_provider_settings(self._provider_settings, self._resource_paths.paths)
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

    def set_provider(self, provider_name: str) -> None:
        """Switch the active provider and reset to that provider's default model."""
        if self._provider_settings is None:
            raise ProviderConfigError("Provider settings are not available for this session")

        provider_config = self._provider_settings.get_provider(provider_name)
        model = provider_config.default_model
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
        self._harness.config.model = model
        self._thinking_level = thinking_level
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(self._config.session_id, model=model)

    async def set_thinking_level(self, level: str) -> str:
        """Persist and activate a thinking mode for future turns."""
        normalized = normalize_thinking_level(level)
        available = self.available_thinking_levels
        if not available:
            raise ValueError(
                f"Thinking controls are unavailable for {self._provider_name}:{self.model}"
            )
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
        await self._config.storage.append(entry)
        leaf = LeafEntry(parent_id=entry.id, entry_id=entry.id)
        await self._config.storage.append(leaf)
        self._last_parent_id = entry.id

        entries = await self._config.storage.read_all()
        self._state = SessionState.from_entries(entries, leaf_id=entry.id)
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(self._config.session_id, model=self.model)
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
        )

    def _refresh_runtime_provider(self) -> None:
        if self._runtime_provider_config is None:
            return
        provider_config = self._active_provider_config() or self._runtime_provider_config
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

    def reload(self) -> None:
        """Reload Tau-owned resources and provider settings for future turns."""
        resources = _load_session_resources(self._resource_paths, self._config.context_files)
        self._skills = resources.skills
        self._prompt_templates = resources.prompt_templates
        self._context_files = resources.context_files
        self._resource_diagnostics = resources.diagnostics
        if self._provider_settings is not None:
            self._provider_settings = load_provider_settings()
            self._sync_thinking_level_to_active_model()
            self._refresh_runtime_provider()
        if self._config.system is None:
            self._harness.config.system = build_system_prompt(
                BuildSystemPromptOptions(
                    cwd=self._config.cwd,
                    tools=self._harness.config.tools,
                    skills=self._skills,
                    custom_prompt=self._config.custom_system_prompt,
                    append_system_prompt=self._config.append_system_prompt,
                    context_files=self._context_files,
                )
            )

    async def resume(self, session_id: str) -> str:
        """Replace this session's active state with another indexed session."""
        manager = self._config.session_manager
        if manager is None:
            raise ValueError("Session manager is not available")
        record = manager.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")

        replacement = await type(self).load(
            CodingSessionConfig(
                provider=self._harness.config.provider,
                model=record.model or self.model,
                cwd=record.cwd,
                storage=jsonl_session_storage(record.path),
                system=self._config.system,
                custom_system_prompt=self._config.custom_system_prompt,
                append_system_prompt=self._config.append_system_prompt,
                context_files=self._config.context_files,
                resource_paths=self._config.resource_paths,
                session_id=record.id,
                session_manager=manager,
                command_registry=self._command_registry,
                provider_name=self._provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=self._runtime_provider_config,
                auto_compact_token_threshold=self._auto_compact_token_threshold,
                thinking_level=self._thinking_level,
            )
        )
        self._config = replacement._config
        self._state = replacement._state
        self._harness = replacement._harness
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
        self._thinking_level = replacement._thinking_level
        return f"Resumed session: {record.id}"

    async def new_session(self) -> str:
        """Replace this session's active state with a newly indexed session."""
        manager = self._config.session_manager
        if manager is None:
            raise ValueError("Session manager is not available")

        record = manager.create_session(cwd=self.cwd, model=self.model)
        replacement = await type(self).load(
            replace(
                self._config,
                provider=self._harness.config.provider,
                model=record.model or self.model,
                cwd=record.cwd,
                storage=jsonl_session_storage(record.path),
                session_id=record.id,
                provider_name=self._provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=self._runtime_provider_config,
                thinking_level=self._thinking_level,
            )
        )
        self._config = replacement._config
        self._state = replacement._state
        self._harness = replacement._harness
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
        self._thinking_level = replacement._thinking_level
        return f"Started new session: {record.id}"

    async def compact(self, summary: str) -> str:
        """Append a manual compaction summary and rebuild active context."""
        normalized_summary = summary.strip()
        if not normalized_summary:
            raise ValueError("Compaction summary cannot be empty")
        compaction = await self._append_compaction(normalized_summary)
        return f"Compacted {len(compaction.replaces_entry_ids)} context entries."

    async def aclose(self) -> None:
        """Close runtime providers created by this coding session."""
        for provider in self._owned_providers:
            await provider.aclose()
        self._owned_providers.clear()

    def handle_command(self, text: str) -> CommandResult:
        """Handle minimal coding-session slash commands.

        This is intentionally tiny. Later phases can replace it with a full Pi-like
        command registry without changing the persistence boundary.
        """
        return self._command_registry.execute(self, text)

    def expand_prompt_text(self, text: str) -> str:
        """Expand prompt text using loaded markdown resources."""
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

        bash_tool = create_bash_tool(cwd=self.cwd)
        result = await bash_tool.execute({"command": normalized_command})
        exit_code = None
        if result.data is not None:
            raw_exit_code = result.data.get("exit_code")
            exit_code = raw_exit_code if isinstance(raw_exit_code, int) else None

        if add_to_context:
            before_count = len(self._harness.messages)
            self._harness.append_message(
                UserMessage(
                    content=_terminal_command_context_message(
                        normalized_command,
                        result.content,
                    )
                )
            )
            await self._persist_new_messages(before_count)

        return TerminalCommandResult(
            command=normalized_command,
            output=result.content,
            exit_code=exit_code,
            ok=result.ok,
            added_to_context=add_to_context,
        )

    async def prompt(
        self,
        content: str,
        *,
        streaming_behavior: StreamingBehavior | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Append a user prompt, run the agent, and persist new messages."""
        context = self._diagnostic_context()
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
                yield self._harness.steer(expanded_content)
                return
            if streaming_behavior == "follow_up":
                yield self._harness.follow_up(expanded_content)
                return
            raise RuntimeError(
                "CodingSession is already running; pass streaming_behavior to queue a message."
            )

        try:
            await self._maybe_auto_compact()
        except Exception as exc:
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="auto_compact",
                exc=exc,
            )
            raise
        before_count = len(self._harness.messages)
        try:
            async for event in self._harness.prompt(expanded_content):
                if isinstance(event, ErrorEvent) and not event.recoverable:
                    self._last_diagnostic_log_path = self._diagnostic_logger.log_error_event(
                        context=context,
                        phase="agent_loop",
                        event=event,
                    )
                yield event
            await self._persist_new_messages(before_count)
        except Exception as exc:
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="agent_loop",
                exc=exc,
            )
            raise

    async def continue_(self) -> AsyncIterator[AgentEvent]:
        """Continue the agent from restored state and persist new messages."""
        context = self._diagnostic_context()
        before_count = len(self._harness.messages)
        try:
            async for event in self._harness.continue_():
                if isinstance(event, ErrorEvent) and not event.recoverable:
                    self._last_diagnostic_log_path = self._diagnostic_logger.log_error_event(
                        context=context,
                        phase="agent_loop",
                        event=event,
                    )
                yield event
            await self._persist_new_messages(before_count)
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

    async def _persist_new_messages(self, before_count: int) -> None:
        new_messages = self._harness.messages[before_count:]
        last_message_entry_id: str | None = None
        for message in new_messages:
            entry = MessageEntry(parent_id=self._last_parent_id, message=message)
            await self._config.storage.append(entry)
            self._last_parent_id = entry.id
            last_message_entry_id = entry.id

        if last_message_entry_id is not None:
            leaf = LeafEntry(parent_id=last_message_entry_id, entry_id=last_message_entry_id)
            await self._config.storage.append(leaf)

        entries = await self._config.storage.read_all()
        self._state = SessionState.from_entries(entries)
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(self._config.session_id, model=self.model)

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

    async def _maybe_auto_compact(self) -> None:
        threshold = self._auto_compact_token_threshold
        if threshold is None or threshold <= 0:
            return
        if len(self._state.context_entry_ids) < 2:
            return
        if self.context_token_estimate <= threshold:
            return
        summary = summarize_messages_for_compaction(self._state.messages)
        await self._append_compaction(summary)

    async def _append_compaction(self, summary: str) -> CompactionEntry:
        if not self._state.context_entry_ids:
            raise ValueError("No active context messages to compact")

        compaction = CompactionEntry(
            parent_id=self._last_parent_id,
            summary=summary,
            replaces_entry_ids=list(self._state.context_entry_ids),
        )
        await self._config.storage.append(compaction)
        leaf = LeafEntry(parent_id=compaction.id, entry_id=compaction.id)
        await self._config.storage.append(leaf)
        self._last_parent_id = compaction.id

        entries = await self._config.storage.read_all()
        self._state = SessionState.from_entries(entries, leaf_id=compaction.id)
        self._harness.replace_messages(self._state.messages)
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(self._config.session_id, model=self.model)
        return compaction


def _last_parent_id_from_state(state: SessionState) -> str | None:
    if state.active_leaf_id is not None:
        return state.active_leaf_id
    if state.entries:
        return state.entries[-1].id
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

    def append_descendants(parent_id: str | None) -> None:
        children = children_by_parent.get(parent_id, [])
        for child in children:
            if child.id not in seen:
                ordered.append(child)
                seen.add(child.id)
        for child in children:
            append_descendants(child.id)

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
            if isinstance(message, AssistantMessage) and message.tool_calls and not message.content:
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
    content = message.content
    if isinstance(content, str):
        return _short_preview(content)
    return _short_preview(str(content))


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
        entry.message
        for entry in active_path[target_index + 1 :]
        if entry.type == "message"
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


def _state_thinking_level(
    state: SessionState,
    default: ThinkingLevel,
) -> ThinkingLevel:
    thinking_level = getattr(state, "thinking_level", None)
    if thinking_level is None:
        return default
    return normalize_thinking_level(thinking_level)


def _coerced_thinking_level(
    provider: ProviderConfig,
    *,
    model: str,
    current: ThinkingLevel,
) -> ThinkingLevel:
    levels = provider_thinking_levels(provider, model=model)
    if not levels or current in levels:
        return current
    default = provider_default_thinking_level(provider, model=model)
    return default or levels[0]


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


def _load_session_resources(
    resource_paths: TauResourcePaths,
    explicit_context_files: tuple[ProjectContextFile, ...],
) -> SessionResources:
    loaded_skills, skill_diagnostics = load_skills_with_diagnostics(resource_paths)
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


def default_session_path(cwd: Path) -> Path:
    """Return Tau's default user-home session path for a project cwd."""
    return TauPaths().default_session_path(cwd)


def jsonl_session_storage(path: str | Path) -> JsonlSessionStorage:
    """Convenience factory for local JSONL coding-session storage."""
    return JsonlSessionStorage(path)
