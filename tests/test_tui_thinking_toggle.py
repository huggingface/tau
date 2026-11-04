"""Ctrl+T shows and hides every thinking block in the transcript at once.

Each test streams several turns that each think, toggles, and reads every
block's visibility off the mounted rows: after the turns have settled, and
while the last one is still streaming.
"""

import asyncio

import pytest
from textual.widgets._markdown import MarkdownBlock

from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    UserMessage,
)
from tau_agent.provider_events import TextDeltaEvent, ThinkingDeltaEvent, ThinkingEndEvent
from tau_coding.tui.app import TauTuiApp
from tau_coding.tui.widgets import (
    StreamingTranscriptMessageWidget,
    TranscriptMessageWidget,
    TranscriptView,
)
from test_tui_app import FakeSession

HIDDEN_LINE = "Thinking… Press Ctrl+T to show thinking tokens."
TURNS = 3
# Positions in `_turn_events(..., with_tool=False)`.
AFTER_FIRST_THINKING_DELTA = 4
AFTER_THINKING = 5
AFTER_TEXT_STARTED = 6


def _thinking_delta(partial: AssistantMessage, delta: str) -> MessageUpdateEvent:
    return MessageUpdateEvent(
        message=partial,
        assistant_message_event=ThinkingDeltaEvent(content_index=0, delta=delta, partial=partial),
    )


def _text_delta(partial: AssistantMessage, delta: str) -> MessageUpdateEvent:
    return MessageUpdateEvent(
        message=partial,
        assistant_message_event=TextDeltaEvent(content_index=1, delta=delta, partial=partial),
    )


def _turn_events(index: int, *, with_tool: bool) -> list[AgentEvent]:
    """One prompt and its reply: thinking streamed in two deltas, then an answer."""
    thinking = f"plan {index}"
    answer = f"answer {index}"
    partial = AssistantMessage()
    events: list[AgentEvent] = [
        MessageEndEvent(message=UserMessage(content=f"prompt {index}")),
        AgentStartEvent(),
        MessageStartEvent(message=partial),
        _thinking_delta(partial, "plan "),
        _thinking_delta(partial, str(index)),
    ]
    final: list[ThinkingContent | TextContent]
    if with_tool:
        call = ToolCall(
            id=f"call-{index}",
            name="bash",
            arguments={"command": "ls", "description": f"Step {index}"},
        )
        events += [
            MessageEndEvent(
                message=AssistantMessage(content=[ThinkingContent(thinking=thinking), call])
            ),
            ToolExecutionStartEvent(tool_call_id=call.id, tool_name=call.name, args=call.arguments),
            ToolExecutionEndEvent(
                tool_call_id=call.id,
                tool_name=call.name,
                result=AgentToolResult(content="ok"),
                is_error=False,
            ),
        ]
        partial = AssistantMessage()
        events.append(MessageStartEvent(message=partial))
        final = [TextContent(text=answer)]
    else:
        final = [ThinkingContent(thinking=thinking), TextContent(text=answer)]
    events += [
        _text_delta(partial, answer),
        MessageEndEvent(message=AssistantMessage(content=final)),
        AgentEndEvent(),
    ]
    return events


def _agentic_run(turn: int, steps: int) -> list[AgentEvent]:
    """One agent run of several messages, each thinking, then calling a tool."""
    events: list[AgentEvent] = [AgentStartEvent()]
    for step in range(steps):
        thinking = f"plan {turn}.{step}"
        note = f"note {turn}.{step}"
        partial = AssistantMessage()
        events += [
            MessageStartEvent(message=partial),
            _thinking_delta(partial, thinking[:5]),
            _thinking_delta(partial, thinking[5:]),
            MessageUpdateEvent(
                message=partial,
                assistant_message_event=ThinkingEndEvent(
                    content_index=0, content=thinking, partial=partial
                ),
            ),
            _text_delta(partial, note),
        ]
        content: list[ThinkingContent | TextContent | ToolCall] = [
            ThinkingContent(thinking=thinking),
            TextContent(text=note),
        ]
        if step == steps - 1:
            events.append(MessageEndEvent(message=AssistantMessage(content=content)))
            continue
        call = ToolCall(
            id=f"call-{turn}-{step}",
            name="bash",
            arguments={"command": "ls", "description": f"Step {turn}.{step}"},
        )
        events += [
            MessageEndEvent(message=AssistantMessage(content=[*content, call])),
            ToolExecutionStartEvent(tool_call_id=call.id, tool_name=call.name, args=call.arguments),
            ToolExecutionEndEvent(
                tool_call_id=call.id,
                tool_name=call.name,
                result=AgentToolResult(content="ok"),
                is_error=False,
            ),
        ]
    events.append(AgentEndEvent())
    return events


def _rows(app: TauTuiApp) -> list[TranscriptMessageWidget | StreamingTranscriptMessageWidget]:
    transcript = app.query_one("#transcript", TranscriptView)
    return [
        child
        for child in transcript.children
        if isinstance(child, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
    ]


def _visible_thinking(app: TauTuiApp) -> list[str]:
    """The text of every thinking block on screen, in transcript order."""
    return [
        row.selection_text.strip()
        for row in _rows(app)
        if row.item.role == "thinking" and row.selection_text.strip() != HIDDEN_LINE
    ]


def _rendered_thinking(app: TauTuiApp) -> list[list[str]]:
    """The Markdown blocks each thinking row draws, which can differ from its text."""
    return [
        [str(block.content) for block in row.query_children(MarkdownBlock)]
        for row in _rows(app)
        if row.item.role == "thinking"
    ]


def _hidden_line_positions(app: TauTuiApp) -> list[int]:
    return [
        index for index, row in enumerate(_rows(app)) if row.selection_text.strip() == HIDDEN_LINE
    ]


def _streamer(app: TauTuiApp):
    async def stream(event: AgentEvent) -> None:
        # The app projects a confirmed user message itself, as `_run_prompt`
        # does; handing it to the adapter too would add it twice.
        if not (isinstance(event, MessageEndEvent) and isinstance(event.message, UserMessage)):
            app.adapter.apply(event)
        await app._apply_streaming_transcript_event(event)

    return stream


async def _toggle(pilot) -> None:
    await pilot.press("ctrl+t")
    await pilot.pause()


@pytest.mark.anyio
@pytest.mark.parametrize("with_tool", [False, True])
@pytest.mark.parametrize("start_shown", [False, True])
async def test_thinking_toggle_all_after_streaming(with_tool: bool, start_shown: bool) -> None:
    app = TauTuiApp(FakeSession())
    stream = _streamer(app)
    every_plan = [f"plan {index}" for index in range(TURNS)]

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        if start_shown:
            await _toggle(pilot)
        for index in range(TURNS):
            for event in _turn_events(index, with_tool=with_tool):
                await stream(event)
                await pilot.pause()

        assert _visible_thinking(app) == (every_plan if start_shown else [])
        for _ in range(3):
            await _toggle(pilot)
            assert _visible_thinking(app) == (every_plan if app.state.show_thinking else [])
            assert _hidden_line_positions(app) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "toggle_at", [AFTER_FIRST_THINKING_DELTA, AFTER_THINKING, AFTER_TEXT_STARTED]
)
@pytest.mark.parametrize("start_shown", [False, True])
async def test_thinking_toggle_all_during_streaming(toggle_at: int, start_shown: bool) -> None:
    """A toggle while the last turn streams reaches the earlier turns, and the
    block still streaming stays one row that its MessageEnd replaces."""
    app = TauTuiApp(FakeSession())
    stream = _streamer(app)
    earlier = [f"plan {index}" for index in range(TURNS - 1)]
    every_plan = [*earlier, f"plan {TURNS - 1}"]

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        if start_shown:
            await _toggle(pilot)
        for index in range(TURNS):
            for position, event in enumerate(_turn_events(index, with_tool=False)):
                last_turn = index == TURNS - 1
                if last_turn and position == toggle_at:
                    await _toggle(pilot)
                    in_flight = app.state.items[-1].text.strip()
                    if app.state.show_thinking:
                        assert _visible_thinking(app) == [*earlier, in_flight]
                    else:
                        assert _visible_thinking(app) == []
                await stream(event)
                await pilot.pause()
                if last_turn and position >= toggle_at and app.state.show_thinking:
                    # Deltas after the toggle land in the same row: the open
                    # block is never split in two, nor left behind twice.
                    shown = _visible_thinking(app)
                    assert shown[: len(earlier)] == earlier
                    assert len(shown) == TURNS

        assert app.state.show_thinking is not start_shown
        assert _visible_thinking(app) == (every_plan if app.state.show_thinking else [])
        for _ in range(2):
            await _toggle(pilot)
            assert _visible_thinking(app) == (every_plan if app.state.show_thinking else [])


@pytest.mark.anyio
async def test_thinking_toggle_all_status_line_only_for_the_open_block() -> None:
    """Hidden, only the open block shows the status line; earlier hidden
    blocks show nothing."""
    app = TauTuiApp(FakeSession())
    stream = _streamer(app)

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await _toggle(pilot)
        events = _turn_events(TURNS - 1, with_tool=False)
        for index in range(TURNS - 1):
            for event in _turn_events(index, with_tool=False):
                await stream(event)
        for event in events[:AFTER_FIRST_THINKING_DELTA]:
            await stream(event)
        await pilot.pause()
        assert _visible_thinking(app) == ["plan 0", "plan 1", "plan"]

        await _toggle(pilot)
        assert _visible_thinking(app) == []
        assert _hidden_line_positions(app) == [len(_rows(app)) - 1]

        await stream(events[AFTER_FIRST_THINKING_DELTA])
        await pilot.pause()
        assert _hidden_line_positions(app) == [len(_rows(app)) - 1]

        await _toggle(pilot)
        assert _hidden_line_positions(app) == []
        assert _visible_thinking(app) == ["plan 0", "plan 1", "plan 2"]

        await _toggle(pilot)
        for event in events[AFTER_THINKING:]:
            await stream(event)
            await pilot.pause()
        # Answered and settled: the status line is taken back.
        assert _hidden_line_positions(app) == []
        assert _visible_thinking(app) == []
        await _toggle(pilot)
        assert _visible_thinking(app) == ["plan 0", "plan 1", "plan 2"]


@pytest.mark.anyio
@pytest.mark.parametrize("start_shown", [False, True])
async def test_thinking_toggle_all_through_run_prompt(start_shown: bool) -> None:
    """Several agent runs, each several thinking messages with tool calls."""
    session = FakeSession()
    app = TauTuiApp(session)
    every_plan = [f"plan {turn}.{step}" for turn in range(TURNS) for step in range(3)]

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        if start_shown:
            await _toggle(pilot)
        for turn in range(TURNS):
            session.events = tuple(_agentic_run(turn, 3))
            await app._run_prompt(f"prompt {turn}")
            await pilot.pause()

        assert _visible_thinking(app) == (every_plan if start_shown else [])
        for _ in range(3):
            await _toggle(pilot)
            assert _visible_thinking(app) == (every_plan if app.state.show_thinking else [])


@pytest.mark.anyio
@pytest.mark.parametrize("start_shown", [False, True])
async def test_thinking_toggle_all_in_a_paged_window(start_shown: bool) -> None:
    """Past the mounted window, every block in the window toggles."""
    app = TauTuiApp(FakeSession())
    stream = _streamer(app)

    def window_plans() -> list[str]:
        transcript = app.query_one("#transcript", TranscriptView)
        window = app.state.items[transcript._window_start : transcript._window_end]
        return [item.text for item in window if item.role == "thinking"]

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        if start_shown:
            await _toggle(pilot)
        for index in range(30):
            for event in _turn_events(index, with_tool=True):
                await stream(event)
            await pilot.pause()
        assert app.query_one("#transcript", TranscriptView)._window_start > 0
        assert len(window_plans()) > TURNS

        assert _visible_thinking(app) == (window_plans() if start_shown else [])
        for _ in range(2):
            await _toggle(pilot)
            assert _visible_thinking(app) == (window_plans() if app.state.show_thinking else [])


@pytest.mark.anyio
async def test_thinking_toggle_off_and_on_mid_stream_draws_each_paragraph_once() -> None:
    """The row a toggle mounts mid-stream starts with the text so far. The next
    delta must not reach Markdown before that text is drawn, or it is drawn
    twice: Markdown.append reads its parse position before taking its lock."""
    app = TauTuiApp(FakeSession())
    stream = _streamer(app)

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await _toggle(pilot)
        partial = AssistantMessage()
        for event in [
            MessageEndEvent(message=UserMessage(content="prompt")),
            AgentStartEvent(),
            MessageStartEvent(message=partial),
            _thinking_delta(partial, "one.\n\n"),
            _thinking_delta(partial, "two.\n\n"),
            _thinking_delta(partial, "three"),
        ]:
            await stream(event)
            await pilot.pause()

        # Straight to the actions: a key press would wait for the mount, and
        # a real delta lands sooner.
        app.action_toggle_thinking()
        app.action_toggle_thinking()
        await stream(_thinking_delta(partial, " more\n\nfour"))
        for _ in range(3):
            await pilot.pause()

        assert _rendered_thinking(app) == [["one.", "two.", "three more", "four"]]


@pytest.mark.anyio
@pytest.mark.parametrize("toggles", [1, 2])
@pytest.mark.parametrize("start_shown", [False, True])
async def test_thinking_toggle_while_the_first_row_mounts(toggles: int, start_shown: bool) -> None:
    """A toggle can land while the first delta awaits its row's mount. The
    toggle rebuilds thinking from state, which already holds that delta, so
    the delta must neither crash the stream nor reach a second row."""
    app = TauTuiApp(FakeSession())
    stream = _streamer(app)

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        if start_shown:
            await _toggle(pilot)
        partial = AssistantMessage()
        for event in [
            MessageEndEvent(message=UserMessage(content="prompt")),
            AgentStartEvent(),
            MessageStartEvent(message=partial),
        ]:
            await stream(event)

        first = _thinking_delta(partial, "plan ")
        app.adapter.apply(first)
        mounting = asyncio.create_task(app._apply_streaming_transcript_event(first))
        await asyncio.sleep(0)
        assert not mounting.done()
        for _ in range(toggles):
            app.action_toggle_thinking()
        await mounting
        await pilot.pause()
        assert _visible_thinking(app) == (["plan"] if app.state.show_thinking else [])
        await stream(_thinking_delta(partial, "0"))
        await pilot.pause()
        assert _visible_thinking(app) == (["plan 0"] if app.state.show_thinking else [])
        for event in [
            MessageEndEvent(
                message=AssistantMessage(
                    content=[ThinkingContent(thinking="plan 0"), TextContent(text="answer 0")]
                )
            ),
            AgentEndEvent(),
        ]:
            await stream(event)
            await pilot.pause()

        assert [item.role for item in app.state.items] == ["user", "thinking", "assistant"]
        assert _visible_thinking(app) == (["plan 0"] if app.state.show_thinking else [])
        await _toggle(pilot)
        assert _visible_thinking(app) == (["plan 0"] if app.state.show_thinking else [])
