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
    MessageEndEvent,
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
from tau_ai.events import ProviderErrorEvent, ProviderResponseEndEvent, ProviderTextDeltaEvent
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
from tau_coding.paths import TauPaths
from tau_coding.prompt_templates import (
    PromptTemplate,
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
    toggle_saved_scoped_model,
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
    ) -> None:
        self._config = config
        self._state = state
        self._harness = harness
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
        pending_initial_entries: tuple[SessionEntry, ...] = ()
        if not entries:
            info = SessionInfoEntry(cwd=str(config.cwd))
            model = ModelChangeEntry(parent_id=info.id, model=config.model)
            thinking = ThinkingLevelChangeEntry(
                parent_id=model.id,
                thinking_level=config.thinking_level,
            )
            entries = [info, model, thinking]
            pending_initial_entries = (info, model, thinking)

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
            pending_initial_entries=pending_initial_entries,
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

    async def branch_to_entry(
        self,
        entry_id: str,
        *,
        summarize: bool = False,
        custom_instructions: str | None = None,
        replace_instructions: bool = False,
    ) -> str:
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

        leaf = LeafEntry(parent_id=target_id, entry_id=target_id)
        await self._append_session_entry(leaf)
        self._last_parent_id = target_id

        await self._refresh_persisted_state(leaf_id=target_id)
        self._harness.replace_messages(self._state.messages)
        self._harness.config.model = self._state.model or self._config.model
        self._thinking_level = _state_thinking_level(self._state, self._config.thinking_level)
        self._sync_thinking_level_to_active_model()
        self._refresh_runtime_provider()
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
        """Return the effective automatic compaction threshold, if any."""
        if not self._auto_compact_enabled:
            return None
        if self._auto_compact_token_threshold is not None:
            return self._auto_compact_token_threshold
        return auto_compaction_threshold_for_context_window(self.context_window_tokens)

    @property
    def context_window_tokens(self) -> int:
        """Return the active model's configured context window, or Tau's fallback."""
        provider = self._active_provider_config()
        if provider is None:
            return DEFAULT_CONTEXT_WINDOW_TOKENS
        return provider.context_windows.get(self.model, DEFAULT_CONTEXT_WINDOW_TOKENS)

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
        """Switch the active model for future turns and make it the default."""
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

    def reload(self) -> CodingReloadSummary:
        """Reload local coding resources and project context for future turns."""
        before_skills = _skill_signatures(self._skills)
        before_prompt_templates = _prompt_template_signatures(self._prompt_templates)
        before_context_files = _context_file_signatures(self._context_files)
        before_diagnostics = _diagnostic_signatures(self._resource_diagnostics)
        before_system_prompt_inputs = _system_prompt_resource_signatures(
            skills=self._skills,
            context_files=self._context_files,
        )

        resources = _load_session_resources(self._resource_paths, self._config.context_files)

        after_skills = _skill_signatures(resources.skills)
        after_prompt_templates = _prompt_template_signatures(resources.prompt_templates)
        after_context_files = _context_file_signatures(resources.context_files)
        after_diagnostics = _diagnostic_signatures(resources.diagnostics)
        after_system_prompt_inputs = _system_prompt_resource_signatures(
            skills=resources.skills,
            context_files=resources.context_files,
        )

        rebuilt_system_prompt: str | None = None
        system_prompt_rebuilt = False
        if (
            self._config.system is None
            and before_system_prompt_inputs != after_system_prompt_inputs
        ):
            rebuilt_system_prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    cwd=self._config.cwd,
                    tools=self._harness.config.tools,
                    skills=resources.skills,
                    custom_prompt=self._config.custom_system_prompt,
                    append_system_prompt=self._config.append_system_prompt,
                    context_files=resources.context_files,
                )
            )
            system_prompt_rebuilt = True

        self._skills = resources.skills
        self._prompt_templates = resources.prompt_templates
        self._context_files = resources.context_files
        self._resource_diagnostics = resources.diagnostics
        if rebuilt_system_prompt is not None:
            self._harness.config.system = rebuilt_system_prompt

        return CodingReloadSummary(
            skills=_category_summary(before_skills, after_skills),
            prompt_templates=_category_summary(
                before_prompt_templates,
                after_prompt_templates,
            ),
            context_files=_category_summary(before_context_files, after_context_files),
            diagnostics=_category_summary(before_diagnostics, after_diagnostics),
            system_prompt_rebuilt=system_prompt_rebuilt,
        )

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
        if record.provider_name:
            if self._provider_settings is None:
                raise ValueError(
                    "Cannot resume session provider without provider settings: "
                    f"{record.provider_name}"
                )
            try:
                runtime_provider_config = self._provider_settings.get_provider(record.provider_name)
            except ProviderConfigError as exc:
                raise ValueError(
                    f"Session provider is not configured: {record.provider_name}"
                ) from exc
            provider_name = runtime_provider_config.name

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
                provider_name=provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=runtime_provider_config,
                auto_compact_token_threshold=self._auto_compact_token_threshold,
                auto_compact_enabled=self._auto_compact_enabled,
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
        self._auto_compact_enabled = replacement._auto_compact_enabled
        self._thinking_level = replacement._thinking_level
        return f"Resumed session: {record.id}"

    async def new_session(self) -> str:
        """Replace this session's active state with a newly indexed session."""
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

        record = manager.create_session(
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
        self._auto_compact_enabled = replacement._auto_compact_enabled
        self._thinking_level = replacement._thinking_level
        return f"Started new session: {record.id}"

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
            await self._persist_messages_since(before_count)

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

        await self._try_auto_compact(context=context, phase="auto_compact_before_prompt")
        persisted_count = len(self._harness.messages)
        overflow_event: ErrorEvent | None = None
        try:
            async for event in self._harness.prompt(expanded_content):
                if isinstance(event, MessageEndEvent):
                    persisted_count = await self._persist_messages_since(persisted_count)
                if isinstance(event, ErrorEvent) and not event.recoverable:
                    self._last_diagnostic_log_path = self._diagnostic_logger.log_error_event(
                        context=context,
                        phase="agent_loop",
                        event=event,
                    )
                    if _is_context_overflow_error(event):
                        overflow_event = event
                yield event
            persisted_count = await self._persist_messages_since(persisted_count)
            if overflow_event is not None:
                compacted = await self._try_overflow_compact(context=context)
                if compacted:
                    retry_persisted_count = len(self._harness.messages)
                    async for retry_event in self._harness.continue_():
                        if isinstance(retry_event, MessageEndEvent):
                            retry_persisted_count = await self._persist_messages_since(
                                retry_persisted_count
                            )
                        if isinstance(retry_event, ErrorEvent) and not retry_event.recoverable:
                            self._last_diagnostic_log_path = (
                                self._diagnostic_logger.log_error_event(
                                    context=context,
                                    phase="agent_loop_retry",
                                    event=retry_event,
                                )
                            )
                        yield retry_event
                    await self._persist_messages_since(retry_persisted_count)
                return
            await self._try_auto_compact(context=context, phase="auto_compact_after_prompt")
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
        persisted_count = len(self._harness.messages)
        try:
            async for event in self._harness.continue_():
                if isinstance(event, MessageEndEvent):
                    persisted_count = await self._persist_messages_since(persisted_count)
                if isinstance(event, ErrorEvent) and not event.recoverable:
                    self._last_diagnostic_log_path = self._diagnostic_logger.log_error_event(
                        context=context,
                        phase="agent_loop",
                        event=event,
                    )
                yield event
            await self._persist_messages_since(persisted_count)
            await self._try_auto_compact(context=context, phase="auto_compact_after_continue")
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

        await self._refresh_persisted_state()
        return persisted_count + len(new_messages)

    async def _refresh_persisted_state(self, *, leaf_id: str | None = None) -> None:
        entries = await self._config.storage.read_all()
        self._state = SessionState.from_entries(entries, leaf_id=leaf_id)
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(
                self._config.session_id,
                model=self.model,
                provider_name=self.provider_name,
            )

    async def _append_session_entry(self, entry: SessionEntry) -> None:
        """Append one durable entry after flushing deferred session metadata."""
        await self._ensure_session_initialized()
        await self._config.storage.append(entry)

    async def _ensure_session_initialized(self) -> None:
        if not self._pending_initial_entries:
            return
        for entry in self._pending_initial_entries:
            await self._config.storage.append(entry)
        self._pending_initial_entries = ()

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
            if isinstance(event, ProviderTextDeltaEvent):
                text_parts.append(event.delta)
            elif isinstance(event, ProviderResponseEndEvent):
                final_text = event.message.content
            elif isinstance(event, ProviderErrorEvent):
                details = f": {event.data}" if event.data is not None else ""
                raise RuntimeError(f"Compaction summarization failed: {event.message}{details}")

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
        if rows[index][1].role != "tool":
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


def _is_context_overflow_error(event: ErrorEvent) -> bool:
    text = event.message
    if event.data is not None:
        text = f"{text} {event.data}"
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


def _unavailable_thinking_message(session: CodingSession) -> str:
    message = f"Thinking controls are unavailable for {session.provider_name}:{session.model}"
    reason = session.thinking_unavailable_reason
    if reason:
        return f"{message}: {reason}"
    return message


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
