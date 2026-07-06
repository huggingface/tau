import asyncio
import re
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import pytest
from rich.console import Console
from rich.panel import Panel
from textual.color import Color
from textual.containers import VerticalScroll
from textual.geometry import Offset
from textual.selection import SELECT_ALL, Selection
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static, TextArea
from textual.widgets import Markdown as TextualMarkdown
from textual.widgets.markdown import MarkdownStream

from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    AgentToolResult,
    AssistantMessage,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    QueueUpdateEvent,
    ThinkingDeltaEvent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolResultMessage,
    UserMessage,
)
from tau_coding.catalog_loader import user_catalog_path
from tau_coding.commands import CommandResult
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.paths import TauPaths
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.provider_config import (
    OpenAICodexProviderConfig,
    OpenAICompatibleProviderConfig,
    ProviderSettings,
    ScopedModelConfig,
    save_provider_settings,
)
from tau_coding.session import (
    ModelChoice,
    SessionTreeBranchResult,
    SessionTreeChoice,
    TerminalCommandResult,
)
from tau_coding.session_manager import CodingSessionRecord
from tau_coding.skills import Skill, format_skill_invocation
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.tools import create_coding_tools
from tau_coding.tui import app as tui_app
from tau_coding.tui.app import (
    COMPLETION_MAX_VISIBLE_LINES,
    CommandOutputScreen,
    CustomProviderLoginResult,
    CustomProviderLoginScreen,
    LoginMethodPickerScreen,
    LoginProviderPickerScreen,
    LoginScreen,
    ModelPickerScreen,
    OAuthLoginScreen,
    PromptInput,
    SessionPickerScreen,
    TauTuiApp,
    ThemePickerScreen,
    TreePickerScreen,
    _activity_prompt_border_color,
    _completion_selected_render_line,
    _terminal_command_prefix_span,
    _theme_css_variables,
    _visible_completion_state,
)
from tau_coding.tui.autocomplete import CompletionItem, CompletionState
from tau_coding.tui.config import (
    HIGH_CONTRAST_THEME,
    TAU_DARK_THEME,
    TAU_LIGHT_THEME,
    TuiKeybindings,
    TuiSettings,
    tui_settings_path,
)
from tau_coding.tui.state import ChatItem
from tau_coding.tui.widgets import (
    LeftAlignedMarkdownHeading,
    StreamingTranscriptMessageWidget,
    TauMarkdownBlock,
    ThemedMarkdownWidget,
    TranscriptMessageWidget,
    TranscriptView,
    _compact_token_count,
    _split_rich_style_colors,
    _syntax_language,
    _transcript_plain_body_text,
    render_chat_item,
    render_compact_session_info,
    render_session_sidebar,
    transcript_item_selection_text,
)

ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text)


def _style_color_escape(color: str) -> str:
    stripped = color.lstrip("#")
    red, green, blue = (int(stripped[index : index + 2], 16) for index in (0, 2, 4))
    return f"38;2;{red};{green};{blue}"


def _style_rgb(color: str) -> str:
    stripped = color.lstrip("#")
    red, green, blue = (int(stripped[index : index + 2], 16) for index in (0, 2, 4))
    return f"rgb({red},{green},{blue})"


class FakeSessionState:
    thinking_level = "medium"


class FakeSession:
    def __init__(self, messages=(), events=()) -> None:
        self.messages = tuple(messages)
        self.events = tuple(events)
        self.cwd = Path("/workspace/project")
        self.provider_name = "openai"
        self.model = "fake-model"
        self.available_models = ("fake-model", "other-model")
        self.available_model_choices = (
            ModelChoice(provider_name="openai", model="fake-model"),
            ModelChoice(provider_name="openai", model="other-model"),
            ModelChoice(provider_name="local", model="local-model"),
        )
        self.scoped_model_choices: tuple[ModelChoice, ...] = ()
        self.available_providers = ("openai",)
        self.tools = tuple(create_coding_tools(cwd=self.cwd))
        self.skills = (Skill(name="review", path=self.cwd / "review.md", content="Review code"),)
        self.prompt_templates = ()
        self.context_files = (
            ProjectContextFile(path=str(self.cwd / "AGENTS.md"), content="Follow rules."),
        )
        self.context_token_estimate = 12034
        self.auto_compact_token_threshold = 200000
        self.context_window_tokens = 216384
        self.thinking_level = "medium"
        self.available_thinking_levels = ("off", "minimal", "low", "medium", "high", "xhigh")
        self.state = FakeSessionState()
        self.resource_diagnostics = ()
        self.system_prompt = "You are Tau."
        self.session_manager = None
        self._session_title: str | None = None
        self.compact_summaries: list[str] = []
        self.resumed_session_ids: list[str] = []
        self.tree_branch_requests: list[tuple[str, bool, str | None]] = []
        self.new_session_count = 0
        self.prompt_texts: list[str] = []
        self.reload_count = 0
        self.provider_reload_count = 0
        self.queued_steering_messages: tuple[str, ...] = ()
        self.queued_follow_up_messages: tuple[str, ...] = ()
        self.streaming_behaviors: list[str | None] = []
        self.terminal_commands: list[tuple[str, bool]] = []
        self.cancel_count = 0
        self.export_calls: list[tuple[Path | None, str | None]] = []

    @property
    def session_title(self) -> str | None:
        return self._session_title

    def handle_command(self, text: str) -> CommandResult:
        if text == "/session":
            return CommandResult(
                handled=True,
                message="Session info",
            )
        if text == "/reload":
            self.reload_count += 1
            self.skills = (
                Skill(
                    name="reloaded",
                    path=self.cwd / "reloaded.md",
                    content="Reloaded skill",
                ),
            )
            return CommandResult(
                handled=True,
                message="Reloaded local coding resources and project context.",
            )
        if text == "/system":
            return CommandResult(handled=True, message=self.system_prompt)
        if text == "/new":
            return CommandResult(handled=True, new_session_requested=True)
        if text == "/compact":
            return CommandResult(handled=True, compact_summary="")
        if text.startswith("/compact "):
            return CommandResult(handled=True, compact_summary=text.removeprefix("/compact "))
        if text == "/export":
            return CommandResult(handled=True, export_requested=True)
        if text.startswith("/export "):
            return CommandResult(
                handled=True,
                export_requested=True,
                export_destination=Path("out.jsonl"),
                export_format="jsonl",
            )
        if text.startswith("/resume "):
            return CommandResult(handled=True, resume_session_id=text.removeprefix("/resume "))
        if text == "/resume":
            return CommandResult(handled=True, resume_picker_requested=True)
        if text == "/tree":
            return CommandResult(handled=True, tree_picker_requested=True)
        if text == "/login":
            return CommandResult(handled=True, login_picker_requested=True)
        if text in {"/login custom", "/login new", "/login add"}:
            return CommandResult(handled=True, custom_provider_login_requested=True)
        if text.startswith("/login "):
            return CommandResult(handled=True, login_provider=text.removeprefix("/login "))
        if text == "/logout":
            return CommandResult(handled=True, logout_picker_requested=True)
        if text.startswith("/logout "):
            return CommandResult(handled=True, logout_provider=text.removeprefix("/logout "))
        if text == "/model":
            return CommandResult(handled=True, model_picker_requested=True)
        if text in {"/scoped-models", "/scoped models"}:
            return CommandResult(handled=True, scoped_models_picker_requested=True)
        if text.startswith("/thinking "):
            return CommandResult(handled=True, thinking_level=text.removeprefix("/thinking "))
        if text == "/theme":
            return CommandResult(handled=True, theme_picker_requested=True)
        if text.startswith("/theme "):
            return CommandResult(handled=True, theme=text.removeprefix("/theme "))
        if text.startswith("/name "):
            self._session_title = text.removeprefix("/name ")
            return CommandResult(
                handled=True,
                message=f"Session renamed: {self._session_title}",
            )
        return CommandResult(handled=False)

    def set_model(self, model: str) -> None:
        self.model = model

    def set_model_choice(self, choice: ModelChoice) -> None:
        self.set_provider(choice.provider_name)
        self.set_model(choice.model)

    def toggle_scoped_model(self, choice: ModelChoice) -> tuple[ModelChoice, ...]:
        scoped = list(self.scoped_model_choices)
        if choice in scoped:
            scoped.remove(choice)
        else:
            scoped.append(choice)
        self.scoped_model_choices = tuple(scoped)
        return self.scoped_model_choices

    def cycle_scoped_model(self) -> ModelChoice:
        if not self.scoped_model_choices:
            raise ValueError("No scoped models configured.")
        current = ModelChoice(provider_name=self.provider_name, model=self.model)
        try:
            index = self.scoped_model_choices.index(current)
        except ValueError:
            index = -1
        choice = self.scoped_model_choices[(index + 1) % len(self.scoped_model_choices)]
        self.set_model_choice(choice)
        return choice

    def set_provider(self, provider_name: str) -> None:
        self.provider_name = provider_name
        if provider_name == "local":
            self.available_models = ("local-model",)

    def reload(self) -> None:
        self.reload_count += 1

    def reload_provider_settings(self) -> None:
        self.provider_reload_count += 1

    async def set_thinking_level(self, level: str) -> str:
        self.thinking_level = level
        self.state.thinking_level = level
        return f"Thinking mode: {level}"

    async def cycle_thinking_level(self) -> str:
        levels = self.available_thinking_levels
        current_index = levels.index(self.thinking_level)
        self.thinking_level = levels[(current_index + 1) % len(levels)]
        self.state.thinking_level = self.thinking_level
        return f"Thinking mode: {self.thinking_level}"

    async def compact(self, summary: str) -> str:
        self.compact_summaries.append(summary)
        self.messages = (UserMessage(content="Previous conversation summary:\nGenerated summary"),)
        self.context_token_estimate = 42
        return "Compacted 2 context entries."

    async def export(self, destination: Path | None = None, *, format: str | None = None) -> Path:
        self.export_calls.append((destination, format))
        return self.cwd / "session.html"

    async def resume(self, session_id: str) -> str:
        self.resumed_session_ids.append(session_id)
        self.messages = (UserMessage(content="Restored prompt"),)
        self.context_token_estimate = 456
        return f"Resumed session: {session_id}"

    async def tree_choices(self) -> tuple[SessionTreeChoice, ...]:
        return (
            SessionTreeChoice(entry_id="root", label="user: Root"),
            SessionTreeChoice(entry_id="tool", label="tool call: read", is_tool_call=True),
            SessionTreeChoice(entry_id="left", label="assistant: Left"),
            SessionTreeChoice(entry_id="right", label="assistant: Right", active=True),
        )

    async def branch_to_entry(
        self,
        entry_id: str,
        *,
        summarize: bool = False,
        custom_instructions: str | None = None,
    ) -> str:
        self.tree_branch_requests.append((entry_id, summarize, custom_instructions))
        self.messages = (UserMessage(content=f"Branched to {entry_id}"),)
        return f"Branched session at {entry_id}."

    async def new_session(self) -> str:
        self.new_session_count += 1
        self.messages = ()
        self.context_token_estimate = 0
        return "Started new session: new-session"

    def cancel(self) -> None:
        self.cancel_count += 1

    def queue_update_event(self) -> QueueUpdateEvent:
        return QueueUpdateEvent(
            steering=self.queued_steering_messages,
            follow_up=self.queued_follow_up_messages,
        )

    async def run_terminal_command(
        self,
        command: str,
        *,
        add_to_context: bool,
    ) -> TerminalCommandResult:
        self.terminal_commands.append((command, add_to_context))
        return TerminalCommandResult(
            command=command,
            output="command output",
            exit_code=0,
            ok=True,
            added_to_context=add_to_context,
        )

    def pop_latest_follow_up_message(self) -> str | None:
        if not self.queued_follow_up_messages:
            return None
        message = self.queued_follow_up_messages[-1]
        self.queued_follow_up_messages = self.queued_follow_up_messages[:-1]
        return message

    async def prompt(
        self,
        text: str,
        *,
        streaming_behavior: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self.prompt_texts.append(text)
        self.streaming_behaviors.append(streaming_behavior)
        if streaming_behavior == "steer":
            self.queued_steering_messages = (*self.queued_steering_messages, text)
            yield self.queue_update_event()
            return
        if streaming_behavior == "follow_up":
            self.queued_follow_up_messages = (*self.queued_follow_up_messages, text)
            yield self.queue_update_event()
            return
        for event in self.events:
            yield event


def _visible_footer_bindings(app: TauTuiApp) -> dict[str, str]:
    """Return visible bindings that Textual's built-in Footer will render."""
    return {
        binding.description: binding.key_display or binding.key
        for _, binding, _enabled, _tooltip in app.screen.active_bindings.values()
        if binding.show
    }


def test_session_sidebar_renders_session_metadata() -> None:
    console = Console(record=True, width=80)

    console.print(render_session_sidebar(FakeSession()))

    output = console.export_text()
    assert "████████" not in output
    assert "τ = 2π" in output
    assert "session" in output
    assert "context" in output
    assert "AGENTS.md" in output
    assert "12k" not in output
    assert "provider" in output
    assert "openai" in output
    assert "fake-model" in output
    assert "thinking" in output
    assert "medium" in output
    assert "location" not in output
    assert "branch" not in output
    assert "tools" in output
    assert "read" in output
    assert "skills" in output
    assert "review" in output


def test_session_sidebar_uses_accented_aligned_headers_without_section_borders() -> None:
    console = Console(record=True, width=80)
    sidebar = render_session_sidebar(FakeSession())
    panels = [renderable for renderable in sidebar.renderables if isinstance(renderable, Panel)]
    session_section = sidebar.renderables[1]
    header = session_section.renderables[0]

    console.print(sidebar)

    output = console.export_text()
    assert panels == []
    assert header.left == 1
    assert str(header.renderable.style) == f"bold {TAU_DARK_THEME.accent}"
    assert " session" in output
    assert " context" in output
    assert "─" in output
    assert "┌" not in output
    assert "│" not in output


def test_session_sidebar_lists_multiple_context_files() -> None:
    session = FakeSession()
    session.context_files = (
        ProjectContextFile(path=str(session.cwd / "AGENTS.md"), content="Root rules."),
        ProjectContextFile(
            path=str(session.cwd / ".agents" / "AGENTS.md"),
            content="Agent rules.",
        ),
        ProjectContextFile(path="docs/AGENTS.md", content="Docs rules."),
    )
    console = Console(record=True, width=100)

    console.print(render_session_sidebar(session))

    output = console.export_text()
    assert "AGENTS.md" in output
    assert ".agents/AGENTS.md" in output
    assert "docs/AGENTS.md" in output


def test_compact_session_info_renders_sidebar_facts() -> None:
    console = Console(record=True, width=120)

    console.print(render_compact_session_info(FakeSession()))

    output = console.export_text()
    assert "/workspace/project (--)" in output
    assert "12k/200k context" in output
    assert "openai:fake-model" in output
    assert "(medium)" in output


def test_compact_token_count_uses_thousands_suffix() -> None:
    assert _compact_token_count(0) == "0k"
    assert _compact_token_count(499) == "<1k"
    assert _compact_token_count(12034) == "12k"
    assert _compact_token_count(12500) == "13k"


def test_compact_session_info_wraps_to_available_width() -> None:
    console = Console(record=True, width=36)

    console.print(render_compact_session_info(FakeSession()))

    lines = console.export_text().splitlines()
    assert len(lines) > 1
    assert max(len(line) for line in lines) <= 36


def test_chat_items_render_as_unlabeled_blocks() -> None:
    console = Console(record=True, width=40)

    console.print(render_chat_item(ChatItem(role="user", text="Read the file")))
    output = console.export_text()

    assert "Read the file" in output
    assert "you:" not in output
    assert "assistant:" not in output
    assert "tool:" not in output
    assert "▌ Read the file" in output


def test_chat_items_use_left_accent_instead_of_box_border() -> None:
    console = Console(record=True, width=40)

    console.print(render_chat_item(ChatItem(role="assistant", text="Done.")))
    output = console.export_text()

    assert "▌ Done." in output
    assert "┌" not in output
    assert "└" not in output


def test_chat_items_have_bottom_padding() -> None:
    console = Console(record=True, width=40)

    console.print(render_chat_item(ChatItem(role="user", text="Read the file")))
    output = console.export_text().splitlines()

    assert output[-1].strip() == ""


def test_chat_items_fold_long_unbroken_text_to_console_width() -> None:
    console = Console(record=True, width=36)
    long_text = "supercalifragilisticexpialidocious" * 2

    console.print(render_chat_item(ChatItem(role="assistant", text=long_text)))
    output = console.export_text()

    assert max(len(line) for line in output.splitlines()) <= 36


def test_chat_items_use_configured_theme_accent() -> None:
    console = Console(record=True, width=40)

    console.print(
        render_chat_item(
            ChatItem(role="assistant", text="Done."),
            theme=HIGH_CONTRAST_THEME,
        )
    )
    output = console.export_text(styles=True)

    assert "Done." in output
    assert "38;2;0;255;102" in output


def test_chat_items_render_fenced_code_without_markers() -> None:
    console = Console(record=True, width=60)
    item = ChatItem(
        role="assistant",
        text='Here is code:\n\n```python\nprint("hi")\n```',
    )

    console.print(render_chat_item(item))
    output = console.export_text()

    assert 'print("hi")' in output
    assert "```" not in output
    assert "python" not in output


def test_assistant_chat_items_apply_syntax_highlighting_to_code_fences() -> None:
    console = Console(record=True, width=80, color_system="truecolor")
    item = ChatItem(role="assistant", text="```python\ndef hi():\n    return 1\n```")

    console.print(render_chat_item(item))
    output = console.export_text(styles=True)

    assert "def" in output
    assert "return" in output
    assert "\x1b[94;48;2;22;27;33mdef" in output
    assert "\x1b[94;48;2;22;27;33mreturn" in output


def test_chat_items_fallback_unknown_fenced_language_to_plain_code() -> None:
    assert _syntax_language("definitely-not-a-lexer") == "text"

    console = Console(record=True, width=60)
    item = ChatItem(role="assistant", text="```definitely-not-a-lexer\nvalue\n```")

    console.print(render_chat_item(item))
    output = console.export_text()

    assert "value" in output
    assert "```" not in output
    assert "definitely-not-a-lexer" not in output


def test_tool_chat_items_hide_and_show_result_text() -> None:
    item = ChatItem(
        role="tool",
        text="→ read README.md",
        tool_result_text="✓ read\nfull file contents",
    )

    collapsed_console = Console(record=True, width=80)
    collapsed_console.print(render_chat_item(item))
    collapsed = collapsed_console.export_text()

    expanded_console = Console(record=True, width=80)
    expanded_console.print(render_chat_item(item, show_tool_results=True))
    expanded = expanded_console.export_text()

    assert "→ read" in collapsed
    assert "full file contents" not in collapsed
    assert "→ read" in expanded
    assert "full file contents" in expanded


EDIT_TOOL_RESULT_WITH_PATCH = (
    "✓ edit\n"
    "Successfully replaced 1 block.\n"
    "\n"
    "Patch:\n"
    "--- a/README.md\n"
    "+++ b/README.md\n"
    "@@\n"
    "-old\n"
    "+new"
)


def test_expanded_edit_tool_result_renders_patch_as_colored_diff() -> None:
    item = ChatItem(
        role="tool",
        text="→ edit README.md",
        tool_result_text=EDIT_TOOL_RESULT_WITH_PATCH,
    )

    console = Console(record=True, width=100, color_system="truecolor")
    console.print(render_chat_item(item, show_tool_results=True))

    plain = console.export_text(clear=False)
    styled = console.export_text(styles=True)
    assert "Patch:" in plain
    assert "-old" in plain
    assert "+new" in plain
    assert re.search(r"\x1b\[91;[^m]*m-old", styled)
    assert re.search(r"\x1b\[92;[^m]*m\+new", styled)


def test_transcript_plain_tool_body_renders_patch_as_colored_diff() -> None:
    item = ChatItem(
        role="tool",
        text="→ edit README.md",
        tool_result_text=EDIT_TOOL_RESULT_WITH_PATCH,
    )
    body = _transcript_plain_body_text(
        item,
        text=transcript_item_selection_text(item, show_tool_results=True),
        body_style="#cbd5e1 on #000000",
        theme=TAU_DARK_THEME,
    )

    console = Console(record=True, width=100, color_system="truecolor")
    console.print(body)

    plain = console.export_text(clear=False)
    styled = console.export_text(styles=True)
    assert "Patch:" in plain
    assert "-old" in plain
    assert "+new" in plain
    assert re.search(r"\x1b\[91;[^m]*m-old", styled)
    assert re.search(r"\x1b\[92;[^m]*m\+new", styled)


def test_thinking_chat_items_use_distinct_style_and_markdown() -> None:
    console = Console(record=True, width=80)

    console.print(render_chat_item(ChatItem(role="thinking", text="**Plan**\n\nHidden reasoning")))

    output = console.export_text(styles=True)
    plain = console.export_text()
    assert "Plan" in output
    assert "**Plan**" not in plain
    assert "Hidden reasoning" in output
    assert "38;2;156;163;175" in output


def test_skill_chat_items_use_distinct_compact_style() -> None:
    console = Console(record=True, width=80)

    console.print(render_chat_item(ChatItem(role="skill", text="Using skill: review")))

    output = console.export_text(styles=True)
    assert "Using skill: review" in output
    assert "38;2;229;212;239" in output


def test_skill_chat_items_expand_with_tool_results_toggle() -> None:
    item = ChatItem(
        role="skill",
        text="Loading skill: review",
        tool_result_text="✓ read\n# Review\nFull noisy instructions.",
    )
    collapsed_console = Console(record=True, width=80)
    collapsed_console.print(render_chat_item(item, show_tool_results=False))
    collapsed = collapsed_console.export_text()
    expanded_console = Console(record=True, width=80)
    expanded_console.print(render_chat_item(item, show_tool_results=True))
    expanded = expanded_console.export_text()

    assert "Loading skill: review" in collapsed
    assert "Full noisy instructions" not in collapsed
    assert "Loading skill: review" in expanded
    assert "Full noisy instructions" in expanded


def test_branch_summary_chat_items_expand_with_tool_results_toggle() -> None:
    item = ChatItem(
        role="branch_summary",
        text="Branch summary (Ctrl+O to expand)",
        tool_result_text="Detailed summary text",
    )
    collapsed_console = Console(record=True, width=80)
    collapsed_console.print(render_chat_item(item, show_tool_results=False))
    collapsed = collapsed_console.export_text()
    expanded_console = Console(record=True, width=80)
    expanded_console.print(render_chat_item(item, show_tool_results=True))
    expanded = expanded_console.export_text()

    assert "Branch summary (Ctrl+O to expand)" in collapsed
    assert "Detailed summary text" not in collapsed
    assert "Branch Summary" in expanded
    assert "Detailed summary text" in expanded


def test_compaction_summary_chat_items_expand_with_tool_results_toggle() -> None:
    item = ChatItem(
        role="compaction_summary",
        text="Compaction summary (Ctrl+O to expand)",
        tool_result_text="Detailed compaction text",
    )
    collapsed_console = Console(record=True, width=80)
    collapsed_console.print(render_chat_item(item, show_tool_results=False))
    collapsed = collapsed_console.export_text()
    expanded_console = Console(record=True, width=80)
    expanded_console.print(render_chat_item(item, show_tool_results=True))
    expanded = expanded_console.export_text()

    assert "Compaction summary (Ctrl+O to expand)" in collapsed
    assert "Detailed compaction text" not in collapsed
    assert "Compaction Summary" in expanded
    assert "Detailed compaction text" in expanded


def test_tui_state_compacts_branch_summary_messages() -> None:
    state = tui_app.TuiState()

    state.load_messages(
        [
            UserMessage(
                content=(
                    "The following is a summary of a branch that this conversation "
                    "came back from:\n<summary>\nImportant context.\n</summary>"
                )
            )
        ]
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        ("branch_summary", "Branch summary (Ctrl+O to expand)", "Important context.")
    ]


def test_tui_state_compacts_compaction_summary_messages() -> None:
    state = tui_app.TuiState()

    state.load_messages(
        [UserMessage(content="Previous conversation summary:\nCompacted prior work.")]
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        (
            "compaction_summary",
            "Compaction summary (Ctrl+O to expand)",
            "Compacted prior work.",
        )
    ]


def test_tui_state_compacts_expanded_skill_messages() -> None:
    skill = Skill(
        name="review",
        path=Path("/workspace/.tau/skills/review.md"),
        content="# Review\nFull noisy instructions.",
        description="Review code",
    )
    state = tui_app.TuiState()

    state.load_messages(
        [
            UserMessage(
                content=format_skill_invocation(
                    skill,
                    "check the auth flow",
                )
            )
        ]
    )

    assert [(item.role, item.text) for item in state.items] == [
        ("skill", "Using skill: review"),
        ("user", "check the auth flow"),
    ]


def test_tui_state_renders_restored_skill_file_reads_with_skill_style() -> None:
    skill = Skill(
        name="review",
        path=Path("/workspace/.tau/skills/review.md"),
        content="# Review\nFull noisy instructions.",
        description="Review code",
    )
    state = tui_app.TuiState(skills=(skill,))

    state.load_messages(
        [
            AssistantMessage(
                content="Reading skill.",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="read",
                        arguments={"path": "/workspace/.tau/skills/review.md"},
                    )
                ],
            ),
            ToolResultMessage(
                tool_call_id="call-1",
                name="read",
                ok=True,
                content="# Review\nFull noisy instructions.",
            ),
        ]
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        ("assistant", "Reading skill.", None),
        ("skill", "Loading skill: review", "✓ read\n# Review\nFull noisy instructions."),
    ]


def test_light_theme_tool_success_uses_dark_text_without_background() -> None:
    console = Console(record=True, width=80)
    console.print(
        render_chat_item(
            ChatItem(role="tool", text="→ read README.md", tool_result_text="✓ read\ncontents"),
            theme=TAU_LIGHT_THEME,
            show_tool_results=True,
        )
    )

    output = console.export_text(styles=True)

    assert "38;2;22;101;52" in output
    assert "38;2;22;101;52;48;2" not in output


def test_light_theme_tool_error_uses_red_text_without_background() -> None:
    console = Console(record=True, width=80)
    console.print(
        render_chat_item(
            ChatItem(role="tool", text="$ false", tool_result_text="✗ bash\nfailed"),
            theme=TAU_LIGHT_THEME,
            show_tool_results=True,
        )
    )

    output = console.export_text(styles=True)

    assert "38;2;185;28;28" in output
    assert "38;2;185;28;28;48;2" not in output


def test_dark_theme_markdown_code_uses_aqua_highlight() -> None:
    console = Console(record=True, width=80)
    console.print(render_chat_item(ChatItem(role="assistant", text="Use `tau` here.")))

    output = console.export_text(styles=True)

    assert "38;2;117;158;149" in output
    assert "38;2;219;148;90" not in output


def test_assistant_markdown_titles_use_highlight_color_and_left_alignment() -> None:
    console = Console(record=True, width=60, color_system="truecolor")
    console.print(render_chat_item(ChatItem(role="assistant", text="# Title\n\n## Header")))

    output = console.export_text(styles=True)
    plain_output = _strip_ansi(output)

    assert _style_color_escape(TAU_DARK_THEME.markdown_heading) in output
    assert "Title" in plain_output
    assert not plain_output.splitlines()[1].startswith(" " * 20)
    assert LeftAlignedMarkdownHeading.LEVEL_ALIGN["h1"] == "left"


def test_dark_theme_markdown_links_use_theme_link_color() -> None:
    console = Console(record=True, width=80, color_system="truecolor")
    console.print(
        render_chat_item(ChatItem(role="assistant", text="Read [docs](https://example.com)."))
    )

    output = console.export_text(styles=True)

    assert "38;2;147;197;253" in output


def test_dark_theme_markdown_bullets_use_theme_bullet_color() -> None:
    console = Console(record=True, width=80, color_system="truecolor")
    console.print(render_chat_item(ChatItem(role="assistant", text="- first\n- second")))

    output = console.export_text(styles=True)

    assert _style_color_escape(TAU_DARK_THEME.markdown_bullet) in output


def test_markdown_tables_use_highlight_color_for_headers() -> None:
    console = Console(record=True, width=80, color_system="truecolor")
    console.print(
        render_chat_item(
            ChatItem(role="assistant", text="| Name | Value |\n| --- | --- |\n| A | B |")
        )
    )

    output = console.export_text(styles=True)

    assert "38;2;219;148;90" in output
    assert "\x1b[36" not in output


@pytest.mark.anyio
async def test_textual_markdown_widget_uses_theme_link_style() -> None:
    app = TauTuiApp(
        FakeSession([AssistantMessage(content="Read [docs](https://example.com).")]),
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        markdown = app.query_one(ThemedMarkdownWidget)

    assert markdown.tau_link_style == TAU_DARK_THEME.markdown_link


def test_textual_markdown_uses_theme_highlight_and_aqua_inline_code() -> None:
    variables = _theme_css_variables(TAU_LIGHT_THEME)

    assert variables["tau-markdown-highlight"] == TAU_LIGHT_THEME.markdown_heading
    assert variables["tau-markdown-table-header"] == TAU_LIGHT_THEME.markdown_table_header
    assert variables["tau-markdown-table-border"] == TAU_LIGHT_THEME.markdown_table_border
    assert variables["tau-markdown-inline-code"] == TAU_LIGHT_THEME.markdown_inline_code
    assert (
        variables["tau-markdown-code-block-background"]
        == TAU_LIGHT_THEME.markdown_code_block_background
    )
    assert variables["tau-markdown-link"] == TAU_LIGHT_THEME.markdown_link
    assert variables["tau-markdown-bullet"] == TAU_LIGHT_THEME.markdown_bullet


def test_light_theme_markdown_code_uses_aqua_without_background() -> None:
    console = Console(record=True, width=80)
    console.print(
        render_chat_item(
            ChatItem(role="assistant", text="Use `tau` here."),
            theme=TAU_LIGHT_THEME,
        )
    )

    output = console.export_text(styles=True)

    assert "38;2;15;118;110" in output
    assert "38;2;15;118;110;48;2" not in output


def test_tool_chat_items_color_status_metadata_not_tool_name_or_results() -> None:
    success_console = Console(record=True, width=80)
    success_console.print(
        render_chat_item(
            ChatItem(role="tool", text="→ read README.md", tool_result_text="✓ read\ncontents"),
            show_tool_results=True,
        )
    )
    success_output = success_console.export_text(styles=True)

    error_console = Console(record=True, width=80)
    error_console.print(
        render_chat_item(
            ChatItem(role="tool", text="$ false", tool_result_text="✗ bash\nfailed"),
            show_tool_results=True,
        )
    )
    error_output = error_console.export_text(styles=True)

    green = "38;2;156;255;177"
    red = "38;2;255;79;79"
    white = "38;2;203;213;225"

    assert green in success_output
    assert f"{white};48;2;0;0;0mread" in success_output
    assert f"{green};48;2;0;0;0mread" not in success_output
    assert f"{green};48;2;0;0;0m✓ read" not in success_output
    assert f"{green};48;2;0;0;0mcontents" not in success_output

    assert red in error_output
    assert f"{white};48;2;0;0;0m✗ bash" in error_output
    assert f"{red};48;2;0;0;0m✗ bash" not in error_output
    assert f"{red};48;2;0;0;0mfailed" not in error_output


def test_assistant_chat_items_render_markdown_lists() -> None:
    console = Console(record=True, width=60)
    item = ChatItem(role="assistant", text="Plan:\n\n- inspect\n- patch")

    console.print(render_chat_item(item))
    output = console.export_text()

    assert "Plan:" in output
    assert "• inspect" in output
    assert "• patch" in output
    assert "- inspect" not in output


def test_assistant_chat_items_render_markdown_tables() -> None:
    console = Console(record=True, width=60)
    item = ChatItem(
        role="assistant",
        text="| File | Status |\n| --- | --- |\n| README.md | updated |",
    )

    console.print(render_chat_item(item))
    output = console.export_text()

    assert "File" in output
    assert "Status" in output
    assert "README.md" in output
    assert "updated" in output
    assert "---" not in output


def test_user_chat_items_keep_markdown_literal() -> None:
    console = Console(record=True, width=60)
    item = ChatItem(role="user", text="- keep this literal")

    console.print(render_chat_item(item))
    output = console.export_text()

    assert "- keep this literal" in output
    assert "• keep this literal" not in output


def test_chat_items_preserve_malformed_fenced_code() -> None:
    console = Console(record=True, width=60)
    item = ChatItem(role="assistant", text='```python\nprint("hi")')

    console.print(render_chat_item(item))
    output = console.export_text()

    assert "```python" in output
    assert 'print("hi")' in output


@pytest.mark.anyio
async def test_transcript_message_widget_extracts_plain_text_selection() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content="alpha beta\ngamma"),
            ]
        )
    )

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        widget = app.query_one(TranscriptMessageWidget)

        assert widget.get_selection(Selection(Offset(6, 0), Offset(10, 0))) == (
            "beta",
            "\n",
        )


@pytest.mark.anyio
async def test_transcript_message_widget_renders_full_height_role_block() -> None:
    plain_text = "alpha beta gamma\nsecond line\nthird line"
    app = TauTuiApp(
        FakeSession(messages=[UserMessage(content=plain_text)]),
        tui_settings=TuiSettings(theme="high-contrast"),
    )

    role_style = HIGH_CONTRAST_THEME.role_styles["user"]
    _, expected_background = _split_rich_style_colors(role_style.body)
    background = Color.parse(expected_background)
    border = Color.parse(role_style.border)

    async with app.run_test(size=(60, 30)) as pilot:
        await pilot.pause()
        widget = app.query_one(TranscriptMessageWidget)
        body = widget.query_one(".transcript-message-body")

        # A multi-line message must occupy more than a single row so the accent
        # and background have to span the full message height.
        assert widget.size.height > 1

        # The container owns the role background and a real left border, so the
        # block is rectangular and the accent spans every wrapped line.
        assert widget.styles.background == background
        assert body.styles.background == background
        edge_type, edge_color = widget.styles.border_left
        assert edge_type != "none"
        assert edge_color == border

        # Selecting the whole message still yields the original plain text.
        assert widget.get_selection(SELECT_ALL) == (plain_text, "\n")


@pytest.mark.anyio
async def test_streaming_transcript_applies_role_foreground() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)

        thinking_fg, _ = _split_rich_style_colors(TAU_DARK_THEME.role_styles["thinking"].body)
        assistant_fg, _ = _split_rich_style_colors(TAU_DARK_THEME.role_styles["assistant"].body)

        # Streamed thinking is dimmed immediately, matching the finalized block
        # instead of shifting color on the next redraw.
        await transcript.append_thinking_delta(
            "reasoning", theme=TAU_DARK_THEME, show_thinking=True
        )
        await pilot.pause()
        thinking = next(
            w for w in app.query(StreamingTranscriptMessageWidget) if w.item.role == "thinking"
        )
        assert thinking.styles.color == Color.parse(thinking_fg)

        await transcript.append_assistant_delta("answer", theme=TAU_DARK_THEME)
        await pilot.pause()
        assistant = next(
            w for w in app.query(StreamingTranscriptMessageWidget) if w.item.role == "assistant"
        )
        assert assistant.styles.color == Color.parse(assistant_fg)


@pytest.mark.anyio
async def test_assistant_message_renders_without_role_block() -> None:
    app = TauTuiApp(
        FakeSession([AssistantMessage(content="line one\nline two")]),
        tui_settings=TuiSettings(theme="high-contrast"),
    )

    async with app.run_test(size=(60, 30)) as pilot:
        await pilot.pause()
        widget = next(w for w in app.query(TranscriptMessageWidget) if w.item.role == "assistant")
        body = widget.query_one(".transcript-message-body")

        # Assistant output flows as plain prose: no left accent and no role
        # background, so it reads the same while streaming and once finalized.
        assert not widget.styles.has_rule("border_left")
        assert widget.styles.background.a == 0
        assert body.styles.background.a == 0


@pytest.mark.anyio
async def test_streaming_transcript_deltas_do_not_force_scroll_end_during_scrollback() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content=f"message {index}\n" + "line\n" * 4) for index in range(12)
            ]
        )
    )

    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)
        transcript.follow_output()
        await pilot.pause()
        transcript.scroll_to(
            y=max(0, transcript.max_scroll_y - 5),
            animate=False,
            immediate=True,
        )
        await pilot.pause()
        forced_scrolls = 0
        original_scroll_end = transcript.scroll_end

        def tracking_scroll_end(*args: object, **kwargs: object) -> None:
            nonlocal forced_scrolls
            forced_scrolls += 1
            original_scroll_end(*args, **kwargs)

        transcript.scroll_end = tracking_scroll_end  # type: ignore[method-assign]

        await transcript.append_assistant_delta("alpha")
        await transcript.append_assistant_delta(" beta")
        await pilot.pause()

    assert forced_scrolls == 0


@pytest.mark.anyio
async def test_streaming_transcript_deltas_follow_when_at_bottom() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content=f"message {index}\n" + "line\n" * 4) for index in range(12)
            ]
        )
    )

    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)
        transcript.follow_output()
        await pilot.pause()
        assert transcript.is_vertical_scroll_end

        await transcript.append_assistant_delta("alpha\n" * 20)
        for _ in range(5):
            await pilot.pause()
            if transcript.is_vertical_scroll_end:
                break

        assert transcript.is_vertical_scroll_end


@pytest.mark.anyio
async def test_streaming_transcript_deltas_preserve_user_scrollback() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content=f"message {index}\n" + "line\n" * 4) for index in range(12)
            ]
        )
    )

    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)
        transcript.follow_output()
        await pilot.pause()
        assert transcript.max_scroll_y > 0

        transcript.scroll_to(
            y=max(0, transcript.max_scroll_y - 5),
            animate=False,
            immediate=True,
        )
        await pilot.pause()
        scrollback_y = transcript.scroll_y
        assert not transcript.is_vertical_scroll_end

        await transcript.append_assistant_delta("alpha\n" * 20)
        await pilot.pause()

        assert transcript.scroll_y == scrollback_y
        assert not transcript.is_vertical_scroll_end


@pytest.mark.anyio
async def test_streaming_transcript_deltas_do_not_apply_stale_follow_scroll() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content=f"message {index}\n" + "line\n" * 4) for index in range(12)
            ]
        )
    )

    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)
        transcript.follow_output()
        await pilot.pause()
        assert transcript.is_vertical_scroll_end

        await transcript.append_assistant_delta("alpha\n" * 20)
        transcript.scroll_to(
            y=max(0, transcript.max_scroll_y - 5),
            animate=False,
            immediate=True,
        )
        scrollback_y = transcript.scroll_y
        assert not transcript.is_vertical_scroll_end

        await pilot.pause()

        assert transcript.scroll_y == scrollback_y
        assert not transcript.is_vertical_scroll_end


@pytest.mark.anyio
async def test_streaming_transcript_fractional_scrollback_after_refollow_stops_following() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content=f"message {index}\n" + "line\n" * 4) for index in range(12)
            ]
        )
    )

    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)
        transcript.follow_output()
        await pilot.pause()
        assert transcript.is_vertical_scroll_end

        transcript.scroll_to(
            y=max(0, transcript.max_scroll_y - 5),
            animate=False,
            immediate=True,
        )
        await pilot.pause()
        assert not transcript.is_vertical_scroll_end

        transcript.scroll_end(animate=False, immediate=True)
        await pilot.pause()
        assert transcript.is_vertical_scroll_end

        transcript.scroll_to(
            y=max(0, transcript.max_scroll_y - 0.6),
            animate=False,
            immediate=True,
        )
        scrollback_y = transcript.scroll_y
        assert not transcript.is_vertical_scroll_end

        await transcript.append_assistant_delta("alpha\n" * 20)
        await pilot.pause()

        assert transcript.scroll_y == scrollback_y
        assert not transcript.is_vertical_scroll_end


@pytest.mark.anyio
async def test_tui_transcript_selects_only_one_message() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content="first message"),
                AssistantMessage(content="second message"),
            ]
        )
    )

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        messages = list(app.query(TranscriptMessageWidget))

        app.screen.selections = {messages[0]: SELECT_ALL}

        assert app.screen.get_selected_text() == "first message"


@pytest.mark.anyio
async def test_tui_transcript_extracts_adjacent_message_selection() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content="first one"),
                AssistantMessage(content="middle message"),
                UserMessage(content="third item"),
            ]
        )
    )

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        messages = list(app.query(TranscriptMessageWidget))

        app.screen.selections = {
            messages[0]: Selection(Offset(6, 0), None),
            messages[1]: SELECT_ALL,
            messages[2]: Selection(None, Offset(5, 0)),
        }

        assert app.screen.get_selected_text() == "one\nmiddle message\nthird"


@pytest.mark.anyio
async def test_tui_auto_copies_selected_text_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = TauTuiApp(
        FakeSession(messages=[UserMessage(content="copy this")]),
        tui_settings=TuiSettings(auto_copy_selection=True),
    )
    copied: list[str] = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        message = app.query_one(TranscriptMessageWidget)
        app.screen.selections = {message: SELECT_ALL}

        await app.on_text_selected()

    assert copied == ["copy this"]


@pytest.mark.anyio
async def test_tui_auto_copy_selection_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = TauTuiApp(
        FakeSession(messages=[UserMessage(content="do not copy")]),
        tui_settings=TuiSettings(auto_copy_selection=False),
    )
    copied: list[str] = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        message = app.query_one(TranscriptMessageWidget)
        app.screen.selections = {message: SELECT_ALL}

        await app.on_text_selected()

    assert copied == []


def test_transcript_selection_text_tracks_tool_result_visibility() -> None:
    item = ChatItem(
        role="tool",
        text="→ read README.md",
        tool_result_text="✓ read\nREADME contents",
    )

    assert transcript_item_selection_text(item, show_tool_results=False) == "→ read README.md"
    assert transcript_item_selection_text(item, show_tool_results=True) == (
        "→ read README.md\n\n✓ read\nREADME contents"
    )


@pytest.mark.anyio
async def test_tool_transcript_uses_native_markdown_without_custom_selection_painting() -> None:
    app = TauTuiApp(FakeSession(messages=[]))
    item = ChatItem(
        role="tool",
        text="→ read README.md",
        tool_result_text="✓ read\nREADME contents",
    )

    async with app.run_test(size=(120, 30)) as pilot:
        transcript = app.query_one("#transcript", TranscriptView)
        widget = await transcript.append_item(item, show_tool_results=True)
        await pilot.pause()

        assert isinstance(widget, TranscriptMessageWidget)
        assert widget.get_selection(SELECT_ALL) == (
            "→ read README.md\n\n✓ read\nREADME contents",
            "\n",
        )
        assert list(widget.query("MarkdownFence")) == []


@pytest.mark.anyio
async def test_tui_message_start_does_not_mount_empty_assistant_message() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)) as pilot:
        await app._apply_streaming_transcript_event(MessageStartEvent())
        await pilot.pause()

        assert list(app.query(StreamingTranscriptMessageWidget)) == []


@pytest.mark.anyio
async def test_tui_streaming_deltas_update_active_message_without_full_refresh() -> None:
    session = FakeSession(
        events=[
            AgentStartEvent(),
            MessageStartEvent(),
            MessageDeltaEvent(delta="alpha "),
            MessageDeltaEvent(delta="beta"),
            MessageEndEvent(message=AssistantMessage(content="alpha beta")),
            AgentEndEvent(),
        ]
    )
    app = TauTuiApp(session)
    stream_replacements: list[str] = []
    stream_writes: list[str] = []
    full_stream_updates: list[str] = []
    original_replace_text = StreamingTranscriptMessageWidget.replace_text
    original_stream_update = StreamingTranscriptMessageWidget.update
    original_stream_write = MarkdownStream.write

    def tracking_stream_update(
        self: StreamingTranscriptMessageWidget,
        markdown: str,
    ) -> object:
        if markdown:
            full_stream_updates.append(markdown)
        return original_stream_update(self, markdown)

    async def tracking_stream_write(self: MarkdownStream, fragment: str) -> None:
        stream_writes.append(fragment)
        await original_stream_write(self, fragment)

    async def tracking_replace_text(
        self: StreamingTranscriptMessageWidget,
        text: str,
    ) -> None:
        stream_replacements.append(text)
        await original_replace_text(self, text)

    StreamingTranscriptMessageWidget.replace_text = tracking_replace_text  # type: ignore[method-assign]
    StreamingTranscriptMessageWidget.update = tracking_stream_update  # type: ignore[method-assign]
    MarkdownStream.write = tracking_stream_write  # type: ignore[method-assign]
    full_refreshes = 0

    original_refresh = app._refresh

    def tracking_refresh() -> None:
        nonlocal full_refreshes
        full_refreshes += 1
        original_refresh()

    app._refresh = tracking_refresh  # type: ignore[method-assign]

    try:
        async with app.run_test(size=(120, 30)) as pilot:
            await app._run_prompt("stream")
            await pilot.pause()

            transcript = app.query_one("#transcript", TranscriptView)
            streamed = app.query_one(StreamingTranscriptMessageWidget)
            transcript_text = "\n".join(line.text for line in transcript.lines)
    finally:
        StreamingTranscriptMessageWidget.replace_text = original_replace_text  # type: ignore[method-assign]
        StreamingTranscriptMessageWidget.update = original_stream_update  # type: ignore[method-assign]
        MarkdownStream.write = original_stream_write  # type: ignore[method-assign]

    assert full_refreshes == 1
    assert stream_writes == ["alpha ", "beta"]
    assert stream_replacements == []
    assert full_stream_updates == []
    assert streamed.selection_text == "alpha beta"
    assert "alpha beta" in transcript_text


@pytest.mark.anyio
async def test_tui_app_mounts_sidebar_and_transcript() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)):
        assert app.query_one("#sidebar") is not None
        transcript = app.query_one("#transcript")
        assert transcript is not None
        assert transcript.min_width == 1
        prompt = app.query_one("#prompt")
        assert isinstance(prompt, TextArea)
        assert prompt.soft_wrap is True


def test_tau_markdown_block_is_not_selectable_until_mounted() -> None:
    markdown = TextualMarkdown("example")
    block = TauMarkdownBlock(
        markdown,
        type("Token", (), {"map": (0, 1), "level": 0, "type": "paragraph_open"})(),
    )

    assert block.allow_select is False


@pytest.mark.anyio
async def test_tau_markdown_block_remains_selectable_after_mount() -> None:
    app = TauTuiApp(
        FakeSession([AssistantMessage(content="Read [docs](https://example.com).")]),
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        block = app.query_one(TauMarkdownBlock)
        assert block.parent is not None
        assert block.allow_select is True


@pytest.mark.anyio
async def test_tui_app_disables_text_selection_while_agent_is_running() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)):
        assert app.ALLOW_SELECT is True

        app.adapter.apply(AgentStartEvent())
        app._refresh_chrome()

        assert app.ALLOW_SELECT is False

        app.adapter.apply(AgentEndEvent())
        app._refresh_chrome()

        assert app.ALLOW_SELECT is True


@pytest.mark.anyio
async def test_prompt_input_does_not_highlight_active_line() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)) as pilot:
        prompt = app.query_one("#prompt", PromptInput)
        prompt.value = "Keep the prompt background uniform."
        await pilot.pause()

        assert prompt.highlight_cursor_line is False


def test_terminal_command_prefix_span_detects_shell_mode_prefix() -> None:
    assert _terminal_command_prefix_span("! pwd") == (0, 1)
    assert _terminal_command_prefix_span("!! pwd") == (0, 2)
    assert _terminal_command_prefix_span("  !! pwd") == (2, 4)
    assert _terminal_command_prefix_span("hello ! pwd") is None


def test_activity_prompt_border_uses_theme_accent_color_in_shell_mode() -> None:
    theme = TAU_LIGHT_THEME

    assert (
        _activity_prompt_border_color(theme, frame=0, running=False, shell_mode=True)
        == theme.accent
    )


@pytest.mark.anyio
async def test_tui_app_highlights_prompt_shell_mode() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)) as pilot:
        prompt = app.query_one("#prompt", PromptInput)
        prompt.value = "!! pwd"
        await pilot.pause()

        assert prompt.has_class("-shell-mode")
        assert (
            _activity_prompt_border_color(
                app.tui_settings.resolved_theme,
                frame=0,
                running=False,
                shell_mode=prompt.has_class("-shell-mode"),
            )
            == app.tui_settings.resolved_theme.accent
        )
        assert prompt.get_line(0).spans[-1].start == 0
        assert prompt.get_line(0).spans[-1].end == 2
        assert str(prompt.get_line(0).spans[-1].style) == app.tui_settings.resolved_theme.accent

        prompt.value = "ask tau"
        await pilot.pause()

        assert not prompt.has_class("-shell-mode")


@pytest.mark.anyio
async def test_tui_app_uses_textual_footer_for_shortcut_hints() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)):
        assert app.query_one(Footer) is not None
        assert len(app.query("#shortcut-hints")) == 0
        assert _visible_footer_bindings(app) == {
            "Quit": "ctrl+d",
            "Clear": "ctrl+c",
            "Commands": "ctrl+k",
            "Submit": "enter",
            "Newline": "shift+enter",
            "Sessions": "ctrl+r",
            "Thinking": "shift+tab",
            "Model": "ctrl+p",
            "Cancel": "escape",
        }


@pytest.mark.anyio
async def test_tui_app_footer_hints_update_for_completions() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)):
        prompt = app.query_one("#prompt")
        prompt.value = "/se"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        assert _visible_footer_bindings(app) == {
            "Choose": "Up/Down",
            "Complete": "Tab/Enter",
            "Close": "escape",
        }


@pytest.mark.anyio
async def test_tui_app_footer_hints_update_while_running() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)):
        app.adapter.apply(AgentStartEvent())
        app._refresh()

        assert _visible_footer_bindings(app) == {
            "Steer": "enter",
            "Follow-up": "alt+enter",
            "Cancel": "escape",
            "Thinking": "ctrl+t",
            "Tools": "ctrl+o",
        }


@pytest.mark.anyio
async def test_tui_app_keeps_textual_footer_on_short_windows() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 18)):
        assert app.query_one(Footer).display is True
        assert len(app.query("#shortcut-hints")) == 0


@pytest.mark.anyio
async def test_tui_prompt_grows_to_six_lines_then_scrolls() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)) as pilot:
        prompt = app.query_one("#prompt", TextArea)
        assert prompt.size.height == 1

        prompt.text = "x" * 500
        await pilot.pause()
        assert prompt.size.height == 6

        prompt.text = "x" * 1000
        await pilot.pause()
        assert prompt.size.height == 6
        assert prompt.max_scroll_y > 0


@pytest.mark.anyio
async def test_tui_sidebar_is_visible_on_medium_windows() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)):
        sidebar = app.query_one("#sidebar")
        compact_info = app.query_one("#compact-session-info")
        assert sidebar.display is True
        assert compact_info.display is True
        assert not app.has_class("-hide-sidebar")


@pytest.mark.anyio
async def test_tui_sidebar_fills_workspace_height() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)):
        workspace = app.query_one("#workspace")
        sidebar = app.query_one("#sidebar")

        assert sidebar.region.height == workspace.region.height
        assert sidebar.outer_size.height == workspace.size.height


@pytest.mark.anyio
async def test_tui_sidebar_hides_on_narrow_windows() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(80, 30)):
        sidebar = app.query_one("#sidebar")
        compact_info = app.query_one("#compact-session-info")
        assert sidebar.display is False
        assert compact_info.display is True
        assert app.has_class("-hide-sidebar")


@pytest.mark.anyio
async def test_tui_sidebar_hides_on_short_windows() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 18)):
        sidebar = app.query_one("#sidebar")
        compact_info = app.query_one("#compact-session-info")
        assert sidebar.display is False
        assert compact_info.display is True
        assert app.has_class("-hide-sidebar")


@pytest.mark.anyio
async def test_tui_sidebar_visibility_updates_on_resize() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(size=(120, 30)) as pilot:
        sidebar = app.query_one("#sidebar")
        compact_info = app.query_one("#compact-session-info")
        assert sidebar.display is True
        assert compact_info.display is True

        await pilot.resize_terminal(width=80, height=30)
        await pilot.pause()
        assert sidebar.display is False
        assert compact_info.display is True

        await pilot.resize_terminal(width=120, height=18)
        await pilot.pause()
        assert sidebar.display is False
        assert compact_info.display is True

        await pilot.resize_terminal(width=120, height=30)
        await pilot.pause()
        assert sidebar.display is True
        assert compact_info.display is True


@pytest.mark.anyio
async def test_tui_transcript_reflows_when_terminal_resizes() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(
                    content=(
                        "Please summarize this very long sentence that should wrap cleanly "
                        "inside the transcript when the terminal becomes narrower."
                    )
                )
            ]
        )
    )

    async with app.run_test(size=(120, 30)) as pilot:
        transcript = app.query_one("#transcript")
        assert transcript.virtual_size.width <= transcript.scrollable_content_region.width

        await pilot.resize_terminal(width=64, height=30)
        await pilot.pause()

        assert transcript.virtual_size.width <= transcript.scrollable_content_region.width
        assert transcript.scroll_offset.x == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "has_horizontal_overflow"),
    [
        ("x = 1", False),
        ("value = '" + ("x" * 140) + "'", True),
    ],
)
async def test_tui_transcript_code_block_scrollbar_matches_overflow(
    code: str,
    has_horizontal_overflow: bool,
) -> None:
    app = TauTuiApp(FakeSession(messages=[AssistantMessage(content=f"```python\n{code}\n```")]))

    async with app.run_test(size=(64, 30)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)
        fence = app.query_one("MarkdownFence")
        assert transcript.styles.overflow_x == "auto"
        assert transcript.styles.scrollbar_size_horizontal == 1
        assert fence.styles.scrollbar_size_horizontal == 1
        assert fence.styles.overflow_x == "auto"
        assert fence.allow_horizontal_scroll is True
        assert (fence.max_scroll_x > 0) is has_horizontal_overflow
        assert fence.show_horizontal_scrollbar is has_horizontal_overflow


@pytest.mark.anyio
async def test_streaming_code_block_hides_horizontal_scrollbar_until_finalized() -> None:
    app = TauTuiApp(FakeSession())
    long_code_line = "value = '" + ("x" * 140) + "'"

    async with app.run_test(size=(64, 30)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)

        await transcript.append_assistant_delta("```python\n" + long_code_line)
        await pilot.pause()

        streaming_fence = app.query_one("MarkdownFence")
        assert streaming_fence.max_scroll_x > 0
        assert streaming_fence.styles.scrollbar_size_horizontal == 0
        assert streaming_fence.show_horizontal_scrollbar is False

        await transcript.finish_assistant_message("```python\n" + long_code_line + "\n```")
        await pilot.pause()

        finalized_fence = app.query_one("MarkdownFence")
        assert finalized_fence.max_scroll_x > 0
        assert finalized_fence.styles.scrollbar_size_horizontal == 1
        assert finalized_fence.show_horizontal_scrollbar is True


def test_tui_app_uses_configured_theme_css_variables() -> None:
    app = TauTuiApp(FakeSession(), tui_settings=TuiSettings(theme="high-contrast"))

    variables = app.get_theme_variable_defaults()

    assert variables["tau-screen-background"] == "#000000"
    assert variables["tau-prompt-background"] == "#1a1a1a"
    assert variables["tau-prompt-border"] == "#00ff66"


def test_tui_app_uses_light_theme_css_variables() -> None:
    app = TauTuiApp(FakeSession(), tui_settings=TuiSettings(theme="tau-light"))

    variables = app.get_theme_variable_defaults()

    assert variables["tau-screen-background"] == "#ffffff"
    assert variables["tau-chrome-background"] == "#f3f4f6"
    assert variables["tau-muted-text"] == "#475569"
    assert variables["tau-prompt-background"] == "#f8fafc"
    assert variables["tau-prompt-border"] == "#2563eb"
    assert variables["footer-background"] == "#f3f4f6"
    assert variables["footer-foreground"] == "#111827"
    assert variables["footer-description-foreground"] == "#111827"
    assert variables["footer-key-foreground"] == "#0f766e"


def test_tau_dark_theme_uses_black_chat_backgrounds() -> None:
    theme = TuiSettings().resolved_theme

    assert theme.screen_background == "#000000"
    assert theme.transcript_background == "#000000"
    assert theme.prompt_background == "#101419"
    assert theme.role_styles["user"].body.endswith("on #000000")
    assert theme.role_styles["assistant"].body.endswith("on #000000")


def test_tau_light_theme_uses_light_chat_backgrounds() -> None:
    theme = TuiSettings(theme="tau-light").resolved_theme

    assert theme.screen_background == "#ffffff"
    assert theme.transcript_background == "#ffffff"
    assert theme.prompt_text == "#111827"
    assert theme.syntax_theme == "ansi_light"
    assert theme.role_styles["user"].body == "#111827"
    assert theme.role_styles["assistant"].body == "#111827"
    assert theme.role_styles["tool"].body == "#1f2937"
    assert theme.role_styles["error"].border == "#b91c1c"


def test_tui_app_loads_restored_messages_into_display_state() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content="Read the file"),
                AssistantMessage(
                    content="I'll inspect it.",
                    tool_calls=[
                        ToolCall(id="call-1", name="edit", arguments={"path": "README.md"})
                    ],
                ),
                ToolResultMessage(
                    tool_call_id="call-1",
                    name="edit",
                    content="Successfully replaced 1 block.",
                    ok=True,
                    data={"patch": "--- README.md\n+++ README.md\n@@\n-old\n+new"},
                ),
            ]
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in app.state.items] == [
        ("user", "Read the file", None),
        ("assistant", "I'll inspect it.", None),
        (
            "tool",
            "→ edit README.md",
            "✓ edit\n"
            "Successfully replaced 1 block.\n"
            "\n"
            "Patch:\n"
            "--- README.md\n"
            "+++ README.md\n"
            "@@\n"
            "-old\n"
            "+new",
        ),
    ]


@pytest.mark.anyio
async def test_tui_app_shows_activity_indicator_while_running() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test():
        prompt = app.query_one("#prompt")
        indicator = app.query_one("#prompt-prefix")

        assert not app.query("#status")
        assert not app.query("#activity-status")
        assert prompt.styles.border.top[1].hex.lower() == "#2d3748"
        assert indicator.render().plain == "τ"

        app.adapter.apply(AgentStartEvent())
        app._refresh()

        assert pytest.approx(tui_app.ACTIVITY_TICK_SECONDS) == 0.15
        assert tui_app.ACTIVITY_COLOR_FADE_STEPS == 24
        assert prompt.styles.border.top[1].hex.lower() == "#2d3748"
        assert indicator.render().plain.startswith("■")

        app._tick_activity()

        assert prompt.styles.border.top[1].hex.lower() == "#2d3748"
        assert indicator.render().plain.splitlines()[1] == "■"

        app.adapter.apply(AgentEndEvent())
        app._refresh()

        assert not app.query("#status")
        assert prompt.styles.border.top[1].hex.lower() == "#2d3748"
        assert indicator.render().plain == "τ"


@pytest.mark.anyio
async def test_tui_app_clears_activity_status_on_error() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test():
        prompt = app.query_one("#prompt")
        indicator = app.query_one("#prompt-prefix")
        app.adapter.apply(AgentStartEvent())
        app._refresh()
        app.adapter.apply(ErrorEvent(message="provider failed", recoverable=False))
        app._refresh()

        assert not app.query("#status")
        assert not app.query("#activity-status")
        assert prompt.styles.border.top[1].hex.lower() == "#2d3748"
        assert indicator.render().plain == "τ"


@pytest.mark.anyio
async def test_tui_app_theme_command_opens_picker_and_persists_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/theme"
        await pilot.press("enter")
        await pilot.pause()

        picker = app.screen
        assert isinstance(picker, ThemePickerScreen)
        assert [str(item.query_one(Label).render()) for item in picker.query(ListItem)] == [
            "✓ tau-dark",
            "  tau-light",
            "  high-contrast",
        ]

        theme_list = picker.query_one("#theme-picker-list", ListView)
        assert theme_list.index == 0
        await pilot.press("down")
        await pilot.pause()
        assert theme_list.index == 1
        await pilot.press("up")
        await pilot.pause()
        assert theme_list.index == 0
        await pilot.press("down", "enter")
        await pilot.pause()

        assert app.tui_settings.theme == "tau-light"
        assert tui_settings_path().read_text(encoding="utf-8").find('"theme": "tau-light"') != -1
        assert app.get_theme_variable_defaults()["tau-screen-background"] == "#ffffff"


@pytest.mark.anyio
async def test_tui_app_theme_command_argument_updates_theme_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/theme tau-light"
        await pilot.press("enter")

        assert app.tui_settings.theme == "tau-light"
        assert tui_settings_path().read_text(encoding="utf-8").find('"theme": "tau-light"') != -1
        assert app.get_theme_variable_defaults()["tau-screen-background"] == "#ffffff"


@pytest.mark.anyio
async def test_tui_app_new_command_starts_new_visible_state() -> None:
    app = TauTuiApp(FakeSession(messages=[UserMessage(content="Earlier")]))
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/new"
        await pilot.press("enter")

        assert app.session.new_session_count == 1
        assert app.state.items == []
        assert notifications == []

        await pilot.press("up")
        await pilot.pause()

        assert prompt.value == ""


@pytest.mark.anyio
async def test_tui_app_compact_command_runs_session_compaction() -> None:
    session = FakeSession(messages=[UserMessage(content="Earlier")])
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/compact Summary of earlier work."
        await pilot.press("enter")

        assert session.compact_summaries == ["Summary of earlier work."]
        assert [(item.role, item.text) for item in app.state.items] == [
            ("compaction_summary", "Compaction summary (Ctrl+O to expand)")
        ]
        assert app.state.items[0].tool_result_text == "Generated summary"


@pytest.mark.anyio
async def test_tui_app_compact_command_accepts_no_instructions() -> None:
    session = FakeSession(messages=[UserMessage(content="Earlier")])
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/compact"
        await pilot.press("enter")

        assert session.compact_summaries == [""]


@pytest.mark.anyio
@pytest.mark.parametrize("blocked_command", ["/new", "/resume abc123"])
async def test_tui_app_blocks_session_commands_while_compacting(blocked_command: str) -> None:
    started = asyncio.Event()
    finish = asyncio.Event()

    class SlowCompactSession(FakeSession):
        async def compact(self, summary: str) -> str:
            self.compact_summaries.append(summary)
            started.set()
            await finish.wait()
            self.messages = (
                UserMessage(content="Previous conversation summary:\nGenerated summary"),
            )
            self.context_token_estimate = 42
            return "Compacted 2 context entries."

    session = SlowCompactSession(messages=[UserMessage(content="Earlier")])
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/compact Summary of earlier work."
        await pilot.press("enter")
        await asyncio.wait_for(started.wait(), timeout=1)

        prompt.value = blocked_command
        await pilot.press("enter")
        await pilot.pause()

        assert session.new_session_count == 0
        assert session.resumed_session_ids == []
        assert prompt.value == blocked_command
        assert notifications == [
            "Compaction is still running. You can keep editing, but wait to submit."
        ]

        finish.set()
        await pilot.pause()

        assert session.compact_summaries == ["Summary of earlier work."]


@pytest.mark.anyio
async def test_tui_app_blocks_compact_command_while_agent_is_running() -> None:
    session = FakeSession()
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.state.running = True
        prompt = app.query_one("#prompt")
        prompt.value = "/compact Summary of earlier work."
        await pilot.press("enter")
        await pilot.pause()

        assert session.compact_summaries == []
        assert prompt.value == "/compact Summary of earlier work."
        assert notifications == [
            "Wait for the current agent turn and queued messages to finish before compacting."
        ]


@pytest.mark.anyio
async def test_tui_app_blocks_compact_command_while_follow_up_is_queued() -> None:
    session = FakeSession()
    session.queued_follow_up_messages = ("after this",)
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app._refresh()
        prompt = app.query_one("#prompt")
        prompt.value = "/compact Summary of earlier work."
        await pilot.press("enter")
        await pilot.pause()

        assert session.compact_summaries == []
        assert prompt.value == "/compact Summary of earlier work."
        assert notifications == [
            "Wait for the current agent turn and queued messages to finish before compacting."
        ]


@pytest.mark.anyio
async def test_tui_app_escape_cancels_active_compaction() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()

    class SlowCompactSession(FakeSession):
        async def compact(self, summary: str) -> str:
            self.compact_summaries.append(summary)
            started.set()
            await finish.wait()
            self.messages = (
                UserMessage(content="Previous conversation summary:\nGenerated summary"),
            )
            self.context_token_estimate = 42
            return "Compacted 2 context entries."

    session = SlowCompactSession(messages=[UserMessage(content="Earlier")])
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/compact Summary of earlier work."
        await pilot.press("enter")
        await asyncio.wait_for(started.wait(), timeout=1)

        await pilot.press("escape")
        await pilot.pause()

        assert app._compaction_worker is None
        assert [(item.role, item.text) for item in app.state.items] == [("user", "Earlier")]
        assert notifications == ["Cancelled compaction."]

        prompt.value = "/new"
        await pilot.press("enter")
        await pilot.pause()

        assert session.new_session_count == 1
        assert session.messages == ()
        assert not any(item.role == "compaction_summary" for item in app.state.items)


@pytest.mark.anyio
async def test_tui_app_export_command_runs_session_export() -> None:
    session = FakeSession(messages=[UserMessage(content="Earlier")])
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/export --format jsonl out.jsonl"
        await pilot.press("enter")

        assert session.export_calls == [(Path("out.jsonl"), "jsonl")]
        assert notifications == ["Exported session to /workspace/project/session.html"]
        assert session.prompt_texts == []


@pytest.mark.anyio
async def test_tui_app_resume_command_reloads_visible_state() -> None:
    session = FakeSession(messages=[UserMessage(content="Earlier")])
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/resume session-1"
        await pilot.press("enter")

        assert session.resumed_session_ids == ["session-1"]
        assert [(item.role, item.text) for item in app.state.items] == [
            ("user", "Restored prompt"),
        ]

        await pilot.press("up")
        await pilot.pause()

        assert prompt.value == "Restored prompt"


@pytest.mark.anyio
async def test_tui_app_resume_command_opens_session_picker() -> None:
    record = CodingSessionRecord(
        id="session-1",
        path=Path("/workspace/project/session-1.jsonl"),
        cwd=Path("/workspace/project"),
        model="fake-model",
        title="Test session",
        created_at=1.0,
        updated_at=2.0,
    )
    session = FakeSession(messages=[UserMessage(content="Earlier")])
    session.session_manager = _FakeSessionManager([record])
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/resume"
        await pilot.press("enter")

        assert isinstance(app.screen, SessionPickerScreen)
        picker_list = app.screen.query_one("#session-picker-list", ListView)
        assert picker_list.index == 0
        assert [(item.role, item.text) for item in app.state.items] == [("user", "Earlier")]


@pytest.mark.anyio
async def test_prompt_arrow_keys_move_between_lines_without_completions() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "first\nsecond"
        prompt.move_cursor((1, 3))

        await pilot.press("up")
        assert prompt.cursor_location == (0, 3)

        await pilot.press("down")
        assert prompt.cursor_location == (1, 3)


@pytest.mark.anyio
async def test_tui_app_submits_multiline_prompt_with_enter() -> None:
    session = FakeSession(
        events=[
            AgentStartEvent(),
            MessageEndEvent(message=UserMessage(content="first\nsecond")),
            AgentEndEvent(),
        ]
    )
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "first"
        prompt.cursor_position = len(prompt.value)
        await pilot.press("shift+enter")
        prompt.value += "second"
        await pilot.press("enter")
        await pilot.pause()

    assert session.prompt_texts == ["first\nsecond"]
    assert prompt.value == ""


@pytest.mark.anyio
async def test_tui_app_completes_custom_prompt_slash_command() -> None:
    session = FakeSession()
    session.prompt_templates = (
        PromptTemplate(
            name="example",
            path=Path("example.md"),
            content="Example prompt.",
            description="Run the example prompt.",
        ),
    )
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/exa"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        await pilot.press("tab")

        assert prompt.value == "/example"


@pytest.mark.anyio
async def test_tui_app_completes_registered_slash_command() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/se"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        await pilot.press("tab")

        assert prompt.value == "/session"


@pytest.mark.anyio
async def test_tui_app_enter_accepts_completion_without_submitting() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/se"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        await pilot.press("enter")

        assert prompt.value == "/session"
        assert app.state.items == []


@pytest.mark.anyio
async def test_tui_app_enter_accepts_arrow_selected_completion() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/s"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()
        await pilot.press("down")
        selected = app._completion_state.selected
        assert selected is not None

        await pilot.press("enter")

        assert prompt.value == selected.replacement
        assert app.state.items == []


@pytest.mark.anyio
async def test_tui_app_accepts_file_reference_completion(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    session = FakeSession()
    session.cwd = tmp_path
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "inspect @main"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        assert [item.display for item in app._completion_state.items] == ["@src/main.py"]
        await pilot.press("tab")

        assert prompt.value == "inspect @src/main.py"


@pytest.mark.anyio
async def test_tui_app_accepts_shell_path_completion(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")

    session = FakeSession()
    session.cwd = tmp_path
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "!cat READ"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        assert [item.display for item in app._completion_state.items] == ["README.md"]
        await pilot.press("tab")

        assert prompt.value == "!cat README.md"


@pytest.mark.anyio
async def test_tui_app_completes_skill_name() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/skill:r"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        await pilot.press("tab")

        assert prompt.value == "/skill:review"


@pytest.mark.anyio
async def test_tui_app_completes_model_argument() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/model fak"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        await pilot.press("tab")

        assert prompt.value == "/model fake-model"


@pytest.mark.anyio
async def test_tui_app_completes_resume_session_argument() -> None:
    session = FakeSession()
    session.session_manager = _FakeSessionManager(
        [
            CodingSessionRecord(
                id="session-1",
                path=Path("/tmp/session-1.jsonl"),
                cwd=Path("/workspace/project"),
                model="fake-model",
                title="Session",
                created_at=1.0,
                updated_at=2.0,
            )
        ]
    )
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/resume sess"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        assert app._completion_state.selected is not None
        assert app._completion_state.selected.description == (
            "Session - fake-model - /workspace/project"
        )

        await pilot.press("tab")

        assert prompt.value == "/resume session-1"


@pytest.mark.anyio
async def test_tui_app_session_picker_resumes_selected_session() -> None:
    session = FakeSession(messages=[UserMessage(content="Earlier")])
    session.session_manager = _FakeSessionManager(
        [
            CodingSessionRecord(
                id="session-1",
                path=Path("/tmp/session-1.jsonl"),
                cwd=Path("/workspace/project"),
                model="fake-model",
                title="Session",
                created_at=1.0,
                updated_at=2.0,
            )
        ]
    )
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        assert isinstance(app.screen, SessionPickerScreen)

        await pilot.press("enter")
        await pilot.pause()

        assert session.resumed_session_ids == ["session-1"]
        assert [(item.role, item.text) for item in app.state.items] == [
            ("user", "Restored prompt"),
        ]


@pytest.mark.anyio
async def test_tui_app_session_picker_shows_human_readable_session_metadata() -> None:
    updated_at = datetime(2026, 6, 19, 14, 30).timestamp()
    session = FakeSession()
    session.session_manager = _FakeSessionManager(
        [
            CodingSessionRecord(
                id="session-1",
                path=Path("/tmp/session-1.jsonl"),
                cwd=Path("/workspace/project"),
                model="fake-model",
                title="Untitled session",
                created_at=1.0,
                updated_at=updated_at,
            ),
            CodingSessionRecord(
                id="session-2",
                path=Path("/tmp/session-2.jsonl"),
                cwd=Path("/workspace/project"),
                model="other-model",
                title="Named work",
                created_at=1.0,
                updated_at=updated_at,
            ),
        ]
    )
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        assert isinstance(app.screen, SessionPickerScreen)
        labels = [
            item.query_one(Label).content
            for item in app.screen.query_one("#session-picker-list", ListView).children
        ]

    assert labels == [
        "2026-06-19 14:30 - fake-model",
        "2026-06-19 14:30 - other-model - Named work",
    ]
    assert "session-1" not in "\n".join(str(label) for label in labels)
    assert "Untitled session" not in "\n".join(str(label) for label in labels)


@pytest.mark.anyio
async def test_tui_app_session_picker_arrow_keys_select_session() -> None:
    session = FakeSession(messages=[UserMessage(content="Earlier")])
    session.session_manager = _FakeSessionManager(
        [
            CodingSessionRecord(
                id="session-1",
                path=Path("/tmp/session-1.jsonl"),
                cwd=Path("/workspace/project"),
                model="fake-model",
                title=None,
                created_at=1.0,
                updated_at=3.0,
            ),
            CodingSessionRecord(
                id="session-2",
                path=Path("/tmp/session-2.jsonl"),
                cwd=Path("/workspace/project"),
                model="other-model",
                title=None,
                created_at=1.0,
                updated_at=2.0,
            ),
        ]
    )
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        assert isinstance(app.screen, SessionPickerScreen)
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert session.resumed_session_ids == ["session-2"]


@pytest.mark.anyio
async def test_tui_app_tree_picker_branches_with_summary() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/tree"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, TreePickerScreen)
        tree_list = app.screen.query_one("#tree-picker-list", ListView)
        assert tree_list.index == 3
        rendered_labels = [item.query_one(Label).render() for item in tree_list.children]
        labels = [str(label) for label in rendered_labels]
        assert labels == [
            "  user: Root",
            "  tool call: read",
            "  assistant: Left",
            "* assistant: Right",
        ]
        assert str(rendered_labels[0].spans[0].style) == _style_rgb(TAU_DARK_THEME.accent)

        await pilot.press("up")
        await pilot.pause()
        assert tree_list.index == 2
        await pilot.press("s")
        await pilot.pause()

        assert session.tree_branch_requests == [("left", True, None)]
        assert [(item.role, item.text) for item in app.state.items] == [
            ("user", "Branched to left"),
        ]

        prompt = app.query_one("#prompt")
        await pilot.press("up")
        await pilot.pause()

        assert prompt.value == "Branched to left"


@pytest.mark.anyio
async def test_tui_app_tree_picker_prefills_selected_user_message() -> None:
    class PrefillSession(FakeSession):
        async def branch_to_entry(
            self,
            entry_id: str,
            *,
            summarize: bool = False,
            custom_instructions: str | None = None,
        ) -> SessionTreeBranchResult:
            self.tree_branch_requests.append((entry_id, summarize, custom_instructions))
            self.messages = ()
            return SessionTreeBranchResult(
                message=f"Branched session before {entry_id}.",
                input_prefill="Root",
            )

    session = PrefillSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", PromptInput)
        prompt.value = "/tree"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, TreePickerScreen)
        await pilot.press("up", "up", "up")
        await pilot.press("enter")
        await pilot.pause()

        assert session.tree_branch_requests == [("root", False, None)]
        assert session.prompt_texts == []
        assert [(item.role, item.text) for item in app.state.items] == []
        assert prompt.value == "Root"
        assert prompt.cursor_location == (0, 4)


@pytest.mark.anyio
async def test_tui_app_tree_summary_clears_transcript_while_summarizing() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()

    class SlowSummarySession(FakeSession):
        async def branch_to_entry(
            self,
            entry_id: str,
            *,
            summarize: bool = False,
            custom_instructions: str | None = None,
        ) -> str:
            self.tree_branch_requests.append((entry_id, summarize, custom_instructions))
            started.set()
            await finish.wait()
            self.messages = (UserMessage(content=f"Branched to {entry_id}"),)
            return f"Branched session at {entry_id}."

    session = SlowSummarySession(messages=[UserMessage(content="Old thread")])
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/tree"
        await pilot.press("enter")
        await pilot.pause()

        await pilot.press("up")
        await pilot.press("s")
        await pilot.pause()
        await started.wait()

        assert [(item.role, item.text) for item in app.state.items] == [
            ("status", "Summarizing branch…"),
        ]

        finish.set()
        await pilot.pause()

        assert session.tree_branch_requests == [("left", True, None)]
        assert [(item.role, item.text) for item in app.state.items] == [
            ("user", "Branched to left"),
        ]


@pytest.mark.anyio
async def test_tui_app_tree_picker_toggles_tool_calls() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/tree"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, TreePickerScreen)
        tree_list = app.screen.query_one("#tree-picker-list", ListView)
        assert tree_list.index == 3

        await pilot.press("ctrl+t")
        await pilot.pause()

        labels = [str(item.query_one(Label).render()) for item in tree_list.children]
        assert labels == [
            "  user: Root",
            "  assistant: Left",
            "* assistant: Right",
        ]
        assert tree_list.index == 2
        assert "tool calls hidden" in str(
            app.screen.query_one("#tree-picker-help", Static).render()
        )

        await pilot.press("ctrl+t")
        await pilot.pause()

        labels = [str(item.query_one(Label).render()) for item in tree_list.children]
        assert labels == [
            "  user: Root",
            "  tool call: read",
            "  assistant: Left",
            "* assistant: Right",
        ]
        assert tree_list.index == 3


@pytest.mark.anyio
def test_completion_selected_render_line_accounts_for_group_headers() -> None:
    state = CompletionState(
        items=(
            CompletionItem(
                display="/session",
                replacement="/session",
                start=0,
                end=2,
                category="Commands",
            ),
            CompletionItem(
                display="/example",
                replacement="/example",
                start=0,
                end=2,
                category="Custom prompts",
            ),
        ),
        selected_index=1,
    )

    assert _completion_selected_render_line(state) == 3


@pytest.mark.anyio
def test_visible_completion_state_keeps_selected_item_in_render_window() -> None:
    items = tuple(
        CompletionItem(
            display=f"/prompt-{index:02d}",
            replacement=f"/prompt-{index:02d}",
            start=0,
            end=1,
            category="Custom prompts",
        )
        for index in range(30)
    )
    state = CompletionState(items=items, selected_index=24)

    visible = _visible_completion_state(state, max_lines=8)

    assert visible.selected is not None
    assert visible.selected.display == "/prompt-24"
    assert visible.selected_index < len(visible.items)
    assert len(visible.items) < len(items)
    assert _completion_selected_render_line(visible) < 8


def test_visible_completion_state_accounts_for_wrapped_descriptions() -> None:
    items = tuple(
        CompletionItem(
            display=f"/prompt-{index:02d}",
            replacement=f"/prompt-{index:02d}",
            start=0,
            end=1,
            description=(
                "This prompt has a long description that wraps across multiple lines "
                "inside the completion table."
            ),
            category="Custom prompts",
        )
        for index in range(12)
    )
    state = CompletionState(items=items, selected_index=8)

    visible = _visible_completion_state(state, max_lines=8, width=48)

    assert visible.selected is not None
    assert visible.selected.display == "/prompt-08"
    assert tui_app._completion_render_line_count(visible, width=48) <= 8
    assert _completion_selected_render_line(visible, width=48) < 7


def test_visible_completion_state_keeps_selected_item_above_bottom_edge() -> None:
    items = tuple(
        CompletionItem(
            display=f"/prompt-{index:02d}",
            replacement=f"/prompt-{index:02d}",
            start=0,
            end=1,
            category="Custom prompts",
        )
        for index in range(30)
    )
    state = CompletionState(items=items, selected_index=15)

    visible = _visible_completion_state(state, max_lines=8)

    assert visible.selected is not None
    assert visible.selected.display == "/prompt-15"
    assert _completion_selected_render_line(visible) < 7


@pytest.mark.anyio
async def test_tui_app_scrolls_completion_selection_into_view() -> None:
    session = FakeSession()
    session.prompt_templates = tuple(
        PromptTemplate(
            name=f"prompt-{index:02d}",
            path=Path(f"prompt-{index:02d}.md"),
            content="Run.",
        )
        for index in range(30)
    )
    app = TauTuiApp(session)

    async with app.run_test(size=(80, 24)) as pilot:
        prompt = app.query_one("#prompt")
        prompt.focus()
        prompt.value = "/"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()
        await pilot.pause()
        autocomplete = app.query_one("#autocomplete", Static)
        visible_line_limit = app._completion_window_line_budget(autocomplete)
        assert visible_line_limit < tui_app.COMPLETION_MAX_VISIBLE_LINES
        visible = tui_app._visible_completion_state(
            app._completion_state,
            max_lines=visible_line_limit,
        )
        assert visible.items[0].display != "/prompt-00"

        for _ in range(35):
            app.action_completion_next()
            await pilot.pause()

        visible = tui_app._visible_completion_state(
            app._completion_state,
            max_lines=visible_line_limit,
        )
        selected = app._completion_state.selected
        assert selected is not None
        assert visible.selected is not None
        assert visible.selected.display == selected.display
        assert visible.items[0].display != "/prompt-00"
        assert _completion_selected_render_line(visible) < visible_line_limit - 1


@pytest.mark.anyio
async def test_tui_app_uses_terminal_space_for_initial_completion_window() -> None:
    session = FakeSession()
    session.prompt_templates = tuple(
        PromptTemplate(
            name=f"prompt-{index:02d}",
            path=Path(f"prompt-{index:02d}.md"),
            content="Run.",
        )
        for index in range(30)
    )
    app = TauTuiApp(session)

    async with app.run_test(size=(80, 18)) as pilot:
        prompt = app.query_one("#prompt")
        prompt.focus()
        prompt.value = "/"
        await pilot.pause()
        autocomplete = app.query_one("#autocomplete", Static)

        initial_line_budget = app._completion_window_line_budget(autocomplete)
        assert initial_line_budget < COMPLETION_MAX_VISIBLE_LINES

        app.action_completion_next()
        await pilot.pause()
        assert app._completion_window_line_budget(autocomplete) == initial_line_budget


@pytest.mark.anyio
async def test_tui_app_keeps_completion_window_height_stable_while_navigating() -> None:
    session = FakeSession()
    session.prompt_templates = tuple(
        PromptTemplate(
            name=f"prompt-{index:02d}",
            path=Path(f"prompt-{index:02d}.md"),
            content="Run.",
        )
        for index in range(30)
    )
    app = TauTuiApp(session)

    async with app.run_test(size=(80, 24)) as pilot:
        prompt = app.query_one("#prompt")
        prompt.focus()
        prompt.value = "/"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()
        await pilot.pause()
        autocomplete = app.query_one("#autocomplete", Static)
        initial_line_budget = app._completion_window_line_budget(autocomplete)

        app.action_completion_next()
        await pilot.pause()

        assert autocomplete.size.height < initial_line_budget
        assert app._completion_window_line_budget(autocomplete) == initial_line_budget


@pytest.mark.anyio
async def test_tui_app_cycles_completion_selection() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test():
        prompt = app.query_one("#prompt")
        prompt.focus()
        prompt.value = "/s"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        first = app._completion_state.selected.display if app._completion_state.selected else None
        prompt.action_scroll_down()
        second = app._completion_state.selected.display if app._completion_state.selected else None

        assert first != second


@pytest.mark.anyio
async def test_tui_app_opens_command_palette_from_keybinding() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        await pilot.press("ctrl+k")

        assert prompt.value == "/"
        assert app._completion_state.items
        assert any(item.display == "/session" for item in app._completion_state.items)
        assert app.query_one("#autocomplete").display is True


def test_tui_model_picker_guides_setup_when_no_provider_is_usable() -> None:
    class UnusableProviderSession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.available_models = ()
            self.available_model_choices = ()

    session = UnusableProviderSession()
    app = TauTuiApp(session)
    notifications: list[tuple[str, str | None]] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        severity = kwargs.get("severity")
        notifications.append((message, severity if isinstance(severity, str) else None))

    app._notify = fake_notify  # type: ignore[method-assign]

    app._open_model_picker()

    assert notifications == [
        ("No configured providers are usable. Run /login to set up a provider.", "warning")
    ]


@pytest.mark.anyio
async def test_tui_app_deduplicates_active_notifications() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(notifications=True) as pilot:
        app._notify("Thinking controls are not available.", severity="warning")
        app._notify("Thinking controls are not available.", severity="warning")
        app._notify("Thinking controls are not available.", severity="error")
        await pilot.pause()

        active_notifications = tuple(app._notifications)

    assert [
        (notification.message, notification.severity) for notification in active_notifications
    ] == [
        ("Thinking controls are not available.", "warning"),
        ("Thinking controls are not available.", "error"),
    ]


@pytest.mark.anyio
async def test_tui_app_notifications_render_literal_markup_text() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test(notifications=True) as pilot:
        app._notify("Error: value [type=extra_forbidden]", severity="error")
        await pilot.pause()

        [notification] = tuple(app._notifications)

    assert notification.message == "Error: value [type=extra_forbidden]"
    assert notification.markup is False


@pytest.mark.anyio
async def test_tui_app_clicking_transcript_refocuses_prompt() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", PromptInput)
        transcript = app.query_one("#transcript", TranscriptView)
        transcript.focus()
        await pilot.pause()
        assert app.screen.focused is transcript

        await pilot.click("#transcript")
        await pilot.pause()

        assert app.screen.focused is prompt


@pytest.mark.anyio
async def test_tui_app_help_uses_modal_instead_of_transcript() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/session"
        await pilot.press("enter")

        assert isinstance(app.screen, CommandOutputScreen)
        assert app.state.items == []
        assert "Session info" in app.screen.message
        scroll = app.screen.query_one("#command-output-scroll", VerticalScroll)
        assert scroll is not None
        assert app.screen.focused is scroll


@pytest.mark.anyio
async def test_tui_app_reload_appends_command_output_to_transcript() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/reload"
        await pilot.press("enter")
        await pilot.pause()

        assert not isinstance(app.screen, CommandOutputScreen)
        assert session.reload_count == 1
        assert app.state.items == [
            ChatItem(
                role="status",
                text="/reload\nReloaded local coding resources and project context.",
            )
        ]
        assert [skill.name for skill in app.state.skills] == ["reloaded"]


@pytest.mark.anyio
async def test_tui_app_system_appends_command_output_to_transcript() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/system"
        await pilot.press("enter")
        await pilot.pause()

        assert not isinstance(app.screen, CommandOutputScreen)
        assert app.state.items == [ChatItem(role="status", text="/system\nYou are Tau.")]


@pytest.mark.anyio
async def test_tui_app_uses_session_name_in_header() -> None:
    session = FakeSession()
    session._session_title = "Customer bugfix"
    app = TauTuiApp(session)

    async with app.run_test():
        assert app.title == "Tau"
        assert app.sub_title == "Customer bugfix"


@pytest.mark.anyio
async def test_tui_app_uses_default_header_for_unnamed_session() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test():
        assert app.title == "Tau"
        assert app.sub_title == "Untitled session"


@pytest.mark.anyio
async def test_tui_app_name_updates_header() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/name Customer bugfix"
        await pilot.press("enter")
        await pilot.pause()

        assert app.sub_title == "Customer bugfix"


@pytest.mark.anyio
async def test_tui_app_name_success_uses_notification_instead_of_modal() -> None:
    app = TauTuiApp(FakeSession())
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/name Customer bugfix"
        await pilot.press("enter")
        await pilot.pause()

        assert notifications == ["Session renamed: Customer bugfix"]
        assert not isinstance(app.screen, CommandOutputScreen)
        assert app.state.items == []


@pytest.mark.anyio
async def test_tui_app_command_modal_arrow_keys_scroll_output() -> None:
    app = TauTuiApp(FakeSession())
    long_message = "\n".join(f"line {index}" for index in range(80))

    async with app.run_test(size=(100, 20)) as pilot:
        app._show_command_message("/long", long_message)
        await pilot.pause()

        assert isinstance(app.screen, CommandOutputScreen)
        scroll = app.screen.query_one("#command-output-scroll", VerticalScroll)
        await pilot.pause()
        assert scroll.max_scroll_y > 0
        assert app.screen.focused is scroll
        assert scroll.scroll_y == 0

        await pilot.press("down")
        await pilot.pause()

        assert scroll.scroll_y > 0


@pytest.mark.anyio
async def test_tui_app_command_modal_renders_literal_markup_text() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        app._show_command_message("/session", "Session [info]\n/session")
        await pilot.pause()

        assert isinstance(app.screen, CommandOutputScreen)
        body = app.screen.query_one("#command-output-body")
        assert str(body.render()) == "Session [info]\n/session"


@pytest.mark.anyio
async def test_tui_app_command_modal_uses_centered_picker_style() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        app._show_command_message("/session", "Session info")
        await pilot.pause()

        assert isinstance(app.screen, CommandOutputScreen)
        command_output = app.screen.query_one("#command-output")
        command_scroll = app.screen.query_one("#command-output-scroll")
        assert app.screen.styles.align == ("center", "middle")
        assert command_output.styles.width.value == 76
        assert command_output.styles.max_width.value == 90
        assert command_output.styles.height.is_auto
        assert command_output.styles.max_height.value == 70
        assert command_scroll.styles.height.is_auto
        assert command_scroll.styles.max_height.value == 18


@pytest.mark.anyio
async def test_tui_app_session_modal_auto_copies_selected_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = TauTuiApp(FakeSession(), tui_settings=TuiSettings(auto_copy_selection=False))
    copied: list[str] = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)

    async with app.run_test() as pilot:
        app._show_command_message("/session", "Session info")
        await pilot.pause()

        assert isinstance(app.screen, CommandOutputScreen)
        body = app.screen.query_one("#command-output-body")
        app.screen.selections = {body: SELECT_ALL}

        await app.on_text_selected()

    assert copied == ["Session info"]


@pytest.mark.anyio
async def test_tui_app_non_session_modal_uses_global_auto_copy_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = TauTuiApp(FakeSession(), tui_settings=TuiSettings(auto_copy_selection=False))
    copied: list[str] = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)

    async with app.run_test() as pilot:
        app._show_command_message("/hotkeys", "Shortcut info")
        await pilot.pause()

        assert isinstance(app.screen, CommandOutputScreen)
        body = app.screen.query_one("#command-output-body")
        app.screen.selections = {body: SELECT_ALL}

        await app.on_text_selected()

    assert copied == []


@pytest.mark.anyio
async def test_tui_app_escape_cancels_running_session_from_prompt() -> None:
    class RunningSession(FakeSession):
        @property
        def is_running(self) -> bool:
            return True

    session = RunningSession()
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.adapter.apply(AgentStartEvent())
        app._refresh()

        await pilot.press("escape")

        assert session.cancel_count == 1
        assert app.state.running is False
        assert notifications == ["Interrupted current operation."]


@pytest.mark.anyio
async def test_tui_app_new_command_cancels_active_run_and_ignores_late_events() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        app.adapter.apply(AgentStartEvent())
        app._refresh()
        old_run_id = app._prompt_run_id
        prompt = app.query_one("#prompt")
        prompt.value = "/new"

        await pilot.press("enter")

        assert session.cancel_count == 1
        assert session.new_session_count == 1
        assert app._prompt_run_id == old_run_id + 1
        assert app.state.items == []
        assert app.state.running is False

        session.events = (MessageEndEvent(message=AssistantMessage(content="late old output")),)
        await app._run_prompt("old prompt", old_run_id)

        assert app.state.items == []


@pytest.mark.anyio
async def test_tui_app_escape_without_running_does_not_append_transcript_status() -> None:
    app = TauTuiApp(FakeSession(messages=[UserMessage(content="Earlier")]))
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.press("escape")

        assert [(item.role, item.text) for item in app.state.items] == [("user", "Earlier")]
        assert notifications == []


@pytest.mark.anyio
async def test_tui_app_uses_configured_command_palette_keybinding() -> None:
    app = TauTuiApp(
        FakeSession(),
        tui_settings=TuiSettings(keybindings=TuiKeybindings(command_palette="ctrl+j")),
    )

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        await pilot.press("ctrl+k")

        assert prompt.value == ""
        assert app._completion_state.items == ()

        await pilot.press("ctrl+j")

        assert prompt.value == "/"
        assert app._completion_state.items
        assert any(item.display == "/session" for item in app._completion_state.items)


@pytest.mark.anyio
async def test_tui_app_quits_from_focused_prompt_with_default_keybinding() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        visible_bindings = [
            binding for binding in prompt._bindings.get_bindings_for_key("ctrl+d") if binding.show
        ]

        assert any(
            binding.action == "quit" and binding.description == "Quit"
            for binding in visible_bindings
        )

        await pilot.press("ctrl+d")
        await pilot.pause()

        assert app._exit is True


@pytest.mark.anyio
async def test_tui_app_uses_configured_completion_keybinding() -> None:
    app = TauTuiApp(
        FakeSession(),
        tui_settings=TuiSettings(keybindings=TuiKeybindings(accept_completion="f2")),
    )

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/se"
        app._completion_state = app._build_completion_state(prompt.value)
        app._refresh_completions()

        await pilot.press("tab")
        assert prompt.value == "/se"

        await pilot.press("f2")
        assert prompt.value == "/session"


@pytest.mark.anyio
async def test_tui_login_saves_provider_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/login openai"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, LoginScreen)

        api_key_input = app.screen.query_one("#login-api-key", Input)
        api_key_input.value = "stored-openai-key"
        await pilot.press("enter")
        await pilot.pause()

    assert session.reload_count == 0
    assert session.provider_reload_count == 1
    assert session.provider_name == "openai"
    assert session.prompt_texts == []
    assert all(item.text != "stored-openai-key" for item in app.state.items)
    assert (tmp_path / ".tau" / "credentials.json").read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_tui_login_openai_codex_saves_oauth_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    credential_future = asyncio.get_running_loop().create_future()

    async def fake_login_openai_codex(**_kwargs: object) -> OAuthCredential:
        return await credential_future

    monkeypatch.setattr(tui_app, "login_openai_codex", fake_login_openai_codex)
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/login openai-codex"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, OAuthLoginScreen)
        credential_future.set_result(
            OAuthCredential(
                access="access-token",
                refresh="refresh-token",
                expires=123456,
                account_id="account-1",
            )
        )
        await pilot.pause()

    assert session.reload_count == 0
    assert session.provider_reload_count == 1
    assert session.provider_name == "openai-codex"
    assert tui_app.load_provider_settings().default_provider == "openai"
    assert all("access-token" not in item.text for item in app.state.items)
    credentials = (tmp_path / ".tau" / "credentials.json").read_text(encoding="utf-8")
    assert '"type": "oauth"' in credentials
    assert "refresh-token" in credentials


@pytest.mark.anyio
async def test_tui_login_custom_provider_writes_catalog_and_preferences(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test():
        app._handle_custom_provider_login_result(
            CustomProviderLoginResult(
                provider_name="nebius",
                display_name="Nebius AI Studio",
                base_url="https://api.studio.nebius.ai/v1/",
                api_key_env="NEBIUS_API_KEY",
                models=("deepseek-ai/DeepSeek-V4-Pro", "Qwen/Qwen3-Coder"),
                default_model="deepseek-ai/DeepSeek-V4-Pro",
                api_key="stored-nebius-key",
            )
        )

    paths = TauPaths(home=tmp_path / ".tau")
    catalog = user_catalog_path(paths).read_text(encoding="utf-8")
    settings = tui_app.load_provider_settings(paths)

    assert 'name = "nebius"' in catalog
    assert 'display_name = "Nebius AI Studio"' in catalog
    assert 'base_url = "https://api.studio.nebius.ai/v1"' in catalog
    assert 'models = ["deepseek-ai/DeepSeek-V4-Pro", "Qwen/Qwen3-Coder"]' in catalog
    assert settings.get_provider("nebius").default_model == "deepseek-ai/DeepSeek-V4-Pro"
    assert FileCredentialStore(tmp_path / ".tau" / "credentials.json").get("nebius") == (
        "stored-nebius-key"
    )
    assert session.provider_reload_count == 1
    assert session.provider_name == "nebius"


@pytest.mark.anyio
async def test_tui_login_custom_provider_opens_from_slash_command() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/login custom"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, CustomProviderLoginScreen)
        assert app.screen.query_one("#custom-provider-name", Input).has_focus


@pytest.mark.anyio
async def test_tui_login_preserves_existing_scoped_models_and_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    tau_home = tmp_path / ".tau"
    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen",),
                default_model="qwen",
            ),
        ),
        scoped_models=(ScopedModelConfig(provider="local", model="qwen"),),
    )
    save_provider_settings(settings)
    session = FakeSession()
    app = TauTuiApp(session)
    entry = tui_app.builtin_provider_entry("openrouter")
    assert entry is not None

    async with app.run_test():
        app._handle_login_result(entry, "stored-openrouter-key")

    saved = tui_app.load_provider_settings()
    assert saved.default_provider == "local"
    assert saved.get_provider("local").default_model == "qwen"
    assert saved.get_provider("openrouter").credential_name == "openrouter"
    assert saved.scoped_models == (ScopedModelConfig(provider="local", model="qwen"),)
    assert FileCredentialStore(tau_home / "credentials.json").get("openrouter") == (
        "stored-openrouter-key"
    )


@pytest.mark.anyio
async def test_tui_login_provider_does_not_change_default_startup_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session = FakeSession()
    app = TauTuiApp(session)
    entry = tui_app.builtin_provider_entry("openrouter")
    assert entry is not None

    async with app.run_test():
        app._handle_login_result(entry, "stored-openrouter-key")

    assert session.provider_reload_count == 1
    assert session.provider_name == "openrouter"
    assert tui_app.load_provider_settings().default_provider == "openai"


@pytest.mark.anyio
async def test_tui_logout_without_stored_credentials_shows_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session = FakeSession()
    app = TauTuiApp(session)
    notifications: list[tuple[str, str | None]] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        severity = kwargs.get("severity")
        notifications.append((message, severity if isinstance(severity, str) else None))

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/logout"
        await pilot.press("enter")
        await pilot.pause()

    assert notifications == [(tui_app.NO_STORED_CREDENTIALS_MESSAGE, "warning")]
    assert session.provider_reload_count == 0


@pytest.mark.anyio
async def test_tui_logout_removes_stored_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    credential_path = tmp_path / ".tau" / "credentials.json"
    FileCredentialStore(credential_path).set("openai", "stored-openai-key")
    session = FakeSession()
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/logout openai"
        await pilot.press("enter")
        await pilot.pause()

    assert FileCredentialStore(credential_path).get("openai") is None
    assert session.provider_reload_count == 1
    assert notifications == [
        "Removed stored API key for OpenAI. "
        "Environment variables and providers.json config are unchanged."
    ]


@pytest.mark.anyio
async def test_tui_logout_removes_oauth_credential(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    credential_path = tmp_path / ".tau" / "credentials.json"
    FileCredentialStore(credential_path).set_oauth(
        "openai-codex",
        OAuthCredential(
            access="access-token",
            refresh="refresh-token",
            expires=123456,
            account_id="account-1",
        ),
    )
    session = FakeSession()
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/logout openai-codex"
        await pilot.press("enter")
        await pilot.pause()

    assert FileCredentialStore(credential_path).get_oauth("openai-codex") is None
    assert session.provider_reload_count == 1
    assert notifications == ["Logged out of OpenAI Codex subscription."]


@pytest.mark.anyio
async def test_tui_logout_opens_stored_credential_provider_picker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    FileCredentialStore(tmp_path / ".tau" / "credentials.json").set("anthropic", "stored-key")
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/logout"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, LoginProviderPickerScreen)
        title = app.screen.query_one("#login-provider-title", Static)
        assert str(title.render()) == "Logout"
        provider_list = app.screen.query_one("#login-provider-list", ListView)
        labels = [str(item.query_one(Label).render()) for item in provider_list.children]
        assert labels == ["Anthropic — anthropic"]


@pytest.mark.anyio
async def test_tui_login_opens_method_picker() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/login"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, LoginMethodPickerScreen)
        method_list = app.screen.query_one("#login-method-list", ListView)
        labels = [str(item.query_one(Label).render()) for item in method_list.children]
        assert labels == [
            "Subscription — OAuth account",
            "API key — built-in provider",
            "Custom provider — OpenAI-compatible",
        ]
        assert app.screen.focused is method_list
        assert method_list.index == 0


@pytest.mark.anyio
async def test_tui_login_method_picker_supports_arrow_keys() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/login"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, LoginMethodPickerScreen)
        method_list = app.screen.query_one("#login-method-list", ListView)
        assert app.screen.focused is method_list
        assert method_list.index == 0

        await pilot.press("down")
        await pilot.pause()
        assert method_list.index == 1

        await pilot.press("up")
        await pilot.pause()
        assert method_list.index == 0

        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, LoginProviderPickerScreen)
        provider_list = app.screen.query_one("#login-provider-list", ListView)
        labels = [str(item.query_one(Label).render()) for item in provider_list.children]
        assert labels[0] == "OpenAI — openai"


@pytest.mark.anyio
async def test_tui_login_subscription_opens_oauth_provider_picker() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/login"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, LoginMethodPickerScreen)
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, LoginProviderPickerScreen)
        provider_list = app.screen.query_one("#login-provider-list", ListView)
        labels = [str(item.query_one(Label).render()) for item in provider_list.children]
        assert labels == ["OpenAI Codex subscription — openai-codex"]
        assert "gpt-5.5" not in "\n".join(labels)


@pytest.mark.anyio
async def test_tui_login_api_key_opens_api_provider_picker() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/login"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, LoginMethodPickerScreen)
        app.screen.action_cursor_down()
        app.screen.action_select_cursor()
        await pilot.pause()

        assert isinstance(app.screen, LoginProviderPickerScreen)
        provider_list = app.screen.query_one("#login-provider-list", ListView)
        labels = [str(item.query_one(Label).render()) for item in provider_list.children]
        assert labels[0] == "OpenAI — openai"
        assert "OpenAI Codex subscription — openai-codex" not in labels

        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, LoginScreen)
        assert app.screen.provider.name == "anthropic"


@pytest.mark.anyio
async def test_tui_model_opens_interactive_picker() -> None:
    session = FakeSession()
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/model"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ModelPickerScreen)
        tabs = app.screen.query_one("#model-picker-tabs", Static)
        assert str(tabs.render()) == "Tabs: ● All models  ○ Scoped models"
        model_list = app.screen.query_one("#model-picker-list", ListView)
        labels = [str(item.query_one(Label).render()) for item in model_list.children]
        assert labels == [
            "* openai:fake-model",
            "  openai:other-model",
            "  local:local-model",
        ]

        search = app.screen.query_one("#model-picker-search", Input)
        assert search.has_focus
        search.value = "local"
        await pilot.pause()

        labels = [str(item.query_one(Label).render()) for item in model_list.children]
        assert labels == ["  local:local-model"]

        await pilot.press("tab")
        await pilot.pause()
        assert str(tabs.render()) == "Tabs: ○ All models  ● Scoped models"

        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert session.provider_name == "local"
    assert session.model == "local-model"
    assert session.prompt_texts == []
    assert notifications == []


@pytest.mark.anyio
async def test_tui_scoped_models_picker_toggles_scoped_models_without_switching_model() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/scoped-models"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ModelPickerScreen)
        tabs = app.screen.query_one("#model-picker-tabs", Static)
        assert str(tabs.render()) == (
            "Scoped models setup — Enter toggles membership; active model is unchanged"
        )
        await pilot.press("enter")
        await pilot.pause()

        assert session.scoped_model_choices == (
            ModelChoice(provider_name="openai", model="fake-model"),
        )
        assert session.provider_name == "openai"
        assert session.model == "fake-model"
        model_list = app.screen.query_one("#model-picker-list", ListView)
        labels = [str(item.query_one(Label).render()) for item in model_list.children]
        assert labels[0] == "* openai:fake-model [scoped]"

        await pilot.press("enter")
        await pilot.pause()

        assert session.scoped_model_choices == ()
        assert session.provider_name == "openai"
        assert session.model == "fake-model"


@pytest.mark.anyio
async def test_tui_app_runs_terminal_command_and_adds_context() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "! pwd"
        await pilot.press("enter")
        await pilot.pause()

    assert session.terminal_commands == [("pwd", True)]
    assert session.prompt_texts == []
    assert [(item.role, item.text, item.tool_result_text) for item in app.state.items] == [
        ("tool", "$ pwd", "✓ bash · added to context\ncommand output")
    ]


@pytest.mark.anyio
async def test_tui_app_runs_terminal_command_without_context() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "!! pwd"
        await pilot.press("enter")
        await pilot.pause()

    assert session.terminal_commands == [("pwd", False)]
    assert session.prompt_texts == []
    assert app.state.items[-1].tool_result_text == "✓ bash · not added to context\ncommand output"
    assert app.state.items[-1].always_show_tool_result is True


@pytest.mark.anyio
@pytest.mark.parametrize("add_to_context", [True, False])
async def test_tui_app_renders_terminal_command_while_running(add_to_context: bool) -> None:
    session = FakeSession()
    app = TauTuiApp(session)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run_terminal_command(
        command: str,
        *,
        add_to_context: bool,
    ) -> TerminalCommandResult:
        started.set()
        await release.wait()
        return TerminalCommandResult(
            command=command,
            output="finished",
            exit_code=0,
            ok=True,
            added_to_context=add_to_context,
        )

    session.run_terminal_command = fake_run_terminal_command  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        task = asyncio.create_task(
            app._run_terminal_command("sleep 1", add_to_context=add_to_context)
        )
        await started.wait()
        await pilot.pause()

        assert [(item.role, item.text, item.tool_result_text) for item in app.state.items] == [
            ("tool", "$ sleep 1", None)
        ]
        assert app.state.items[-1].always_show_tool_result is True

        release.set()
        await task

    context_label = "added to context" if add_to_context else "not added to context"
    assert app.state.items[-1].tool_result_text == f"✓ bash · {context_label}\nfinished"


@pytest.mark.anyio
async def test_tui_app_marks_failed_terminal_command_as_error() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async def fake_run_terminal_command(
        command: str,
        *,
        add_to_context: bool,
    ) -> TerminalCommandResult:
        return TerminalCommandResult(
            command=command,
            output="failed",
            exit_code=2,
            ok=False,
            added_to_context=add_to_context,
        )

    session.run_terminal_command = fake_run_terminal_command  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "!! false"
        await pilot.press("enter")
        await pilot.pause()

    assert session.prompt_texts == []
    assert app.state.items[-1].text == "$ false"
    assert app.state.items[-1].tool_result_text == "✗ bash · not added to context\nfailed"


@pytest.mark.anyio
async def test_tui_app_marks_terminal_command_exception_as_failed() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async def fake_run_terminal_command(
        command: str,
        *,
        add_to_context: bool,
    ) -> TerminalCommandResult:
        del command, add_to_context
        raise RuntimeError("boom")

    session.run_terminal_command = fake_run_terminal_command  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "!! false"
        await pilot.press("enter")
        await pilot.pause()

    assert app.state.items[-1].text == "$ false"
    assert app.state.items[-1].tool_result_text == "✗ bash · not added to context\nboom"


@pytest.mark.anyio
async def test_tui_app_renders_terminal_command_output_when_tool_results_are_collapsed() -> None:
    item = ChatItem(
        role="tool",
        text="$ pwd",
        tool_result_text="✓ bash · not added to context\ncommand output",
        always_show_tool_result=True,
    )

    console = Console(record=True, width=80)
    console.print(render_chat_item(item, show_tool_results=item.always_show_tool_result))

    assert "command output" in console.export_text()


@pytest.mark.anyio
async def test_tui_app_limits_terminal_command_output_preview() -> None:
    session = FakeSession()
    app = TauTuiApp(session)
    output = "\n".join(f"line {index}" for index in range(130))

    async def fake_run_terminal_command(
        command: str,
        *,
        add_to_context: bool,
    ) -> TerminalCommandResult:
        return TerminalCommandResult(
            command=command,
            output=output,
            exit_code=0,
            ok=True,
            added_to_context=add_to_context,
        )

    session.run_terminal_command = fake_run_terminal_command  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "!! seq 130"
        await pilot.press("enter")
        await pilot.pause()

    result_text = app.state.items[-1].tool_result_text
    assert result_text is not None
    assert "line 119" in result_text
    assert "line 120" not in result_text
    assert "10 more lines" in result_text


@pytest.mark.anyio
async def test_tui_app_toggles_tool_results_from_keybinding() -> None:
    app = TauTuiApp(FakeSession())
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        assert app.state.show_tool_results is False
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.state.show_tool_results is True
        await pilot.press("ctrl+o")
        await pilot.pause()

    assert app.state.show_tool_results is False
    assert notifications == ["Tool results expanded.", "Tool results collapsed."]


@pytest.mark.anyio
async def test_tui_app_queues_steering_prompt_while_running() -> None:
    session = FakeSession()
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.state.running = True
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "adjust course\nwith extra detail"

        await pilot.press("enter")
        await pilot.pause()

        queued_messages = app.query_one("#queued-messages")
        assert prompt.text == ""
        assert session.prompt_texts == ["adjust course\nwith extra detail"]
        assert session.streaming_behaviors == ["steer"]
        assert app.state.queued_steering == ("adjust course\nwith extra detail",)
        assert app.state.queued_follow_up == ()
        assert queued_messages.display is True
        rendered_queue = tui_app._render_queued_messages(
            app.state,
            theme=app.tui_settings.resolved_theme,
        )
        rendered_rows = [str(row) for row in rendered_queue.renderables]
        assert "↪ steering · queued: adjust course" in rendered_rows
        assert all("with extra detail" not in row for row in rendered_rows)

    assert notifications == []


@pytest.mark.anyio
async def test_tui_app_queues_follow_up_prompt_from_keybinding() -> None:
    session = FakeSession()
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.state.running = True
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "after this\nwith extra detail"

        await pilot.press("alt+enter")
        await pilot.pause()

        queued_messages = app.query_one("#queued-messages")
        assert prompt.text == ""
        assert session.prompt_texts == ["after this\nwith extra detail"]
        assert session.streaming_behaviors == ["follow_up"]
        assert app.state.queued_steering == ()
        assert app.state.queued_follow_up == ("after this\nwith extra detail",)
        assert queued_messages.display is True
        rendered_queue = tui_app._render_queued_messages(
            app.state,
            theme=app.tui_settings.resolved_theme,
        )
        rendered_rows = [str(row) for row in rendered_queue.renderables]
        assert "↳ follow-up · queued: after this" in rendered_rows
        assert all("with extra detail" not in row for row in rendered_rows)

    assert notifications == []


@pytest.mark.anyio
async def test_tui_app_up_arrow_edits_latest_queued_follow_up() -> None:
    session = FakeSession(messages=[UserMessage(content="remembered prompt")])
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        app.state.running = True
        session.queued_follow_up_messages = ("first follow-up", "latest follow-up")
        app._refresh()

        prompt = app.query_one("#prompt", TextArea)
        prompt.focus()
        prompt.text = ""
        await pilot.press("up")
        await pilot.pause()

        assert prompt.text == "latest follow-up"
        assert session.queued_follow_up_messages == ("first follow-up",)
        assert app.state.queued_follow_up == ("first follow-up",)
        queued_messages = app.query_one("#queued-messages")
        assert queued_messages.display is True


@pytest.mark.anyio
async def test_tui_app_up_arrow_recalls_latest_sent_prompt_when_input_is_empty() -> None:
    session = FakeSession()
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "first prompt"
        await pilot.press("enter")
        await pilot.pause()
        prompt.text = "latest prompt"
        await pilot.press("enter")
        await pilot.pause()

        prompt.text = ""
        await pilot.press("up")
        await pilot.pause()

        assert prompt.text == "latest prompt"
        assert prompt.cursor_location == (0, len("latest prompt"))
        assert session.prompt_texts == ["first prompt", "latest prompt"]


@pytest.mark.anyio
async def test_tui_app_up_arrow_recalls_latest_restored_user_message() -> None:
    app = TauTuiApp(
        FakeSession(
            messages=[
                UserMessage(content="earlier prompt"),
                AssistantMessage(content="response"),
                UserMessage(content="restored prompt"),
            ]
        )
    )

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.focus()
        prompt.text = ""

        await pilot.press("up")
        await pilot.pause()

        assert prompt.text == "restored prompt"
        assert prompt.cursor_location == (0, len("restored prompt"))


@pytest.mark.anyio
async def test_tui_app_up_arrow_preserves_non_empty_prompt_movement() -> None:
    app = TauTuiApp(FakeSession(messages=[UserMessage(content="remembered prompt")]))

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.focus()
        prompt.text = "first line\nsecond line"
        prompt.move_cursor((1, len("second line")))

        await pilot.press("up")
        await pilot.pause()

        assert prompt.text == "first line\nsecond line"
        assert prompt.cursor_location == (0, len("first line"))


@pytest.mark.anyio
async def test_tui_app_toggles_thinking_tokens_from_keybinding_while_running() -> None:
    app = TauTuiApp(FakeSession())
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    def transcript_text() -> str:
        transcript = app.query_one("#transcript", TranscriptView)
        return "\n".join(line.text for line in transcript.lines)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.state.running = True
        app.state.add_thinking_delta("internal plan")
        app.state.add_item("assistant", "final answer")
        app._refresh()
        await pilot.pause()

        assert app.state.show_thinking is False
        assert "final answer" in transcript_text()
        assert "Thinking… Press Ctrl+T to show thinking tokens." in transcript_text()
        assert "internal plan" not in transcript_text()

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.state.show_thinking is True
        assert app.state.running is True
        assert "internal plan" in transcript_text()
        assert "Thinking… Press Ctrl+T to show thinking tokens." not in transcript_text()

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.state.show_thinking is False
        assert "Thinking… Press Ctrl+T to show thinking tokens." in transcript_text()
        assert "internal plan" not in transcript_text()

    assert notifications == []


@pytest.mark.anyio
async def test_tui_app_hidden_thinking_placeholder_stays_before_streamed_answer() -> None:
    session = FakeSession(
        events=[
            AgentStartEvent(),
            ThinkingDeltaEvent(delta="private plan"),
            MessageStartEvent(message_role="assistant"),
            MessageDeltaEvent(delta="public answer"),
            MessageEndEvent(message=AssistantMessage(content="public answer")),
            AgentEndEvent(),
        ]
    )
    app = TauTuiApp(session)

    async with app.run_test() as pilot:
        await app._run_prompt("stream")
        await pilot.pause()

        transcript = app.query_one("#transcript", TranscriptView)
        assert [line.text for line in transcript.lines] == [
            "Thinking… Press Ctrl+T to show thinking tokens.",
            "public answer",
        ]


@pytest.mark.anyio
async def test_tui_app_thinking_toggle_preserves_unrelated_widgets() -> None:
    app = TauTuiApp(FakeSession())

    async with app.run_test() as pilot:
        app.state.add_item("user", "first prompt")
        app.state.add_thinking_delta("plan one")
        app.state.add_item("assistant", "first answer")
        app.state.add_item("user", "second prompt")
        app.state.add_thinking_delta("plan two")
        app.state.add_item("assistant", "second answer")
        app._refresh()
        await pilot.pause()

        transcript = app.query_one("#transcript", TranscriptView)
        stable_widgets = [
            widget
            for widget in transcript.children
            if isinstance(widget, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
            and widget.item.role != "thinking"
        ]
        assert len(stable_widgets) == 4
        assert (
            sum(
                1
                for widget in transcript.children
                if isinstance(widget, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
                and widget.item.role == "thinking"
            )
            == 2
        )

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert [
            widget
            for widget in transcript.children
            if isinstance(widget, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
            and widget.item.role != "thinking"
        ] == stable_widgets
        assert [line.text for line in transcript.lines] == [
            "first prompt",
            "plan one",
            "first answer",
            "second prompt",
            "plan two",
            "second answer",
        ]

        app.state.add_item("status", "late status")
        await transcript.append_item(app.state.items[-1])
        await pilot.pause()
        stable_widgets.append(
            next(
                widget
                for widget in reversed(transcript.children)
                if isinstance(widget, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
                and widget.item.role == "status"
            )
        )

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert [
            widget
            for widget in transcript.children
            if isinstance(widget, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
            and widget.item.role != "thinking"
        ] == stable_widgets
        assert [line.text for line in transcript.lines] == [
            "first prompt",
            "Thinking… Press Ctrl+T to show thinking tokens.",
            "first answer",
            "second prompt",
            "Thinking… Press Ctrl+T to show thinking tokens.",
            "second answer",
            "late status",
        ]


@pytest.mark.anyio
async def test_tui_prompt_ctrl_c_clears_text() -> None:
    app = TauTuiApp(FakeSession(messages=(UserMessage(content="User prompt"),)))

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.focus()
        prompt.text = "discard this prompt"
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()

        assert prompt.text == ""


@pytest.mark.anyio
async def test_tui_app_cycles_thinking_from_keybinding() -> None:
    session = FakeSession()
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.press("shift+tab")
        await pilot.pause()

    assert session.thinking_level == "high"
    assert notifications == []


@pytest.mark.anyio
async def test_tui_app_cycles_thinking_from_keybinding_while_running() -> None:
    session = FakeSession()
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.state.running = True
        await pilot.press("shift+tab")
        await pilot.pause()

    assert session.thinking_level == "high"
    assert notifications == []


@pytest.mark.anyio
async def test_tui_app_cycles_scoped_model_from_keybinding() -> None:
    session = FakeSession()
    session.scoped_model_choices = (
        ModelChoice(provider_name="openai", model="fake-model"),
        ModelChoice(provider_name="openai", model="other-model"),
    )
    app = TauTuiApp(session)
    notifications: list[str] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()

    assert session.provider_name == "openai"
    assert session.model == "other-model"
    assert notifications == []


@pytest.mark.anyio
async def test_tui_app_cycles_scoped_model_without_redrawing_transcript() -> None:
    session = FakeSession(
        messages=[UserMessage(content=f"Earlier prompt {index}") for index in range(120)]
    )
    session.scoped_model_choices = (
        ModelChoice(provider_name="openai", model="fake-model"),
        ModelChoice(provider_name="openai", model="other-model"),
    )
    app = TauTuiApp(session)
    transcript_refreshes = 0

    async with app.run_test() as pilot:
        transcript = app.query_one("#transcript", TranscriptView)

        def fake_update_from_state(*args: object, **kwargs: object) -> None:
            del args, kwargs
            nonlocal transcript_refreshes
            transcript_refreshes += 1

        transcript.update_from_state = fake_update_from_state  # type: ignore[method-assign]
        await pilot.press("ctrl+p")
        await pilot.pause()

    assert session.provider_name == "openai"
    assert session.model == "other-model"
    assert transcript_refreshes == 0


@pytest.mark.anyio
async def test_tui_app_uses_configured_thinking_keybinding() -> None:
    session = FakeSession()
    app = TauTuiApp(
        session,
        tui_settings=TuiSettings(keybindings=TuiKeybindings(thinking_cycle="f3")),
    )

    async with app.run_test() as pilot:
        await pilot.press("shift+tab")
        await pilot.pause()
        assert session.thinking_level == "medium"

        await pilot.press("f3")
        await pilot.pause()

    assert session.thinking_level == "high"


@pytest.mark.anyio
async def test_tui_prompt_worker_refreshes_directly() -> None:
    app = TauTuiApp(FakeSession(events=[AgentStartEvent(), AgentEndEvent()]))
    refreshes = 0

    def fake_refresh() -> None:
        nonlocal refreshes
        refreshes += 1

    app._refresh = fake_refresh  # type: ignore[method-assign]

    await app._run_prompt("hello")

    assert refreshes == 2
    assert app.state.running is False


@pytest.mark.anyio
async def test_tui_prompt_worker_shows_diagnostic_log_path_for_error_event(tmp_path: Path) -> None:
    class ErrorSession(FakeSession):
        def __init__(self) -> None:
            super().__init__(events=[AgentStartEvent(), ErrorEvent(message="provider failed")])
            self.last_diagnostic_log_path = tmp_path / "tau-home" / "logs" / "agent-calls.jsonl"

    session = ErrorSession()
    app = TauTuiApp(session)
    app._refresh = lambda: None  # type: ignore[method-assign]

    await app._run_prompt("break")

    assert app.state.error == f"Error: provider failed\nLog: {session.last_diagnostic_log_path}"
    assert app.state.items[-1].role == "error"
    assert app.state.items[-1].text == app.state.error
    assert app.state.running is False


@pytest.mark.anyio
async def test_tui_prompt_worker_shows_diagnostic_log_path_on_failure(tmp_path: Path) -> None:
    class EmptyMessageError(Exception):
        def __str__(self) -> str:
            return ""

    class FailingSession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.last_diagnostic_log_path = tmp_path / "tau-home" / "logs" / "agent-calls.jsonl"

        async def prompt(self, text: str) -> AsyncIterator[AgentEvent]:
            self.prompt_texts.append(text)
            raise EmptyMessageError()
            yield  # pragma: no cover

    session = FailingSession()
    app = TauTuiApp(session)
    app._refresh = lambda: None  # type: ignore[method-assign]

    await app._run_prompt("break")

    assert app.state.error == (f"Error: EmptyMessageError\nLog: {session.last_diagnostic_log_path}")
    assert app.state.items[-1].role == "error"
    assert app.state.items[-1].text == app.state.error
    assert app.state.running is False


@pytest.mark.anyio
async def test_tui_prompt_worker_refreshes_context_after_message_changes() -> None:
    class ContextChangingSession(FakeSession):
        async def prompt(self, text: str) -> AsyncIterator[AgentEvent]:
            self.prompt_texts.append(text)
            self.context_token_estimate = 10
            yield AgentStartEvent()
            self.context_token_estimate = 20
            yield MessageEndEvent(message=UserMessage(content=text))
            self.context_token_estimate = 30
            yield MessageEndEvent(message=AssistantMessage(content="Using a tool."))
            self.context_token_estimate = 40
            yield ToolExecutionStartEvent(
                tool_call=ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
            )
            yield ToolExecutionEndEvent(
                result=AgentToolResult(
                    tool_call_id="call-1",
                    name="read",
                    ok=True,
                    content="contents",
                )
            )
            self.context_token_estimate = 50
            yield AgentEndEvent()

    session = ContextChangingSession()
    app = TauTuiApp(session)
    observed_context: list[int] = []

    def fake_refresh() -> None:
        observed_context.append(session.context_token_estimate)

    app._refresh = fake_refresh  # type: ignore[method-assign]

    await app._run_prompt("read README")

    assert observed_context == [10, 20, 30, 40, 40, 50]
    assert [(item.role, item.text, item.tool_result_text) for item in app.state.items] == [
        ("user", "read README", None),
        ("assistant", "Using a tool.", None),
        ("tool", "→ read README.md", "✓ read\ncontents"),
    ]


@pytest.mark.anyio
async def test_tui_resume_refreshes_context_after_session_swap() -> None:
    session = FakeSession(messages=[UserMessage(content="Earlier")])
    app = TauTuiApp(session)
    observed_context: list[int] = []
    notifications: list[str] = []

    def fake_refresh() -> None:
        observed_context.append(session.context_token_estimate)

    def fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    app._refresh = fake_refresh  # type: ignore[method-assign]
    app._notify = fake_notify  # type: ignore[method-assign]

    await app._resume_session("session-1")

    assert observed_context == [456]
    assert notifications == ["Resumed session: session-1"]
    assert [(item.role, item.text) for item in app.state.items] == [
        ("user", "Restored prompt"),
    ]


@pytest.mark.anyio
async def test_tui_app_shows_startup_update_notice_in_transcript_only() -> None:
    session = FakeSession(messages=[UserMessage(content="Earlier prompt")])
    app = TauTuiApp(session, startup_notices=("Tau updated to 0.2.0", "Tau 0.2.0 is available"))
    notifications: list[tuple[str, str | None]] = []

    def fake_notify(message: str, **kwargs: object) -> None:
        severity = kwargs.get("severity")
        notifications.append((message, severity if isinstance(severity, str) else None))

    app._notify = fake_notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)
        assert [line.text for line in transcript.lines] == [
            "Tau updated to 0.2.0",
            "Tau 0.2.0 is available",
            "Earlier prompt",
        ]

    assert notifications == []
    assert session.messages == (UserMessage(content="Earlier prompt"),)


@pytest.mark.anyio
async def test_tui_app_runs_initial_prompt() -> None:
    session = FakeSession(
        events=[
            AgentStartEvent(),
            MessageEndEvent(message=UserMessage(content="explain this repo")),
            AgentEndEvent(),
        ]
    )
    app = TauTuiApp(session, initial_prompt="explain this repo")

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

    assert session.prompt_texts == ["explain this repo"]
    assert any(item.role == "user" and item.text == "explain this repo" for item in app.state.items)


@pytest.mark.anyio
async def test_run_tui_app_falls_back_to_first_credentialed_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class FakeCredentialStore:
        def get(self, name: str) -> str | None:
            return "stored-key" if name == "openai" else None

        def get_oauth(self, name: str) -> object | None:
            return None

    record = CodingSessionRecord(
        id="new-session",
        path=tmp_path / "new-session.jsonl",
        cwd=tmp_path,
        model="gpt-5.5",
        title=None,
        created_at=1.0,
        updated_at=1.0,
        provider_name="openai",
    )

    class FakeProvider:
        async def aclose(self) -> None:
            calls.append("provider_closed")

    class FakeManager:
        def prepare_session(
            self,
            *,
            cwd: Path,
            model: str,
            provider_name: str | None = None,
        ) -> CodingSessionRecord:
            calls.append(f"prepare:{cwd}:{model}:{provider_name}")
            return record

        def get_session(self, session_id: str) -> CodingSessionRecord | None:
            return None

    class FakeCodingSession:
        @classmethod
        async def load(cls, config: object) -> str:
            assert config.provider_name == "openai"  # type: ignore[attr-defined]
            calls.append("load")
            return "session"

    class FakeApp:
        def __init__(self, session: str, **kwargs: object) -> None:
            assert session == "session"
            assert kwargs["startup_message"] is None

        async def run_async(self) -> None:
            calls.append("run")

    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                credential_name=None,
                models=("qwen",),
                default_model="qwen",
            ),
            OpenAICompatibleProviderConfig(
                name="openai",
                credential_name="openai",
                models=("gpt-5.5",),
                default_model="gpt-5.5",
            ),
        ),
    )
    monkeypatch.setattr(tui_app, "FileCredentialStore", lambda: FakeCredentialStore())
    monkeypatch.setattr(tui_app, "load_provider_settings", lambda: settings)
    monkeypatch.setattr(tui_app, "load_tui_settings", lambda: TuiSettings())
    monkeypatch.setattr(
        tui_app,
        "create_model_provider",
        lambda provider, **kwargs: (
            calls.append(f"provider:{provider.name}:{kwargs['model']}") or FakeProvider()
        ),
    )
    monkeypatch.setattr(tui_app, "CodingSession", FakeCodingSession)
    monkeypatch.setattr(tui_app, "TauTuiApp", FakeApp)

    await tui_app.run_tui_app(cwd=tmp_path, model=None, session_manager=FakeManager())

    assert calls == [
        "provider:openai:gpt-5.5",
        f"prepare:{tmp_path}:gpt-5.5:openai",
        "load",
        "run",
        "provider_closed",
    ]


@pytest.mark.anyio
async def test_run_tui_app_ignores_latest_directory_provider_model_for_new_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    latest_record = CodingSessionRecord(
        id="latest-session",
        path=tmp_path / "latest-session.jsonl",
        cwd=tmp_path / "other",
        model="gpt-5.5",
        title=None,
        created_at=1.0,
        updated_at=1.0,
        provider_name="openai-codex",
    )
    created_record = CodingSessionRecord(
        id="new-session",
        path=tmp_path / "new-session.jsonl",
        cwd=tmp_path,
        model="gpt-5",
        title=None,
        created_at=2.0,
        updated_at=2.0,
        provider_name="openai",
    )

    class FakeProvider:
        async def aclose(self) -> None:
            calls.append("provider_closed")

    class FakeManager:
        def latest_session_for_cwd(self, cwd: Path) -> CodingSessionRecord | None:
            calls.append(f"latest:{cwd}")
            return latest_record

        def prepare_session(
            self,
            *,
            cwd: Path,
            model: str,
            provider_name: str | None = None,
        ) -> CodingSessionRecord:
            calls.append(f"prepare:{cwd}:{model}:{provider_name}")
            return created_record

        def get_session(self, session_id: str) -> CodingSessionRecord | None:
            return None

    class FakeCodingSession:
        @classmethod
        async def load(cls, config: object) -> str:
            assert config.provider_name == "openai"  # type: ignore[attr-defined]
            assert config.model == "gpt-5"  # type: ignore[attr-defined]
            calls.append("load")
            return "session"

    class FakeApp:
        def __init__(self, session: str, **kwargs: object) -> None:
            assert session == "session"

        async def run_async(self) -> None:
            calls.append("run")

    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
            OpenAICodexProviderConfig(
                name="openai-codex",
                models=("gpt-5.5",),
                default_model="gpt-5.5",
            ),
        ),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "stored-key")
    monkeypatch.setattr(tui_app, "load_provider_settings", lambda: settings)
    monkeypatch.setattr(tui_app, "load_tui_settings", lambda: TuiSettings())
    monkeypatch.setattr(
        tui_app,
        "create_model_provider",
        lambda provider, **kwargs: (
            calls.append(f"provider:{provider.name}:{kwargs['model']}") or FakeProvider()
        ),
    )
    monkeypatch.setattr(tui_app, "CodingSession", FakeCodingSession)
    monkeypatch.setattr(tui_app, "TauTuiApp", FakeApp)
    monkeypatch.setattr(tui_app, "load_tui_settings", lambda: TuiSettings())

    await tui_app.run_tui_app(cwd=tmp_path, model=None, session_manager=FakeManager())

    assert calls == [
        "provider:openai:gpt-5",
        f"prepare:{tmp_path}:gpt-5:openai",
        "load",
        "run",
        "provider_closed",
    ]


@pytest.mark.anyio
async def test_run_tui_app_does_not_start_new_session_from_scoped_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    latest = CodingSessionRecord(
        id="latest-session",
        path=tmp_path / "latest-session.jsonl",
        cwd=tmp_path,
        model="gpt-5.5",
        title=None,
        created_at=1.0,
        updated_at=1.0,
        provider_name="openai",
    )
    record = CodingSessionRecord(
        id="new-session",
        path=tmp_path / "new-session.jsonl",
        cwd=tmp_path,
        model="gpt-5.5",
        title=None,
        created_at=2.0,
        updated_at=2.0,
        provider_name="openai-codex",
    )

    class FakeProvider:
        async def aclose(self) -> None:
            calls.append("provider_closed")

    class FakeCredentialStore:
        def get(self, name: str) -> str | None:
            return None

        def get_oauth(self, name: str) -> object | None:
            return object() if name == "openai-codex" else None

    class FakeManager:
        def latest_session_for_cwd(self, cwd: Path) -> CodingSessionRecord | None:
            calls.append(f"latest:{cwd}")
            return latest

        def prepare_session(
            self,
            *,
            cwd: Path,
            model: str,
            provider_name: str | None = None,
        ) -> CodingSessionRecord:
            calls.append(f"prepare:{cwd}:{model}:{provider_name}")
            return record

        def get_session(self, session_id: str) -> CodingSessionRecord | None:
            return None

    class FakeCodingSession:
        @classmethod
        async def load(cls, config: object) -> str:
            assert config.provider_name == "openai"  # type: ignore[attr-defined]
            calls.append("load")
            return "session"

    class FakeApp:
        def __init__(self, session: str, **kwargs: object) -> None:
            assert session == "session"

        async def run_async(self) -> None:
            calls.append("run")

    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5.5",),
                default_model="gpt-5.5",
            ),
            OpenAICodexProviderConfig(
                name="openai-codex",
                models=("gpt-5.5",),
                default_model="gpt-5.5",
            ),
        ),
        scoped_models=(ScopedModelConfig(provider="openai-codex", model="gpt-5.5"),),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "stored-key")
    monkeypatch.setattr(tui_app, "FileCredentialStore", lambda: FakeCredentialStore())
    monkeypatch.setattr(tui_app, "load_provider_settings", lambda: settings)
    monkeypatch.setattr(tui_app, "load_tui_settings", lambda: TuiSettings())
    monkeypatch.setattr(
        tui_app,
        "create_model_provider",
        lambda provider, **kwargs: (
            calls.append(f"provider:{provider.name}:{kwargs['model']}") or FakeProvider()
        ),
    )
    monkeypatch.setattr(tui_app, "CodingSession", FakeCodingSession)
    monkeypatch.setattr(tui_app, "TauTuiApp", FakeApp)

    await tui_app.run_tui_app(cwd=tmp_path, model=None, session_manager=FakeManager())

    assert calls == [
        "provider:openai:gpt-5.5",
        f"prepare:{tmp_path}:gpt-5.5:openai",
        "load",
        "run",
        "provider_closed",
    ]


@pytest.mark.anyio
async def test_run_tui_app_creates_new_session_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    record = CodingSessionRecord(
        id="new-session",
        path=tmp_path / "new-session.jsonl",
        cwd=tmp_path,
        model="fake-model",
        title=None,
        created_at=1.0,
        updated_at=1.0,
    )

    class FakeProvider:
        async def aclose(self) -> None:
            calls.append("provider_closed")

    class FakeManager:
        def prepare_session(
            self,
            *,
            cwd: Path,
            model: str,
            provider_name: str | None = None,
        ) -> CodingSessionRecord:
            calls.append(f"prepare:{cwd}:{model}:{provider_name}")
            return record

        def get_session(self, session_id: str) -> CodingSessionRecord | None:
            calls.append(f"get:{session_id}")
            return None

        def get_or_create_default_session(self, *, cwd: Path, model: str) -> CodingSessionRecord:
            raise AssertionError("default session should not be opened implicitly")

    class FakeCodingSession:
        @classmethod
        async def load(cls, config: object) -> str:
            assert config.provider_name == "local"  # type: ignore[attr-defined]
            assert config.auto_compact_token_threshold == 1000  # type: ignore[attr-defined]
            assert config.index_on_first_persist is True  # type: ignore[attr-defined]
            calls.append("load")
            return "session"

    class FakeApp:
        def __init__(self, session: str, **kwargs: object) -> None:
            assert session == "session"
            assert isinstance(kwargs["tui_settings"], TuiSettings)
            assert kwargs["initial_prompt"] == "explain this repo"

        async def run_async(self) -> None:
            calls.append("run")

    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("local-model",),
                default_model="local-model",
            ),
        ),
    )
    monkeypatch.setattr(tui_app, "load_provider_settings", lambda: settings)
    monkeypatch.setattr(
        tui_app,
        "create_model_provider",
        lambda provider, **kwargs: FakeProvider(),
    )
    monkeypatch.setattr(tui_app, "CodingSession", FakeCodingSession)
    monkeypatch.setattr(tui_app, "TauTuiApp", FakeApp)
    monkeypatch.setattr(tui_app, "load_tui_settings", lambda: TuiSettings())

    await tui_app.run_tui_app(
        model=None,
        cwd=tmp_path,
        provider_name="local",
        auto_compact_token_threshold=1000,
        initial_prompt="explain this repo",
        session_manager=FakeManager(),
    )

    assert calls == [
        f"prepare:{tmp_path}:local-model:local",
        "get:new-session",
        "load",
        "run",
        "provider_closed",
    ]


@pytest.mark.anyio
async def test_run_tui_app_opens_when_provider_login_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    record = CodingSessionRecord(
        id="new-session",
        path=tmp_path / "new-session.jsonl",
        cwd=tmp_path,
        model="fake-model",
        title=None,
        created_at=1.0,
        updated_at=1.0,
    )

    class FakeManager:
        def prepare_session(
            self,
            *,
            cwd: Path,
            model: str,
            provider_name: str | None = None,
        ) -> CodingSessionRecord:
            calls.append(f"prepare:{cwd}:{model}:{provider_name}")
            return record

        def get_session(self, session_id: str) -> CodingSessionRecord | None:
            return None

    class FakeCodingSession:
        @classmethod
        async def load(cls, config: object) -> str:
            calls.append(f"load:{type(config.provider).__name__}")  # type: ignore[attr-defined]
            return "session"

    class FakeApp:
        def __init__(self, session: str, **kwargs: object) -> None:
            assert session == "session"
            message = str(kwargs["startup_message"])
            assert "Tau 0.2.0 is available" not in message
            assert kwargs["startup_notices"] == ("Tau 0.2.0 is available",)
            assert "Login required. Run /login" in message
            assert "/login openai" in message
            assert "OPENAI_API_KEY" not in message
            assert "environment variable" not in message

        async def run_async(self) -> None:
            calls.append("run")

    monkeypatch.setattr(tui_app, "load_provider_settings", lambda: ProviderSettings())
    monkeypatch.setattr(tui_app, "provider_has_usable_credentials", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        tui_app,
        "create_model_provider",
        lambda provider, **kwargs: (_ for _ in ()).throw(RuntimeError("Missing provider API key.")),
    )
    monkeypatch.setattr(tui_app, "CodingSession", FakeCodingSession)
    monkeypatch.setattr(tui_app, "TauTuiApp", FakeApp)
    monkeypatch.setattr(tui_app, "load_tui_settings", lambda: TuiSettings())

    await tui_app.run_tui_app(
        cwd=tmp_path,
        model=None,
        session_manager=FakeManager(),
        startup_notice="Tau 0.2.0 is available",
    )

    assert calls == [f"prepare:{tmp_path}:gpt-5.5:openai", "load:LoginRequiredProvider", "run"]


@pytest.mark.anyio
async def test_run_tui_app_resumes_explicit_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    record = CodingSessionRecord(
        id="session-1",
        path=tmp_path / "session-1.jsonl",
        cwd=tmp_path,
        model="fake-model",
        title=None,
        created_at=1.0,
        updated_at=1.0,
        provider_name="local",
    )

    class FakeProvider:
        async def aclose(self) -> None:
            calls.append("provider_closed")

    class FakeManager:
        def create_session(
            self,
            *,
            cwd: Path,
            model: str,
            provider_name: str | None = None,
        ) -> CodingSessionRecord:
            raise AssertionError("explicit resume should not create a new session")

        def get_session(self, session_id: str) -> CodingSessionRecord | None:
            calls.append(f"get:{session_id}")
            return record

    class FakeCodingSession:
        @classmethod
        async def load(cls, config: object) -> str:
            assert config.provider_name == "local"  # type: ignore[attr-defined]
            assert config.model == "fake-model"  # type: ignore[attr-defined]
            calls.append("load")
            return "session"

    class FakeApp:
        def __init__(self, session: str, **kwargs: object) -> None:
            assert session == "session"
            assert isinstance(kwargs["tui_settings"], TuiSettings)

        async def run_async(self) -> None:
            calls.append("run")

    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5.5",),
                default_model="gpt-5.5",
            ),
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("fake-model",),
                default_model="fake-model",
            ),
        ),
    )
    monkeypatch.setattr(tui_app, "load_provider_settings", lambda: settings)
    monkeypatch.setattr(
        tui_app,
        "create_model_provider",
        lambda provider, **kwargs: (
            calls.append(f"provider:{provider.name}:{kwargs['model']}") or FakeProvider()
        ),
    )
    monkeypatch.setattr(tui_app, "CodingSession", FakeCodingSession)
    monkeypatch.setattr(tui_app, "TauTuiApp", FakeApp)
    monkeypatch.setattr(tui_app, "load_tui_settings", lambda: TuiSettings())

    await tui_app.run_tui_app(
        model=None,
        cwd=tmp_path,
        session_id="session-1",
        session_manager=FakeManager(),
    )

    assert calls == [
        "get:session-1",
        "provider:local:fake-model",
        "load",
        "run",
        "provider_closed",
    ]


@pytest.mark.anyio
async def test_run_tui_app_ignores_uncredentialed_provider_when_matching_resume_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    record = CodingSessionRecord(
        id="session-1",
        path=tmp_path / "session-1.jsonl",
        cwd=tmp_path,
        model="shared-model",
        title=None,
        created_at=1.0,
        updated_at=1.0,
    )

    class FakeCredentialStore:
        def get(self, name: str) -> str | None:
            return "stored-key" if name == "openai" else None

        def get_oauth(self, name: str) -> object | None:
            return None

    class FakeProvider:
        async def aclose(self) -> None:
            calls.append("provider_closed")

    class FakeManager:
        def get_session(self, session_id: str) -> CodingSessionRecord | None:
            calls.append(f"get:{session_id}")
            return record

    class FakeCodingSession:
        @classmethod
        async def load(cls, config: object) -> str:
            assert config.provider_name == "openai"  # type: ignore[attr-defined]
            calls.append("load")
            return "session"

    class FakeApp:
        def __init__(self, session: str, **kwargs: object) -> None:
            assert session == "session"

        async def run_async(self) -> None:
            calls.append("run")

    settings = ProviderSettings(
        default_provider="openai",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                credential_name=None,
                models=("shared-model",),
                default_model="shared-model",
            ),
            OpenAICompatibleProviderConfig(
                name="openai",
                credential_name="openai",
                models=("shared-model",),
                default_model="shared-model",
            ),
        ),
    )
    monkeypatch.delenv("LOCAL_API_KEY", raising=False)
    monkeypatch.setattr(tui_app, "FileCredentialStore", lambda: FakeCredentialStore())
    monkeypatch.setattr(tui_app, "load_provider_settings", lambda: settings)
    monkeypatch.setattr(tui_app, "load_tui_settings", lambda: TuiSettings())
    monkeypatch.setattr(
        tui_app,
        "create_model_provider",
        lambda provider, **kwargs: (
            calls.append(f"provider:{provider.name}:{kwargs['model']}") or FakeProvider()
        ),
    )
    monkeypatch.setattr(tui_app, "CodingSession", FakeCodingSession)
    monkeypatch.setattr(tui_app, "TauTuiApp", FakeApp)

    await tui_app.run_tui_app(
        model=None,
        cwd=tmp_path,
        session_id="session-1",
        session_manager=FakeManager(),
    )

    assert calls == [
        "get:session-1",
        "provider:openai:shared-model",
        "load",
        "run",
        "provider_closed",
    ]


class _FakeSessionManager:
    def __init__(self, records: list[CodingSessionRecord]) -> None:
        self._records = records

    def list_sessions(self, cwd: Path | None = None) -> list[CodingSessionRecord]:
        del cwd
        return self._records
