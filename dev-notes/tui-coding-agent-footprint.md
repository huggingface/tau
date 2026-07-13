# TUI on the Coding Agent Layer: Development Footprint

This report records what Tau learned while building the Textual TUI on top of the
coding-agent layer (`tau_coding`) and the reusable harness layer (`tau_agent`). It
is intended for future agents or contributors who build another TUI, so they can
start with the architectural constraints and known failure modes instead of
rediscovering them one bug at a time.

The focus is not visual polish alone. The important lessons are about the
frontend contract: where UI code may depend on `tau_coding`, where it must not
reach into `tau_agent`, how streamed events relate to durable session state, and
which concurrency/session/scrolling traps have already appeared in production
work.

## Sources reviewed

This report was written from the current source tree plus project history:

- `git log --all` for TUI, session, harness, event, compaction, command, and
  rendering commits.
- Merged PRs, issue bodies, and PR/issue comments via GitHub CLI for the TUI
  build path and follow-up fixes.
- Current implementation files:
  - `src/tau_agent/events.py`
  - `src/tau_agent/loop.py`
  - `src/tau_agent/harness.py`
  - `src/tau_coding/session.py`
  - `src/tau_coding/tui/adapter.py`
  - `src/tau_coding/tui/state.py`
  - `src/tau_coding/tui/app.py`
  - `src/tau_coding/tui/widgets.py`
  - `src/tau_coding/tui/autocomplete.py`
- Existing dev notes under `dev-notes/architecture/`, especially phases 11, 12,
  17, 20.x, 22, 23, 24, `queued-steering-follow-ups.md`, and
  `provider-retries.md`.

Useful history anchors:

- PR #11: print/event rendering modes.
- PR #12: first Textual TUI behind `TuiState`/`TuiEventAdapter`.
- PR #14: early TUI worker/transcript restoration fixes.
- PR #15: session manager/resume support.
- PRs #25, #26, #39, #40, #67, #154, #177, #190: transcript, selection,
  streaming, tool display, and scrolling lessons.
- PRs #43, #45, #46, #47, #99, #169: retry/thinking/queue/cancellation/tool
  interruption event semantics.
- PRs #101, #107, #142, #144, #161, #168, #180: session tree, compaction,
  persistence, and empty-session invariants.
- PRs #98, #100, #140, #147, #148, #149, #152, #153, #174, #198: commands,
  pickers, prompt expansion, terminal commands, and local-only output.
- Open issue #166: prompt-display latency versus durability ordering.
- Open issue #205: future custom TUI support.

## Executive summary

A Tau TUI should be built on this boundary:

```text
CodingSession emits AgentEvent values
        ↓
frontend adapter/state translates events into UI state
        ↓
frontend widgets render that state
```

The TUI should use `tau_coding.session.CodingSession`, not raw provider streams
and usually not raw `AgentHarness`. `CodingSession` is the application/session
environment: it owns provider/model selection, tools, persistence, resources,
skills, prompt templates, commands, compaction, diagnostics, and session-manager
integration. `AgentHarness` is the reusable brain: it owns the active transcript,
queues, cancellation token, event stream, and loop delegation. `tau_agent` must
stay portable and independent of Textual/Rich/keybindings/config paths/slash
commands.

The biggest lesson from the development history is that the event stream is the
contract, but it is not the entire product. A frontend must also respect the
coding-session control surface for:

- slash commands and local-only command output;
- active-run queueing (`steer` and `follow_up`);
- cancellation and stale worker protection;
- session switching and transcript rebuilds;
- compaction, tree branching, model/provider/thinking controls;
- durable persistence timing.

Future custom TUIs should not copy `TauTuiApp` wholesale. Treat it as a reference
implementation. Reuse or mirror the stable seams: `CodingSession`, `AgentEvent`,
`TuiEventAdapter`, `TuiState`, pure autocomplete helpers, and session-manager
methods.

## Layer responsibilities

### `tau_ai`: provider streaming layer

`tau_ai` adapts provider-specific APIs into provider-neutral provider events.
Those provider events are internal to the agent loop. A TUI should not render
OpenAI/Anthropic/Codex chunks directly.

Important provider-level behaviors surfaced upward:

- text deltas;
- thinking/reasoning deltas;
- response start/end;
- provider retry events;
- provider error events with optional structured diagnostic data.

### `tau_agent`: portable harness, loop, events, transcript brain

`tau_agent` owns the reusable agent brain and the frontend event contract.

It must not depend on:

- Textual;
- Rich rendering policy;
- Typer/CLI mode;
- Tau home/session file locations;
- slash-command UX;
- provider credential UI;
- app-specific resources such as `.agents` discovery.

Key objects:

- `AgentEvent` variants in `tau_agent.events`.
- `AgentHarness` in `tau_agent.harness`.
- `run_agent_loop()` in `tau_agent.loop`.
- provider-neutral messages and tools.

`AgentHarness` responsibilities:

- own the in-memory transcript for a run/session;
- append user messages for prompts;
- reject overlapping prompt/continue runs;
- expose `continue_()`;
- expose event listeners;
- support cancellation;
- own steering/follow-up queues;
- repair interrupted tool-call transcripts before the next provider request.

The harness still does not know what a Textual worker, TUI prompt input, command
palette, session picker, or modal is.

### `tau_coding`: coding-agent environment and frontend integration layer

`CodingSession` wraps `AgentHarness` with the app-specific environment:

- durable JSONL/session tree persistence;
- session manager/index/resume/new-session behavior;
- default coding tools;
- system prompt/resource loading;
- skill and prompt-template expansion;
- provider settings, credentials, model choices, thinking levels;
- slash-command dispatch;
- compaction and context accounting;
- diagnostic logging;
- terminal command helpers;
- session export.

A new TUI should depend on `CodingSession` because this is the layer where the
coding-agent product behavior lives.

### `tau_coding.tui`: one frontend, not the architecture

The built-in Textual TUI is one consumer of the contract. Its reusable pieces are:

- `TuiEventAdapter`: pure event-to-state adapter.
- `TuiState`: frontend display state for transcript items, buffers, queues,
  thinking visibility, tool result visibility, loaded skill metadata.
- `autocomplete.py`: pure completion-state builder.

Its Textual-specific pieces are not part of the portable architecture:

- widgets;
- CSS/theme variables;
- keybindings;
- modals/pickers;
- scroll/selection mechanics;
- Textual worker management.

## The `AgentEvent` contract a TUI must handle

The current event union is defined in `src/tau_agent/events.py`.

### Lifecycle events

| Event | `type` | TUI meaning |
|---|---|---|
| `AgentStartEvent` | `agent_start` | Mark the run as active; clear prior run error state. |
| `AgentEndEvent` | `agent_end` | Flush any assistant stream buffer; mark the run idle. |
| `TurnStartEvent` | `turn_start` | A model/tool turn has started. Mostly useful for diagnostics/future UI. |
| `TurnEndEvent` | `turn_end` | A turn has ended. Mostly useful for diagnostics/future UI. |

### Message events

| Event | `type` | TUI meaning |
|---|---|---|
| `MessageStartEvent` | `message_start` | A user/assistant/tool message block is starting. Assistant start usually resets the streaming buffer. |
| `MessageDeltaEvent` | `message_delta` | Append streamed assistant text. |
| `ThinkingDeltaEvent` | `thinking_delta` | Append streamed reasoning/thinking text. Hide by default unless the UI has a thinking display toggle. |
| `MessageEndEvent` | `message_end` | A durable message object is complete. Render user/assistant messages and attach/restore tool messages. |

Important: the user prompt echo is emitted by `AgentHarness`, not the provider,
as a normal user `MessageStartEvent`/`MessageEndEvent` pair after the first
`turn_start`. Do not optimistically duplicate it unless you are explicitly
accepting the durability/display tradeoff described in issue #166.

### Tool events

| Event | `type` | TUI meaning |
|---|---|---|
| `ToolExecutionStartEvent` | `tool_execution_start` | Show a tool call row, normally collapsed. Flush active assistant text first. |
| `ToolExecutionUpdateEvent` | `tool_execution_update` | Show progress/status for a tool. This is part of the public event contract even though the core loop currently emits only start/end. |
| `ToolExecutionEndEvent` | `tool_execution_end` | Attach result to the matching tool call; show success/failure and a bounded preview. |

Tool results must preserve enough structured metadata for restored session
rendering. In particular, edit patches live in tool-result metadata and are used
by the TUI to render diffs after reload as well as live.

### Queue, retry, and error events

| Event | `type` | TUI meaning |
|---|---|---|
| `QueueUpdateEvent` | `queue_update` | Update pending steering/follow-up UI. |
| `RetryEvent` | `retry` | Show provider retry progress as status, not as assistant content. |
| `ErrorEvent` | `error` | Show an error/status row. Non-recoverable errors should end the visible run. Recoverable cancellation should be status, not scary error UI. |

Known cancellation event:

```python
ErrorEvent(message="Agent run cancelled", recoverable=True)
```

The built-in TUI renders this as status rather than an error row.

## Typical event flows

### Simple prompt

```text
agent_start
turn_start
message_start       # user, emitted by AgentHarness
message_end         # user prompt
message_start       # assistant
message_delta*
thinking_delta*     # optional/interleaved depending provider
message_end         # assistant
turn_end
agent_end
```

### Tool use

```text
agent_start
turn_start
message_start       # user
message_end         # user prompt
message_start       # assistant
message_end         # assistant with tool_calls
tool_execution_start
tool_execution_update*   # possible/future/custom
tool_execution_end
turn_end
turn_start          # provider sees tool result in transcript
message_start       # assistant
message_delta*
message_end         # final assistant
turn_end
agent_end
```

### Queueing during an active run

```text
# user submits while an agent run is active
queue_update        # immediate response from CodingSession.prompt(..., streaming_behavior=...)

# later, when the harness drains the queue
message_start       # queued user message
message_end         # queued user message
queue_update        # queue count changed
turn_start          # next model call with updated transcript
...
```

## Crucial architecture decisions

### 1. Event renderers came before Textual

PR #11 added print/event rendering modes (`text`, `json`, `transcript`) before
the full TUI. That was important because it forced the portable contract to be
`AgentEvent` values, not Textual widgets. JSONL output also gave a scriptable way
to inspect event streams.

Future instruction: if adding a frontend capability requires a new event, add it
to the portable event model and update all event consumers: print renderers,
JSON renderer, transcript renderer, TUI adapter, tests, and docs.

### 2. The first TUI used `CodingSession`, not raw `AgentHarness`

PR #12 intentionally built the Textual TUI on `CodingSession.prompt()` and a pure
`TuiEventAdapter`. This preserved the separation:

```text
tau_agent  -> portable brain/events
tau_coding -> coding-agent environment and UI integration
Textual    -> one rendering frontend
```

Future instruction: a new TUI should usually construct/load a `CodingSession`
and consume its event stream. Using `AgentHarness` directly bypasses commands,
persistence, resource expansion, diagnostics, compaction, session manager, and
provider settings.

### 3. Adapter/state must be testable without Textual

The current `TuiEventAdapter` mutates `TuiState` and has no Textual dependency.
This made it possible to cover event-to-display behavior in `tests/test_tui_adapter.py`.

Future instruction: keep frontend state transitions pure where possible. Textual
or another UI framework should only render already-decided state.

### 4. Session messages are the restored transcript source of truth

PR #14 restored previous session messages into the TUI transcript. The stable
pattern is:

```python
state.clear()
state.set_skills(session.skills)
state.load_messages(session.messages)
```

Do not read JSONL directly from a TUI. Use `CodingSession` / `SessionManager` so
branching, compaction entries, tool metadata, and active leaf state are respected.

### 5. Slash commands belong to `tau_coding`, not `tau_agent`

Command registry and command output live in the coding layer. PR #98 explicitly
reviewed commands against Pi and kept command behavior out of the harness.

Future instruction: before sending text to the model, call
`session.handle_command(text)`. If it returns `handled=True`, perform the command
side effect in the frontend and do **not** send it to `session.prompt()` unless
the command semantics say so.

`/skill:<name>` is the important exception: it is intentionally not handled as a
normal command. It is passed to `CodingSession.prompt()`, where it expands into
provider-visible/persisted skill content.

### 6. Local-only command output must not become model context

PR #198 added `/system` as a local-only command. It displays the active system
prompt but does not append a model message and does not persist it as a normal
JSONL message entry.

Future instruction: distinguish three outputs:

1. model-visible and persisted conversation messages;
2. durable session metadata/state entries;
3. local UI-only command output.

Do not fake local command output as assistant/user messages unless it is supposed
to affect future model context.

### 7. Queue ownership lives in the harness

PR #46 moved Pi-style steering/follow-up queues into `AgentHarness`. `CodingSession`
only decides when to call `harness.steer()` or `harness.follow_up()` and expands
prompt text first. The TUI only chooses keybindings and presentation.

Future instruction: if a user submits while `session.is_running`/UI running is
true, do not start another prompt. Call:

```python
session.prompt(text, streaming_behavior="steer")
session.prompt(text, streaming_behavior="follow_up")
```

and render the returned `QueueUpdateEvent`.

### 8. Persistence is tied to streamed `MessageEndEvent`

PR #144 moved session message persistence to the streaming message boundary. This
keeps the session tree current while a run is still active and matches Pi's model
more closely.

Open issue #166 documents the tradeoff: displaying a message before persistence
would make the TUI feel faster in long sessions, but Pi currently persists before
notifying subscribers. Tau has kept the Pi-like durability guarantee for now.

Future instruction: do not optimistically insert submitted user rows unless you
also solve duplicate suppression and accept the crash-window durability tradeoff.

### 9. Themes, keybindings, and layout are frontend policy

PRs #65, #68, #85 and later theme/keybinding work kept visual choices in
`tau_coding.tui`. They must not leak into `tau_agent`.

Future instruction: a custom TUI can ignore `~/.tau/tui.json` entirely. If it
uses it, that dependency belongs in `tau_coding`/frontend code, not the harness.

## Roadblocks and lessons learned

### Roadblock: overlapping prompt runs corrupt or race the transcript

**Seen in:** queued-message work (#28/#46), cancellation fixes (#47), session and
compaction race fixes.

**Symptom:** two UI workers can mutate one transcript/session at the same time,
or late events from an old run can appear after `/new` or resume.

**Root cause:** the transcript is a single ordered conversation. Starting another
run while one is active violates that invariant.

**Resolution:**

- `AgentHarness` rejects overlapping `prompt()`/`continue_()` calls.
- Active user submissions become steering/follow-up queue entries.
- The Textual app tracks `_prompt_run_id` and ignores stale events from old
  workers.
- `/new` cancels active prompt work before swapping session state.

**Future instruction:** maintain a generation/run id in any frontend that can
cancel or replace active workers. Check it before applying streamed events.

### Roadblock: graceful cancellation alone does not stop blocking work

**Seen in:** issue #96, PR #99.

**Symptom:** pressing Escape could request cancellation, but a blocking bash/tool
operation could continue and leave the UI stuck.

**Root cause:** cancellation was initially a UI/session request, not a signal
threaded through provider and tool execution.

**Resolution:**

- Textual requests cancellation.
- `tau_agent` carries a cancellation token through the loop.
- tool execution receives the token.
- the bash tool kills the shell process group when interrupted.
- the TUI uses a two-step mental model: graceful stop first, hard interrupt if
  needed.

**Future instruction:** cancellation must cross all async boundaries. A custom TUI
should call `session.cancel()` and should also be able to cancel its own UI worker
if it provides a hard interrupt.

### Roadblock: stale events after cancellation or `/new`

**Seen in:** PR #47.

**Symptom:** an old worker could still yield events after the visible session had
changed, adding late assistant text to the wrong transcript.

**Root cause:** cancelling a worker/session is not enough; async generators and
background tasks may still deliver final events.

**Resolution:** `_prompt_run_id` increments for each new prompt/cancel/session
reset. `_run_prompt()` checks the id before applying every event and during
cleanup.

**Future instruction:** a new TUI must guard against late events whenever it can:

- cancel a prompt;
- switch sessions;
- start a new session;
- resume a different session;
- replace frontend state.

### Roadblock: interrupted tool calls can make the next provider request invalid

**Seen in:** issue #116 comments, PR #169.

**Symptom:** OpenAI/Codex returned errors like "No tool output found for function
call ..." after a run was interrupted between assistant tool-call emission and
matching tool-result persistence.

**Root cause:** OpenAI-compatible transcripts require every assistant tool call to
be followed by a matching tool result. Cancellation could leave the transcript
with a dangling assistant tool call.

**Resolution:** `AgentHarness` repairs interrupted tool-call transcripts by
appending synthetic failed `ToolResultMessage` objects such as:

```text
Tool call interrupted by user
```

The repair runs on cancelled cleanup and before the next prompt/continue, so even
persisted broken sessions can recover.

**Future instruction:** do not delete or skip tool results in UI/session code. If
a run is interrupted mid-tool, the transcript still needs matching tool-result
messages for provider validity.

### Roadblock: provider errors were too generic to debug

**Seen in:** issue #20, issue #116, PR #31, PR #169.

**Symptom:** the transcript showed only "Provider request failed with status 400"
with no raw provider body, making model/capability/request-shape failures hard to
understand.

**Root cause:** user-facing errors were also the diagnostic payload.

**Resolution:**

- `ErrorEvent` can carry structured `data`.
- `CodingSession` logs non-recoverable `ErrorEvent.data` to agent-call logs.
- the TUI can show the diagnostic log path while keeping transcript errors
  concise.

**Future instruction:** keep user-facing errors short, but preserve structured
provider diagnostic data outside the transcript. Never log prompts or secrets
unless explicitly designed and reviewed.

### Roadblock: queued prompts needed two different semantics

**Seen in:** issue #28, PR #46, issue #53/PR #92, issue #127/PR #136.

**Symptom:** submitting while the agent is running could mean either "steer the
current work as soon as possible" or "do this next after the current work stops".
One queue was not enough.

**Resolution:**

- steering queue: drains after the current assistant turn and tool batch;
- follow-up queue: drains only when the run would otherwise stop;
- `QueueUpdateEvent` reports both queues;
- the TUI maps Enter-while-running to steering and Alt-Enter to follow-up;
- Up on an empty prompt while running can pull back the latest follow-up for
  editing.

**Future instruction:** expose both concepts in a custom TUI. Do not collapse
follow-up into steering unless intentionally changing product behavior.

### Roadblock: queued-message UI could consume too much prompt space

**Seen in:** issue #127, PR #136.

**Symptom:** multiline queued prompts rendered too many lines above the input.

**Resolution:** queue display uses concise labels and first-line previews only.
The full queued content remains in the harness.

**Future instruction:** queue previews are display-only; never truncate the stored
queued message.

### Roadblock: prompt display felt delayed in long sessions

**Seen in:** issue #166, branch `tui-yield-before-persist` commit
`a4ff9fa Improve prompt event immediacy`.

**Symptom:** pressing Enter could feel slow because the TUI waited for
`MessageEndEvent`, and `CodingSession.prompt()` persisted the message before
yielding it.

**Root cause:** event-authoritative display plus synchronous persistence side
effects in long JSONL sessions.

**Decision so far:** keep Pi-like behavior: persist before UI notification for
completed messages. The issue remains open as a documented tradeoff.

**Future instruction:** if changing this, document the durability timing change,
add duplicate-prevention tests, and consider crash behavior between display and
write.

### Roadblock: empty sessions polluted `/resume`

**Seen in:** issue #119/PR #142, issue #179/PR #180.

**Symptom:** opening Tau or running `/new` without sending a message created empty
transcripts or empty indexed sessions that appeared in `/resume`.

**Root cause:** startup/new-session initialization wrote synthetic session entries
or indexed records before the first durable user-visible mutation.

**Resolution:**

- `CodingSession` defers writing initial session-info/model/thinking entries
  until the first durable mutation.
- `SessionManager` has a prepared/unindexed session path.
- TUI startup and `/new` use pending records and index them only when initial
  durable entries flush.

**Future instruction:** new frontends should not eagerly create/index sessions
just by opening a UI. First durable mutation is the correct activation point.

### Roadblock: session manager must preserve provider identity

**Seen in:** issue #117, PR #129.

**Symptom:** new sessions could start on an unexpected model, especially when
same-name models existed across providers or scoped models were configured.

**Root cause:** session metadata did not always carry provider names and startup
selection did not consistently respect scoped/default/latest-directory rules.

**Resolution:** session records persist provider names; startup chooses the latest
usable directory provider/model or falls back through scoped model constraints.

**Future instruction:** model strings are not enough. Store/use provider+model
pairs in UI pickers, session records, and scoped-model logic.

### Roadblock: model picker showed providers the session could not actually use

**Seen in:** issue #21, PR #33 and review comment.

**Symptom:** model picker considered a provider usable because credentials existed
in the session Tau home, but switching provider could fail if provider creation
used the default credential store.

**Root cause:** availability checks and provider construction used different
credential-store scopes.

**Resolution:** provider/model choices are filtered by usable credentials and
session-scoped credential stores are used when switching providers.

**Future instruction:** any custom picker must ask `CodingSession` for available
choices. Do not independently inspect environment variables or credential files.

### Roadblock: thinking controls are provider/model-specific

**Seen in:** issue #22/PR #42, issue #55/PR #113, issue #120, issue #125/PR #147.

**Symptom:** a generic thinking toggle could send unsupported request fields or
hide available reasoning streams.

**Root cause:** providers expose reasoning controls differently:

- OpenAI-compatible chat completions may use top-level `reasoning_effort`;
- Responses/Codex-style transports may use `reasoning: { effort: ... }`;
- Anthropic thinking may involve model-specific budgets/adaptive behavior;
- some models stream reasoning but do not support configurable effort.

**Resolution:** capability metadata lives in `tau_coding`; providers receive only
validated provider-specific runtime parameters; the TUI shows unavailable reasons.
Thinking deltas flow through `ThinkingDeltaEvent` and display is hidden by
default. Thinking level can be changed while a run is active, but it applies to
future turns.

**Future instruction:** never assume a model supports configurable thinking just
because it streams reasoning tokens. Add live-provider validation before adding a
model to `thinking_models`.

### Roadblock: retries needed to be visible without becoming content

**Seen in:** issue #34, PR #43.

**Symptom:** transient provider failures should be retried, and users need to know
why the UI is waiting, but retry notices are not assistant messages.

**Resolution:** provider retries become agent-level `RetryEvent` values and render
as status rows.

**Future instruction:** render retries as status/diagnostic UI. Do not append them
as user or assistant messages.

### Roadblock: tool results were either opaque or too verbose

**Seen in:** issue #18/PR #26, issue #102, issue #157/PR #154, Phase 23 notes.

**Symptom:** hiding tool output made the agent feel opaque; showing full tool
output flooded the transcript.

**Resolution:** the TUI renders tool calls collapsed by default with bounded
previews and an expansion toggle. Edit tool results preserve patch metadata and
can render colored diffs. A broad configurable tool-output preview feature was
closed as too complex for minimalist Tau (#102), but the rendering-focused
preview pattern remained.

**Future instruction:** tool output visibility is frontend policy. Preserve full
tool result data in messages/session state; render bounded previews by default.

### Roadblock: explicit and agent-initiated skill use needed compact display

**Seen in:** issue #51/PR #94, issue #123/PR #148.

**Symptom:** expanded skill prompts and skill-file reads looked like ordinary
messages/tool calls, obscuring when a skill was used.

**Root cause:** skill invocation is model context, but the full skill content is
not a pleasant transcript display.

**Resolution:**

- explicit `/skill:<name>` expansion remains in `CodingSession`, provider-visible
  and persisted;
- the TUI parses structured skill blocks and renders compact `Using skill: ...`;
- agent-initiated reads of known skill files are detected using loaded skill
  metadata in `TuiState` and rendered with skill styling;
- `tau_agent` events/tool calls remain unchanged.

**Future instruction:** do not add skill-specific UI policy to `tau_agent`. Keep
skill interpretation in `tau_coding` or frontend state.

### Roadblock: slash-command autocomplete ranking was misleading

**Seen in:** issue #114, PR #140.

**Symptom:** typing `/resume` could select `/new` first because search-term
matches were not ranked below direct command/alias prefix matches.

**Resolution:** command completions rank direct command/alias matches before
fallback search terms.

**Future instruction:** command autocomplete needs scoring, not only filtering.
Aliases and search terms have different intent.

### Roadblock: completed command tokens kept showing autocomplete

**Seen in:** issue #173, PR #174 and PR comments.

**Symptom:** after selecting `/skill:review ` or a custom prompt command and
starting arguments, generic command suggestions stayed open and became visual
noise.

**Complication:** custom prompt names can collide with built-in commands such as
`/model`, and some built-in commands have argument completions.

**Resolution:** hide generic command-name autocomplete after an exact completed
command token plus space, but preserve argument-specific completions for commands
like `/model`, `/theme`, `/resume`, `/login`, etc. Registered command argument
completions take precedence over prompt-template hiding.

**Future instruction:** implement autocomplete as a pure state builder and test
collisions. Do not bury this logic inside widget key handlers.

### Roadblock: custom prompt templates are dynamic slash commands

**Seen in:** issue #151, PR #152, PR #171.

**Symptom:** `.agents/prompts/example.md` did not work as `/example`.

**Resolution:** loaded prompt templates are exposed as dynamic slash commands and
expanded by `CodingSession` before provider submission. Missing template variables
render as blank text, and invocation arguments are appended unless the template
explicitly references `{{ arguments }}` / `{{ args }}`.

**Future instruction:** prompt-template expansion belongs in `CodingSession`, not
in the TUI. The TUI should only offer completions and submit the resulting text.

### Roadblock: terminal commands needed separate context semantics

**Seen in:** issue #49/PR #93, issue #146/PR #153, issue #103/PR #108.

**Behavior:**

- `! command` runs in the session cwd, displays output, and adds output to model
  context.
- `!! command` runs and displays output without adding it to context/history.

**Roadblock:** these commands initially felt detached from agent tool-call UI.

**Resolution:** the TUI renders terminal commands immediately as tool-like rows,
updates the same row on completion, styles success/failure, and limits output
previews. Shell-mode path autocomplete preserves the `!`/`!!` prefix.

**Future instruction:** terminal commands are `tau_coding` features. A frontend
may render them like tools, but should preserve `!` versus `!!` context semantics.

### Roadblock: compaction races with typing, prompts, queues, and session changes

**Seen in:** issue #52/PR #107, issue #164/PR #168 and review comments.

**Symptoms:**

- input was disabled during compaction, preventing users from drafting;
- making compaction non-exclusive let `/new`/`/resume` run while compaction was
  still mutating the old session;
- compaction could start during an active agent turn or queued follow-up;
- a long/stuck compaction needed cancellation.

**Resolution:**

- manual compaction runs in a non-exclusive worker so the prompt editor stays
  usable;
- prompt submission remains blocked until compaction finishes;
- session-changing commands are blocked while compaction is active;
- manual compaction cannot start while an agent run or queued messages are active;
- Escape cancels active compaction before prompt cancellation;
- on cancellation, visible state reloads from the current session.

**Future instruction:** separate "can type" from "can submit/mutate session".
Do not let compaction overlap session switching or active agent turns.

### Roadblock: session tree branching after user messages was inconsistent

**Seen in:** issue #57/PR #101, issue #143/PR #161 and review comment.

**Symptom:** selecting a user message in `/tree` could continue from after that
message or from an incomplete turn, instead of letting the user edit/resubmit it.
The root/first-user-message case also needed special empty-branch replay on load.

**Resolution:** selecting a user message branches to that message's parent entry
and returns an `input_prefill` for the TUI. The prompt is populated, but the
message is not sent until the user presses Enter. Explicit empty leaf replay is
supported for branching before the first user message.

**Future instruction:** branch navigation changes active history; it should not
implicitly replay selected user input. Rebuild transcript from `session.messages`
and prefill the editor if the branch result asks for it.

### Roadblock: branch summaries and compaction summaries are context, but noisy UI

**Seen in:** PR #101, PR #107, `TuiState.add_user_message()`.

**Symptom:** branch/compaction summaries are represented as user-context messages
for replay, but showing the full summary inline can overwhelm the transcript.

**Resolution:** `TuiState` detects known summary message formats and renders
compact `branch_summary` / `compaction_summary` items with expansion text.

**Future instruction:** some user-role messages are system-generated context.
Frontend state may render them specially, but must preserve them in the session
messages for provider context.

### Roadblock: transcript selection and copy were hard with custom rendering

**Seen in:** issue #19/PR #39, issue #32/PR #40, issue #58/PR #67, issue #150,
issue #156, PR #154.

**Symptoms:**

- selecting/copying text worked only on some lines;
- wrapped lines broke coordinate mapping;
- custom selection painting caused flicker/corruption;
- decorative gutters could pollute selected text.

**Root cause:** Tau initially tried to own too much of the rendering and selection
model.

**Resolution:** move toward Toad-style native Textual widgets:

- streaming assistant/thinking messages use Textual Markdown directly;
- Textual owns native selection for Markdown blocks;
- decorative gutters are non-selectable;
- custom selection is reserved for truly virtual renderers;
- selected-message copy remains as a keyboard fallback.

**Future instruction:** prefer the UI toolkit's native text/Markdown selection
when possible. Avoid coordinate-mapped copy extraction for soft-wrapped content.

### Roadblock: streaming transcript updates broke scrollback

**Seen in:** issue #175, PR #177, earlier PR #154.

**Symptom:** while assistant tokens streamed, trying to scroll up snapped the
viewport back to the bottom.

**Root causes:**

- full transcript refreshes/remounts on each token can reset scroll state;
- unconditional `scroll_end()` pulls users back down;
- Textual's deferred `scroll_end()` can execute after the user scrolls up;
- fractional smooth-scroll values near bottom made bottom detection too eager.

**Resolution:**

- update active streaming widgets incrementally;
- refresh chrome separately from transcript;
- follow output only if the transcript was already pinned to bottom;
- track explicit upward scrollback;
- replace direct deferred `scroll_end()` calls with a helper that re-checks
  follow mode after layout before scrolling immediately;
- upward scroll motion wins over the bottom check.

**Future instruction:** streaming UIs must distinguish "follow mode" from "always
scroll to end". Never remount the whole transcript for every token.

### Roadblock: code block rendering had multiple edge cases

**Seen in:** issue #59/PR #66, PR #190.

**Symptoms:**

- unknown fenced-code languages could break syntax rendering;
- long code lines were clipped with no horizontal scrollbar;
- showing a scrollbar when not needed was noisy.

**Resolution:**

- validate lexer names and fall back unknown languages to plain text;
- use horizontal overflow scrolling for Markdown fences;
- show the scrollbar only on overflow.

**Future instruction:** model-generated fence labels are untrusted. Treat syntax
highlighting as best-effort and always provide a plain fallback.

### Roadblock: visual notifications became noisy

**Seen in:** issue #81/PR #88, issue #121/PR #137, issue #122/PR #138, issue
#124/PR #135, issue #127/PR #136.

**Symptoms:** repeated toasts stacked, session-start notifications were noisy,
thinking toggle notifications were unnecessary, and long queue labels used too
much space.

**Resolution:**

- dedupe active notifications by message/severity;
- use notifications for short confirmations like successful `/name`;
- use modals for longer command information;
- use transcript/status rows for local outputs that should remain visible;
- remove noisy notifications for purely local toggles.

**Future instruction:** choose output surface deliberately:

- transient toast: short confirmation;
- modal/picker: reference or multi-line command output;
- status row: run/retry/cancel/tool progress;
- transcript item: only if the user should see it in conversation history;
- durable message: only if the model should see it later.

### Roadblock: login/model/provider UI must not own provider logic

**Seen in:** PRs #33, #90, #100, #129, #149.

**Lessons:**

- model/provider choices come from `CodingSession`, already filtered for usable
  credentials and scoped settings;
- provider+model pairs matter;
- login/logout mutate credential stores and refresh provider settings;
- environment variables and provider config are distinct from stored Tau
  credentials.

**Future instruction:** custom pickers should be thin selectors over
`CodingSession` methods. They should not reimplement provider configuration.

### Roadblock: tests became sensitive to local credentials and UI framework details

**Seen in:** several PR notes, especially #152 and #153.

**Symptoms:** full test runs could fail locally because stored credentials changed
default provider availability; Textual rendering tests were sensitive to theme,
formatting, and terminal behavior.

**Resolution pattern:**

- use fake sessions/providers for deterministic event-stream tests;
- test pure helpers (`TuiEventAdapter`, `build_completion_state`) separately from
  Textual widgets;
- add focused regression tests for each roadblock;
- reserve manual validation for terminal behaviors that are hard to simulate,
  such as scrolling and selection.

**Future instruction:** for new TUI work, write tests at the lowest possible
layer first, then add one or two integration tests for the Textual/custom UI
surface.

## Recommended build plan for a future custom TUI

1. **Start from `CodingSession`, not Textual internals.**
   Load/create a session the same way the CLI does. Use session properties and
   methods as your API.

2. **Implement an event consumer for every `AgentEvent`.**
   It can reuse `TuiEventAdapter` or define a new pure adapter. Handle unknown
   future events defensively if your code sees a `type` string it does not know.

3. **Restore transcript from `session.messages`.**
   Do this on startup, resume, `/new`, tree branch, compaction completion, model
   provider changes that reload state, and cancellation recovery.

4. **Handle commands before prompts.**
   Use `session.handle_command(text)`. Keep command output local unless the
   command explicitly adds context.

5. **Queue while running.**
   Do not start overlapping runs. Use `streaming_behavior="steer"` or
   `"follow_up"` and render `QueueUpdateEvent`.

6. **Guard stale workers.**
   Maintain a run/session generation id. Ignore late events after cancellation,
   `/new`, resume, or tree branch.

7. **Use cancellation in layers.**
   Call `session.cancel()` for graceful cancellation and cancel your UI worker for
   hard interruption. Continue to expect recoverable cancellation events.

8. **Separate display state from durable state.**
   A UI transcript row is not automatically a model message. Local command output,
   retry status, notifications, and hidden thinking text have different
   persistence semantics.

9. **Stream incrementally.**
   Do not rebuild/remount the entire transcript on every token. Track scroll
   follow mode explicitly.

10. **Keep autocomplete pure.**
    Build a completion state from text+session metadata, then let the UI apply a
    selected replacement. Test command/prompt/skill collisions.

11. **Block dangerous concurrent mutations.**
    Do not allow compaction, session switching, model switching, or tree branching
    to race an active prompt unless the session API explicitly supports it.

12. **Render tools compactly by default.**
    Preserve full results in session state, but show bounded previews and provide
    an expansion path.

13. **Respect provider/model capabilities.**
    Ask `CodingSession` what is available. Do not infer thinking/model support in
    the frontend.

14. **Document any new frontend-only behavior.**
    If it affects user-visible behavior, add a dev note and user docs. If it
    affects the event contract, update all renderers and tests.

## Minimal custom frontend pseudocode

```python
async def submit(text: str) -> None:
    command = session.handle_command(text)
    if command.handled:
        await apply_command_result(command)
        return

    if ui_state.running:
        async for event in session.prompt(text, streaming_behavior="steer"):
            adapter.apply(event)
            redraw()
        return

    run_id = next_run_id()
    async for event in session.prompt(text):
        if run_id != current_run_id:
            return
        adapter.apply(event)
        redraw_event_incrementally_if_possible(event)
```

For cancellation:

```python
def cancel() -> None:
    invalidate_current_run_id()
    session.cancel()
    cancel_ui_worker_if_needed()
```

For session changes:

```python
async def resume(session_id: str) -> None:
    invalidate_current_run_id()
    session.cancel()
    await session.resume(session_id)
    state.clear()
    state.set_skills(session.skills)
    state.load_messages(session.messages)
    redraw()
```

## Testing checklist for future TUI work

Add or update tests in the closest layer:

- `tests/test_agent_loop.py` for raw event emission rules.
- `tests/test_agent_harness.py` for transcript, queues, cancellation, and repair.
- `tests/test_coding_session.py` for persistence, commands, resources,
  compaction, provider settings, and session manager behavior.
- `tests/test_tui_adapter.py` for event-to-display state behavior.
- `tests/test_tui_autocomplete.py` for completions.
- `tests/test_tui_app.py` or equivalent for UI worker/keybinding/picker behavior.

Regression cases that should exist for any serious new TUI:

- user prompt echo appears once;
- assistant deltas stream and final message flushes correctly;
- tool start/end attaches result to the right row;
- non-recoverable provider error stops running UI and shows diagnostic path if
  available;
- recoverable cancellation is status, not fatal error;
- stale events after cancellation/session switch are ignored;
- active-run submit queues instead of overlapping;
- queued follow-up can be edited without losing full content;
- session resume/new/tree/compaction rebuilds transcript from `session.messages`;
- `/system`-style local output is not persisted or sent to provider;
- compaction cannot race active prompts/queues/session switches;
- scrollback is preserved during streaming;
- unknown code fence languages do not break rendering;
- argument completions survive command/prompt name collisions.

## Open questions and future work

### Custom TUI loading/discovery

Issue #205 tracks explicit support for custom TUI selection/discovery, e.g.
`tau --tui my-custom-tui`. The likely architecture is:

- `tau_agent` exposes only generic events/session primitives;
- `tau_coding` owns CLI discovery, resource loading, and concrete frontend
  selection;
- Textual-specific code remains optional and isolated;
- custom TUIs consume `CodingSession`/`AgentEvent` rather than private internals.

The unresolved design question is distribution/discovery: Python entry points,
Tau-managed TUI directories, extension-provided adapters, or a combination.

### Prompt latency versus durability

Issue #166 remains open. The current Pi-like invariant is: completed messages are
persisted before the UI sees the `MessageEndEvent`. Future work may choose a
faster display-before-persist strategy, but that must be documented as a
durability tradeoff.

### Event contract additions

`ToolExecutionUpdateEvent` exists in the contract but is not currently emitted by
the core loop. Future streaming/progress-aware tools may use it. New frontends
should still implement it now.

### Textual app size

`src/tau_coding/tui/app.py` has grown into a large reference implementation with
many product workflows. Future custom TUI work may benefit from extracting a
smaller frontend protocol or controller object from `tau_coding` so new UIs can
reuse session/command/picker orchestration without importing Textual widgets.

## Short instruction block for future agents

If you are an agent building a new Tau TUI, follow these rules before coding:

1. Build on `CodingSession`, not provider chunks and not raw JSONL.
2. Treat `AgentEvent` as the streaming contract; handle every event type.
3. Keep `tau_agent` free of UI, command, theme, keybinding, and path policy.
4. Use `session.handle_command()` before `session.prompt()`.
5. Queue active-run input with `streaming_behavior`, never overlap prompts.
6. Use a run/session generation id to ignore late events.
7. Call `session.cancel()` and be prepared to cancel your own UI worker.
8. Rebuild visible transcript from `session.messages` after session mutations.
9. Separate local UI output from model-visible/persisted messages.
10. Do not remount the whole transcript on every token; preserve scrollback.
11. Do not let compaction/session switching/model switching race an active run.
12. Ask `CodingSession` for provider/model/thinking capabilities; do not infer
    them in the frontend.
13. Keep autocomplete and event adaptation pure/testable.
14. Add focused regression tests for every roadblock listed above.
