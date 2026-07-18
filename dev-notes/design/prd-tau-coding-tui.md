---
title: "PRD — tau_coding: Coding-Agent Layer & Interactive TUI"
status: normative
audience: implementers recreating or evolving Tau's coding-agent frontend
---

# PRD: `tau_coding` — Coding-Agent Layer and Terminal UI

This document is a **product/architecture requirements specification** for Tau's
coding-agent layer (`tau_coding`) and its built-in Textual TUI. It consolidates
everything learned across the project's commit history, pull requests, and issue
tracker (through v0.2.0, ~PR #380 / issue #373), including the **edge cases and
fixes found along the way**, so a new TUI can be recreated from scratch without
re-discovering them.

It is written to be sufficient to rebuild the frontend against the stable core
(`tau_agent`, `tau_ai`) without reading the current implementation.

---

## 1. Purpose and scope

### 1.1 What `tau_coding` is

`tau_coding` is the **coding-agent application layer**. It wraps the portable
agent harness (`tau_agent`) with everything a coding assistant needs on a real
machine:

- a persistent, resumable **session** (conversation + session tree on disk)
- provider/model **configuration and credentials**
- **skills**, **prompt templates**, **project instructions** (AGENTS.md)
- built-in **coding tools** (read/write/edit/bash)
- **slash commands**, **context compaction**, **session branching**
- an **extension system**
- one or more **frontends**: print mode, JSON event mode, and the interactive TUI

### 1.2 What this PRD covers

- The `CodingSession` environment contract a frontend drives.
- The **canonical event stream** a frontend consumes.
- The **interactive TUI**: layout, widgets, keybindings, pickers, streaming,
  autocomplete, theming, terminal integration.
- **Non-goal:** the internals of `tau_agent` (agent loop, harness, session tree
  primitives) and `tau_ai` (provider streaming). Those are dependencies, specified
  only by the contract `tau_coding` relies on.

### 1.3 Architectural boundary (inviolable)

```text
AgentHarness  = reusable brain          (tau_agent)
AgentSession  = coding-agent environment (tau_coding.session.CodingSession)
TUI           = one possible frontend    (tau_coding.tui)
```

Rules that have held since Phase 0 and must continue to hold:

- `tau_agent` **must not** import Textual, Rich, Typer, CLI, resource-path, or
  rendering code. (Enforced by `tests/test_pi_event_protocol.py` layering checks.)
- `tau_coding` → `tau_agent` → `tau_ai`. Never invert. (See issue #317 — a
  `tau_agent`/`tau_ai` import cycle had to be broken by inverting a dependency.)
- The frontend **renders from events and session state**; it never reads
  provider-specific chunks and never reaches into private session internals.
- UI policy (keybindings, themes, pickers, layout) lives **entirely** in
  `tau_coding.tui`, never in the core.

---

## 2. The contract a frontend builds against

A TUI is a consumer of `CodingSession`. The following is the stable surface.
(Reference: `website/content/internals/custom-frontend.md`.)

### 2.1 Driving a turn

```python
async for event in session.prompt(user_text):
    render(event)
```

- The stream yields `CodingSessionEvent` values (§3).
- **Overlapping runs are rejected.** A second `session.prompt(...)` while a run
  is active must pass `streaming_behavior="steer"` or `"follow_up"` (§2.4).
- Provider failures arrive as an assistant message with `stop_reason == "error"`,
  then normal lifecycle events — not as exceptions out of the stream.

### 2.2 Lifecycle / settlement events

Use:

- `agent_start` → enter the **running** state.
- `agent_settled` → leave the running state. **Do not** use `agent_end` for this:
  auto-compaction, auto-retry, or queued continuation may follow `agent_end`,
  and the run is not truly idle until `agent_settled`.

### 2.3 Slash commands

Before treating input as a prompt:

```python
result = session.handle_command(text)
```

If `result.handled`, apply the requested effects (`exit_requested`,
`clear_requested`, `new_session_requested`, `compact_summary`, `resume_session_id`,
picker requests, `message`, …) and render reference output **outside** the durable
conversation (§7.4). `/skill:<name>` is **not** a command — pass it through to
`session.prompt(...)`, which expands it before the run.

### 2.4 Steering and follow-ups (mid-run input)

While a run is active, submitting queues instead of starting a run:

- `streaming_behavior="steer"` — injected after the current assistant turn.
- `streaming_behavior="follow_up"` — injected only when the run would stop.

`QueueUpdateEvent` carries pending queued text for display. Ownership of queues
lives in `tau_agent`; expansion and persistence live in `tau_coding`; the frontend
only decides keybindings and presentation. (See `dev-notes/architecture/queued-steering-follow-ups.md`.)

### 2.5 Restoring a session

Initialize the visible transcript from `session.messages` (the reference
implementation is `TuiState.load_messages()`). Restored `ToolResultMessage`s
preserve structured metadata (e.g. edit patches), so tool results render without
reading JSONL. For switching, use `SessionManager.list_sessions(cwd)` then
`await session.resume(session_id)`, then rebuild the transcript from
`session.messages`.

### 2.6 Cancellation

```python
session.cancel()
```

Keep consuming events until the stream ends. Cancellation is an **intentional
stop, not an error** — render the terminal cancellation as a status, not an
error row (§8.5).

### 2.7 Picker data (read directly from the session)

`command_registry.list_commands()`, `skills`, `prompt_templates`,
`available_model_choices`, `available_models`, `available_providers`,
`thinking_level`, `available_thinking_levels`, `session_manager`. For a model
from another provider: `set_provider(...)` then `set_model(...)`.

---

## 3. Canonical event stream

The frontend consumes a Pi-compatible event protocol (migrated in PR #375; see
`dev-notes/design/pi-event-migration-audit.md` and the canonical-event-stream
reference). Two families:

### 3.1 Portable agent events (`tau_agent.events`)

`agent_start`, `agent_end`, `turn_start`, `turn_end`, `message_start`,
`message_update`, `message_end`, `tool_execution_start`, `tool_execution_update`,
`tool_execution_end`.

- **Streamed provider deltas are nested**: a `MessageUpdateEvent` carries an
  `assistant_message_event` that is one of `TextDeltaEvent`, `ThinkingDeltaEvent`,
  etc. Frontends must switch on the nested event, not on flat delta types.
  (Provider content ordering is preserved in canonical streams — do not reorder;
  see "preserve provider content ordering" fix.)
- `ToolExecutionStartEvent` carries `(tool_call_id, tool_name, args)`.
- `ToolExecutionUpdateEvent` carries `(tool_call_id, partial_result)` — a progress
  line, **not** a re-renderable partial result (lighter than Pi's `onUpdate`).
- `ToolExecutionEndEvent` carries `(tool_call_id, tool_name, result, is_error)`.

### 3.2 Session-own events (`tau_coding.events`)

`SessionAgentEndEvent` (`agent_end` + `messages` + `will_retry`),
`AgentSettledEvent`, `QueueUpdateEvent` (`steering`, `follow_up`),
`CompactionStartEvent`/`CompactionEndEvent` (with `reason`: manual | threshold |
overflow, `aborted`, `will_retry`, `error_message`), `EntryAppendedEvent`,
`SessionInfoChangedEvent` (name), `ThinkingLevelChangedEvent`,
`AutoRetryStartEvent` (`attempt`, `max_attempts`, `delay_ms`, `error_message`),
`AutoRetryEndEvent` (`success`, `attempt`, `final_error`).

### 3.3 Ordered thinking / content blocks (v0.2.0, issue #368, PR #371)

Assistant output is an **ordered sequence of typed content blocks** (thinking and
text, with provider signatures), persisted in session JSONL. This is the canonical
fix for thinking history/replay/ordering. Frontends must render thinking **in
position** from the message's ordered blocks, not reconstruct standalone thinking
rows from transient deltas (the old, buggy approach — see §8.6 / issue #275 and
the "thought process is different than Pi" issue #296: Pi interleaves thinking and
tool calls, and Tau must model that).

---

## 4. Frontend architecture (the adapter seam)

The single most important design decision (ADR 0001): **the TUI is split into a
Textual-free display core and a Textual shell**, so event→display logic is
testable without a terminal and so the frontend could be swapped.

```text
CodingSession.prompt()  →  CodingSessionEvent
        ↓
TuiEventAdapter  (tau_coding/tui/adapter.py)   — Textual-free
        ↓  mutates
TuiState         (tau_coding/tui/state.py)     — Textual-free display state
        ↓  rendered by
TranscriptView / widgets (tau_coding/tui/widgets.py, app.py)  — Textual
```

### 4.1 `TuiState` — display-only state

- `items: list[ChatItem]` — the durable visible transcript rows.
- `assistant_buffer: str` — the in-flight streaming assistant text.
- `running: bool`, `error: str | None`.
- `show_tool_results: bool` (Ctrl+O), `show_thinking: bool` (Ctrl+T).
- `queued_steering`, `queued_follow_up: tuple[str, ...]`.
- `skills`, and lazily-resolved renderers: `custom_renderer`,
  `tool_call_renderer`, `tool_result_renderer` (installed by the extension
  runtime; resolved **lazily at render time** so content restored before the
  runtime connects still picks up renderers on next redraw).
- `tool_spinner: str | None` — current spinner frame for an executing tool row.

**`ChatItem`** roles: `user`, `assistant`, `tool`, `error`, `status`, `thinking`,
`skill`, `branch_summary`, `compaction_summary`, `custom`. Carries optional
`tool_call_id`, `tool_result_text`, the raw `tool_result` object, `update_text`,
`tool_name`, `tool_arguments`, `started_at`, `always_show_tool_result`,
`custom_type`, `details`.

### 4.2 `TuiEventAdapter` — event → state

A pure function-per-event mapping with **no Textual import**. Key behaviors:

- `agent_start` → `running=True`, clear error. `agent_end`/`agent_settled` →
  flush assistant buffer, `running=False`.
- Nested `message_update` → append `TextDeltaEvent.delta` to `assistant_buffer`;
  `ThinkingDeltaEvent.delta` → `add_thinking_delta` (coalesce consecutive thinking
  deltas into one trailing thinking item).
- `message_end` (user) → `add_user_message` (with branch/compaction/skill
  compaction — §4.3). `message_end` (assistant): on `stop_reason in
  {"error","aborted"}` add an **error** item and stop; else add the assistant item.
- `tool_execution_start` → flush assistant buffer, `add_tool_call`.
- `tool_execution_end` → `record_tool_result`.
- `auto_retry_start` → a transient **status** row (`… <error>`).

### 4.3 `add_user_message` compaction (presentation-only)

`add_user_message` recognizes special user-authored messages and renders them
compactly, without changing what goes to the model:

- `custom_type` set → a `custom` item (rendered via the registered renderer, raw
  text as fallback).
- Branch-summary text → a collapsed `branch_summary` item ("Branch summary
  (Ctrl+O to expand)").
- Compaction-summary text → a collapsed `compaction_summary` item.
- A `/skill:<name>` invocation → a `skill` item plus a separate `user` item for
  any additional instructions.

### 4.4 Skill/tool presentation

- A `read` tool call whose path matches a loaded skill's path renders as
  `Loading skill: <name>` (role `skill`), not a raw read (issue #123, #51).
- Tool invocations render as terse human-readable lines: `read path:a-b`,
  `edit path`, `write path`, `$ command (timeout Ns)`; bash uses the invocation
  directly; others are prefixed `→ `. Unknown/extra args fall back to a truncated
  `name {args}` (≤160 chars).
- Tool results render as `✓ name` / `✗ name` plus a preview (§5.4) and, for
  successful `edit`, an inline unified-diff "Patch:" block.

---

## 5. Transcript rendering (Textual)

### 5.1 Widget-per-message, not one big RichLog (issue #156)

The transcript is a scroll container (`TranscriptView`, a `VerticalScroll`) of
individually selectable message widgets (`TranscriptMessageWidget`), **not** a
single `RichLog`. Each message widget owns selected-text extraction for its own
rendered block, so mouse selection stays scoped to one message and copying does
not bleed into neighbors (issues #19, #32, #58, #83, #150).

- **Streaming** assistant/thinking blocks use a `StreamingTranscriptMessageWidget`
  (a Textual `MarkdownStream`-backed widget) so Markdown renders incrementally
  without wrapper-driven selection flicker.
- **Non-streamed** blocks use `TranscriptMessageWidget` with a themed Rich
  renderable.
- Assistant text renders as Markdown (headings, bullets, quotes, links, inline
  code, emphasis). User/tool/status/error stay literal except explicit code/patch
  renderers. Fenced code uses Pygments when the language is known; **unknown
  fence labels fall back to plain code** rather than breaking (issue #66, manual
  check #4 in phase-23 notes).

### 5.2 Scroll-follow with user opt-out (issues #156, #175)

Toad-style scroll anchoring:

- Follow output (auto-scroll to bottom) **only while the user is at the bottom**.
- If the user scrolls up, **stop following** so they can review earlier context
  during a long stream (regression fixed in #177/#175).
- Scrolling back to the bottom re-engages follow. A user-driven turn or explicit
  jump-to-bottom forces follow.
- Implementation: `watch_scroll_y` clears `_follow_output` when scrolling up and
  sets it when reaching `max_scroll_y`; follow-scroll is deferred to
  `call_after_refresh` so layout settles first.

### 5.3 Text selection discipline (issues #150, #212, #213)

- **Disable text selection while streaming**; re-enable when idle
  (`_sync_text_selection_state`), because drag-selection during re-layout flickers
  and corrupts.
- A Markdown block only allows native selection **once mounted** (`allow_select`
  guard) — selection before mount crashes (issue #213).
- Copying wrapped lines must map rendered→source coordinates correctly across
  soft-wrapped visual lines (issue #150, fixed in #154).

### 5.4 Tool-result previews (issue #102, #30, #18)

- Collapsed by default; **Ctrl+O** toggles full output.
- Preview limits: tool result ≤ 8 lines / 2000 chars; edit patch ≤ 32 lines;
  interactive bash output ≤ 120 lines. Truncation appends a hint:
  `[Preview only: N more lines, additional text hidden from the TUI.]`
- The full result is always kept in the durable session for model context/replay.

### 5.5 Activity indicator (issues #35, #50, #64, #82)

- A spinner row **directly above the prompt** (not in a top status line), with a
  slow fade between accent colors (`ACTIVITY_TICK_SECONDS = 0.15`, blended over 24
  steps). It resets to idle on complete/cancel/fail.
- Tool rows show a braille spinner frame standing in for the static `→ `/`▸ `
  marker while executing, plus a live elapsed time once a run exceeds 1s (quick
  reads/edits never flash `(0s)`).

### 5.6 Footer & status (issues #61, #65, #75, #86)

- The built-in **Textual Footer** is the single shortcut-hint surface — do not add
  a custom hint row. It swaps binding sets: normal / completion-open / running.
- **No top "ready/queued" status bar** (removed in #86) — queue feedback lives in
  the stacked queued-messages row above the prompt (§6.6), and the header carries
  session identity (§9.1).

---

## 6. The prompt input

### 6.1 Multiline TextArea with completion bindings

`PromptInput` is a `TextArea` (not a single-line `Input`) supporting
**Shift+Enter** for newline (issue #17, #25). It exposes `value`/`cursor_position`
compatibility aliases and merges a base binding map with mode-specific binding
maps (`normal`/`completion`/`running`) that drive the footer.

### 6.2 Large-paste placeholder (issues #211, #300)

- A bracketed paste over **2000 chars** is not rendered inline. Insert a compact
  placeholder: `[Pasted content #N: X,XXX characters, Y lines, Z.Z KB]`.
- The full content is stored and **substituted back at submit**
  (`text_for_submission`).
- **Invalidate stored content if the placeholder is edited away** so stale content
  is never sent (`sync_pending_paste`). Cleared on submit/clear.

### 6.3 Shell mode highlighting (issues #49, #146)

`!`/`!!` prefixes highlight in the accent color (the input gets a `-shell-mode`
class and the prefix span is stylized). `! cmd` runs and records output **in
context**; `!! cmd` runs without adding to context. Rendered like a tool call:
immediate `$ cmd` running row, then `✓/✗ bash · added/not added to context` plus a
≤120-line output preview (issue #146).

### 6.4 Prompt history recall (issue #172)

**Up on an empty prompt** recalls the most recently submitted prompt for editing.
Guard: only into an empty input (so an accidental Up doesn't erase an in-progress
draft). History is reseeded from restored `UserMessage`s on resume.

### 6.5 Queued-message recall (issues #305, #307)

**Up on an empty prompt while running** pulls the latest queued message back for
editing. Follow-ups take priority; if none, pop a steering message
(`pop_latest_follow_up_message` then `pop_latest_steering_message` on the
session). The bug was that steering recall was missing — both must be supported.

### 6.6 Queued messages display (issues #28, #53, #127)

Pending steering/follow-ups stack above the prompt as `↪ steering · queued: …` /
`↪ follow-up · queued: …` rows (shortened wording per #127). Editable per §6.5.

### 6.7 Focus behavior

Clicking anywhere returns focus to the prompt — **except** while an extension
main view is open (a subagent viewer owns the main area and its keyboard; yanking
focus would reroute Esc/typing to the chat). Clicking the prompt itself always
focuses natively.

---

## 7. Slash commands & command output

### 7.1 Command set (Pi-aligned, pruned in #95)

`/quit` (alias `/exit`), `/new`, `/session`, `/system`, `/compact [instructions]`,
`/export [--format html|jsonl] [dest]`, `/resume [session-id]`, `/tree`,
`/name <new name>`, `/model`, `/scoped-models`, `/theme [name]`,
`/login [provider]`, `/logout [provider]`, `/reload`, `/hotkeys`, and dynamic
`/skill:<name> [request]`.

### 7.2 Unknown slash input passes through (issues #350, #351)

Only **registered** commands are consumed locally. Any other slash-prefixed input
— including absolute paths like `/tmp/x` or `/Users/me/file.png` — is sent to the
model as a normal prompt. (Root cause: a leading-`/` filepath was parsed as a
command, not found, and **silently discarded**.) Do not reject unknown commands;
pass them through.

### 7.3 `/skill:` is prompt expansion, not a command

`/skill:<name>` expands the named skill into the prompt and runs it as a turn. It
is a prompt-expansion path, deliberately **not** in the command registry.

### 7.4 Command output routing (issues #121, #122, #124, phase-23)

Command output is **transient UI**, not transcript content:

- Short confirmations → a Textual **notification** (e.g. `/name` → "Session
  renamed: …"; no notification for "new session started" or thinking toggles).
- Multi-line reference output (`/session`, `/hotkeys`, etc.) → a **dismissible
  modal** (`CommandOutputScreen`).
- Only `/reload` and `/system` render **inline** in the transcript
  (`_command_message_uses_transcript`).

### 7.5 Notification de-duplication (issue #81)

Identical `(message, severity)` notifications must not stack. Track active
notification keys; suppress a repeat until the existing one expires
(`NOTIFICATION_TIMEOUT`).

### 7.6 Busy-state guards

- `/tree` while the agent is running → show the friendly message
  `"Tau is still working. Press Escape to interrupt before using /tree."` and
  restore the draft — **not** an internal error (issue #277; the recursion crash
  was separately fixed by iterative tree traversal, #298).
- `/compact` while running/queued → notify to wait, restore the draft. While a
  compaction is already active → notify and restore.

---

## 8. Streaming, submission, and perceived-latency fixes

These are the subtle, hard-won behaviors. Recreate them faithfully.

### 8.1 Optimistic user-message rendering (issues #166, #271, #272)

The transcript is event-authoritative, which made submission feel laggy in long
threads (the user row only appeared after the harness emitted the durable
`MessageEndEvent`, after persistence). Fix: **optimistically append** the
submitted user message immediately, **incrementally** (append a widget, not a full
redraw), then **dedupe** the later confirmed user `MessageEndEvent` so it isn't
rendered twice.

Rules:

- Only optimistic-render **plain prompts** — text that is non-empty and does
  **not** start with `/` (`_should_optimistically_render_prompt`). Slash-command
  prompts (custom-prompt expansions) render only from the confirmed event,
  avoiding duplicate raw+expanded rows (issues #278, #282).
- **Custom messages are never optimistic** (their `custom_type`/`details` arrive
  only on the confirmed event; an equality-based dedupe would double-render).
- Dedupe matches on **exact content equality** for the same run id
  (`_consume_optimistic_user_event`).
- **Extension `input` hooks can transform** the text, so the confirmed message may
  differ from the optimistic one. Reconcile by **rewriting the optimistic item in
  place** (`_replace_transformed_optimistic_user_message`) instead of appending a
  second row. This runs after exact-match, and is safe because the run's own
  prompt confirmation is its first user event — a queued steering/follow-up can
  never be mistaken for it.
- Track optimistic messages per **run id**; clear a run's unconfirmed optimistic
  messages when it ends. A stale run id must stop applying events (guards against
  late events from a cancelled worker mutating a new run).

### 8.2 Incremental streaming updates (avoid full redraws)

`_apply_streaming_transcript_event` applies each event to mounted widgets without
remounting the transcript:

- text/thinking delta → `append_assistant_delta` / `append_thinking_delta`;
- `message_end` (assistant) → `finish_assistant_message`;
- `tool_execution_start` → finalize the active assistant block, append the tool
  item;
- `tool_execution_update` → finalize assistant, update the matching tool item;
- fall back to `_refresh()` (full redraw) only when the transcript isn't mounted
  (e.g. a modal is on the screen stack) or for events that need chrome only.

Finalize any active streaming block before starting another block (thinking →
assistant → tool), so interleaving renders in order.

### 8.3 Throttle streaming updates (issue #322)

Batch high-frequency assistant/thinking deltas behind a short timer, and **flush
pending deltas immediately at message/tool/run boundaries** to preserve ordering.
Prevents per-token re-layout churn on long streams.

### 8.4 Thinking-toggle performance & ordering (issues #248, #275, #256)

- **Ctrl+T must be O(thinking blocks), not O(transcript).** Use a targeted
  `update_thinking_visibility` that removes/re-inserts only thinking widgets and
  preserves scroll position when not following — never a full rebuild.
- **Ordering bug (#275):** after toggling, thinking blocks must remain **in their
  original position** (between the user turn and the assistant turn that produced
  them), not jump to the bottom. Reinsert thinking widgets **before** the correct
  non-thinking sibling, and render a single collapsed "hidden thinking"
  placeholder when `show_thinking` is off.
- Streaming thinking goes to the active thinking widget; a finalized thinking
  block stays put.

### 8.5 Cancellation is not an error (issues #96, #99, #47)

- **Esc** is a two-step flow: first press requests graceful cancellation via
  `session.cancel()` (provider/tools observe a cancellation token, e.g. `bash`
  honors it); second press interrupts the worker immediately.
- Render the terminal cancellation as **status text**, not an error row.
- Ignore late events from a cancelled worker (run-id guard, §8.1).

### 8.6 Interleaved thinking/tool calls (issue #296)

Pi runs tool calls **inside** the thought process; Tau must model thinking and
tool calls interleaved in one assistant turn. Render each in stream order and
persist ordered content blocks (§3.3). Do not force all thinking to a single
leading block.

---

## 9. Chrome: header, sidebar, footer, terminal

### 9.1 Header & terminal tab title (issues #244, #260, #304)

- Header shows `Tau` + session name (`Tau — <name>`, default "Untitled session").
  Keep it a **single row**: Textual's `Header` toggles a `-tall` (3-row) class on
  click, which makes the sidebar jump — override it to stay one row (issue #336).
- Terminal tab title via OSC 0 (`terminal_title.py`): `τ` when idle/untitled,
  `τ | <name>` for named sessions, and a braille `⠋ …` running frame while active.
  Sanitize control bytes, cap length (120), skip when `TERM=dumb`, non-tty, `CI`,
  or `TAU_TERMINAL_TITLE` is off. Restore a neutral title on exit. Writes are
  deduped and best-effort (disable on stream failure).

### 9.2 Sidebar (issues #29, #30, #37, #38, #279)

- Shows provider/model, thinking mode, tools, skills, prompt templates, context
  files (AGENTS.md), and a context estimate. Logo `τ = 2π`.
- **Responsive**: auto-hide below a min width/height (`SIDEBAR_MIN_WIDTH=96`,
  `SIDEBAR_MIN_HEIGHT=24`); show a `CompactSessionInfo` single-line row under the
  prompt on narrow layouts.
- `sidebar_position` in `tui.json`: `left` (default) / `right` / `off`.

### 9.3 Layout skeleton (CSS ids)

`Header`, `#workspace` → `#sidebar` + `#main-pane`; `#main-pane` → `#transcript`
(or extension `#main-slot`), `#above-prompt-slot`, `#queued-messages`,
`#compact-session-info`, `#prompt-row` (`#prompt-prefix` + `#prompt`),
`#below-prompt-slot`, `#autocomplete`, `Footer`. Extension slots (`#main-slot`,
`#above/below-prompt-slot`) are the component seam (§12).

---

## 10. Autocomplete

### 10.1 Completion sources (`build_completion_state`)

- Slash commands, `/skill:<name>` (and other `:`-namespaced), prompt templates
  (invoked by filename, no slash), `@file` references, shell path completion after
  `!`/`!!`, and **argument values** for `/model`, `/login`, `/theme`, `/resume`.
- `@` file search skips hidden/generated dirs (`.git`, `.venv`, `node_modules`,
  `__pycache__`, `build`, `dist`, `.tau`, caches), caps at 50 results (issue #48).

### 10.2 Behavior rules

- **Hide after argument text starts**: once a custom-prompt/skill command is
  followed by a space and argument text, stop showing suggestions (issue #173).
- **Alias/prefix ordering**: prioritize alias matches correctly (issue #114).
- **Enter applies, doesn't submit**: pressing Enter while a highlighted completion
  would change the prompt applies it; it does not submit (phase-23).
- **Stable height while typing** (issue #299): size the suggestion window from a
  line budget (max ~16 visible lines, fraction of terminal height, min transcript
  lines preserved) so it doesn't shrink/grow per keystroke (also #206 viewport
  sizing).
- `/model` can open a modal picker; `/resume` suggests indexed session ids (with
  title/model/cwd metadata, newest-first).

---

## 11. Pickers and modals

All are `ModalScreen`s with consistent keyboard nav (Up/Down, Enter select, Esc
cancel) and themed `ListView` highlight.

- **SessionPickerScreen** (Ctrl+R, `/resume`) — indexed sessions w/ metadata.
  Selecting in `/session` output auto-copies selected text (issue #268, #270).
- **TreePickerScreen** (`/tree`) — branch from an earlier entry; offers
  branch-with-summary and custom-summary instructions
  (`BranchSummaryInstructionsScreen`); can toggle tool calls. Selecting a **user
  message** restores history to just before it and **prefills the input** with its
  text rather than replaying it (issue #143).
- **ModelPickerScreen** (`/model`) — two modes (all / scoped) toggled with Tab;
  **Space** toggles a model into the scoped list; search input. (Issues #21, #33,
  #60.)
- **ThemePickerScreen** (`/theme`).
- **Login flow** — `LoginProviderPickerScreen` (searchable), `LoginScreen`
  (API key), `OAuthLoginScreen` (device-code / auth-url / manual-code / select
  prompts), `CustomProviderLoginScreen` (multi-field, focus-next navigation),
  `LoginMethodPickerScreen`. Anthropic uses explicit aliases
  `anthropic-subscription` / `anthropic-api` (issue, PR #372). Keyboard navigation
  through the whole flow was a specific fix (#6c14f839).
- **Extension dialogs** — `ExtensionSelectScreen`, `ExtensionConfirmScreen`,
  `ExtensionInputScreen` (async, future/callback-based — not `push_screen_wait`,
  which needs a worker context; see §12).

---

## 12. Extension system UI surface

The extension system (phase-21, PR #320, #366) touches the TUI through deliberate
seams. A recreated TUI must provide:

- **UI bridge** (`_TuiExtensionUiBridge`): `select`/`confirm`/`input` (async,
  `push_screen` + `asyncio.Future`, timeout in seconds, no-op defaults on
  timeout) and `notify`. Installed via `runtime.set_ui_bridge(...)` in `on_mount`,
  **then** `session.emit_pending_session_start()` (idempotent) so `session_start`
  handlers can use dialogs/notifications.
- **Renderers installed into `TuiState`**: `custom_renderer`,
  `tool_call_renderer`, `tool_result_renderer` — resolved lazily (§4.1). Malformed
  markup never crashes the frontend (`Text.from_markup` under a guard, fallback to
  literal).
- **Component seam** (adopted via the component-seam experiment): slot widgets and
  a main-area view. `#main-slot` replaces the transcript when an extension main
  view is open; `#above/below-prompt-slot` host small widgets. Widget type is
  Textual `Widget`; prefer strings where they suffice. While a main view is open,
  don't steal focus to the prompt (§6.7).
- **Key interceptors**: pre-dispatch key handlers registered by extensions.
- **Reload lifecycle ordering** (PR #366): await `session_shutdown` on the
  outgoing generation **before** clearing host UI, then invalidate generations,
  purge `tau_extension_*` modules, re-import, re-run `setup`, rebuild tools and
  the command registry, re-subscribe, and emit `session_start(reason="reload")`.
  Every `ExtensionAPI`/`context` read checks a generation token and raises on a
  stale generation so orphaned background tasks fail loudly.
- **Failure isolation**: a raising extension handler/widget is quarantined and
  recorded as a diagnostic; dispatch continues (fail-safe only for `tool_call`,
  which blocks the tool). A broken extension never crashes the session.

---

## 13. Startup, settings, and robustness

### 13.1 Startup model/provider resolution (`run_tui_app`)

- `--resume` and `--new-session` are mutually exclusive (validated).
- Resolve provider/model from explicit flags, then a resumed record
  (`_selection_from_session_record`: provider+model **atomically**; legacy records
  missing provider fall back to a scoped match or any credentialed provider that
  lists the model), then the default — but only if it has usable credentials, else
  the first usable provider (`_first_usable_startup_selection`).
- **Bad `--model` → clean error, not a traceback** (issue #265): catch
  `ProviderConfigError` and print an actionable message.
- If no provider authenticates, open the TUI anyway with a
  `LoginRequiredProvider` placeholder that surfaces "Login required…" as a
  provider error on first prompt.

### 13.2 Deferred session creation (issues #119, #142, #179)

Do **not** create the JSONL transcript or index entry on startup/`/new` — only on
the **first durable session mutation** (first message). Opening Tau just to run
`/resume` must not leave an empty resumable session. (Regressed once as #179 —
guard it.)

### 13.3 Release notes & update check must never crash startup (issues #313, #339)

- `releases.json` is **bundled inside the package** (`src/tau_coding/data/release-notes/`)
  — a wheel that omits it caused `FileNotFoundError` at startup.
- Loading release notes is **guarded**: a missing/malformed file is treated as
  empty notes (a quiet no-op), matching the docstring. Startup proceeds.
- The PyPI update check caches in `~/.tau/cache/update-check.json`, refreshes ≤
  once/day, and is skipped under `CI` or `TAU_NO_UPDATE_CHECK=1`.

### 13.4 TUI settings (`~/.tau/tui.json`) with strict validation

- Fields: `theme`, `keybindings`, `auto_copy_selection`, `sidebar_position`.
  Unknown fields, unknown theme/key names, empty keys, and **duplicate key
  assignments** are rejected (fail early). Legacy keybinding names are tolerated.
- **One theme system** (issue #281, PR #319): Textual's native theme registry is
  constrained to Tau's themes (`tau-dark`, `tau-light`, `high-contrast`) and maps
  to the same durable `theme` setting — no second, non-persistent theme engine.
  Tau themes feed both Textual CSS variables and Rich renderers for consistency.
- The CLI must **force UTF-8** on stdout/stderr (Windows cp1252 crashes on
  non-ASCII model output; issues #285, #290) — reconfigure non-UTF-8 streams at
  import, `errors="replace"`.

### 13.5 Session index & storage robustness (issues #354, #360, #301, #269)

- **JSONL split on `\n` only**, never `str.splitlines()` — U+2028/U+2029/U+0085
  are written unescaped into JSON strings and would split a valid line, bricking
  the session on next load (PR #354).
- **Append-only index upsert**: the read-filter-rewrite `_upsert` raced with
  concurrent Tau instances in the same folder, silently dropping entries (PR
  #360). Append one record per write; dedupe last-writer-wins at read time.
  Recover orphaned sessions (file exists but no index entry).
- **Persist interrupted tool repairs on resume** (PR #269): dangling tool calls
  from a cancelled run are repaired and persisted so resume doesn't break.
- **Migrate null usage costs** at the persistence boundary (legacy sessions).
- Auto-naming failures must be **non-fatal** to the turn and must not block
  persistence (issues #306, #312).

### 13.6 Provider/model consistency on navigation (issues #226, #247, #249, #258, #261, #353, #356)

- Restore **provider+model atomically** on `/resume` and `/tree`; validate
  compatibility before switching; never combine a model from one provider with
  another provider. Legacy records without a provider preserve the current one.
- `/tree` rewind must **not** apply historical model changes to runtime config —
  preserve the currently active model (keep replayed state for transcript rebuild
  only).
- **Ignore orphaned provider preferences** (saved prefs for providers no longer
  configured) rather than erroring (PR #353).
- **Remember thinking level per scoped model** (`thinking_defaults` in
  `providers.json`) and **apply it at startup** for the resolved model — including
  validating it against the model's available levels (issue #356: a saved `xhigh`
  default was ignored and the session fell back to an invalid `medium`).

---

## 14. Platform & environment edge cases

- **CJK IME input** (issue #337): require **Textual ≥ 8.2.8**. Earlier versions
  mis-parse kitty-keyboard IME commits, inserting escape garbage like
  `[49;;20320:22909u` instead of `你好`. Pin the floor in `pyproject.toml`.
- **Windows** (issues #216, #285, #290, #325, #326): Python ≥ 3.12; force UTF-8
  streams (§13.4); tests must never touch the real `~/.tau` (isolate to tmp);
  path/shell differences covered in `test_coding_session`.
- **SOCKS proxies** (issues #221, #303): normalize `socks://` → `socks5://` before
  creating httpx clients (httpx rejects the generic scheme); honor
  `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY`.
- **Provider retries** (issues #34, #333, #334): retry transient errors; Anthropic
  must retry 425, 529, and 5xx above 504 like other providers. Retry surfaces as
  `auto_retry_start` status rows.
- **Drag-and-drop files into the input** (issue #170): open requirement.

---

## 15. Configuration surface (durable files)

The TUI reads/writes (atomically, with `.bak` backups, reloading before a targeted
change):

- `~/.tau/tui.json` — theme, keybindings, `auto_copy_selection`, `sidebar_position`.
- `~/.tau/providers.json` — default provider/model, scoped models,
  `thinking_defaults`, headers, timeout/retry. Scoped models persist for Ctrl+P.
- `~/.tau/credentials.json` — API keys/OAuth (0600, atomic). Resolution: stored
  credential → env var. `/login` writes here; `/logout` removes only saved creds.
- `~/.tau/catalog.toml` — user provider/model overlay (user-level only; **no
  project-level catalog** so cloning a repo can't redirect `base_url`/credentials).
- `~/.tau/settings.json` — `shellCommandPrefix` for aliases (§6.3 note).
- `~/.tau/sessions/<cleaned-path>-<hash>/` — per-project session transcripts + index.

---

## 16. Default keybindings (remappable)

| Key | Action |
| --- | --- |
| `Enter` | Submit (or apply highlighted completion); while running → queue steering |
| `Shift+Enter` | Newline |
| `Alt+Enter` | Queue follow-up |
| `Esc` | Cancel run (two-step) |
| `Up` (empty prompt) | Recall last prompt; while running → edit latest queued message |
| `Tab` | Accept completion |
| `Up`/`Down` | Move completions |
| `Ctrl+K` | Command palette |
| `Ctrl+R` | Session picker |
| `Ctrl+P` | Cycle scoped models |
| `Shift+Tab` | Cycle thinking level |
| `Ctrl+T` | Toggle thinking display |
| `Ctrl+O` | Toggle full tool output |
| `Ctrl+C` | Clear prompt |
| `Ctrl+D` | Quit |

`Shift+Tab` may arrive as `backtab` on some terminals — accept both
(`_is_thinking_cycle_key`).

---

## 17. Performance requirements (regression-prone)

1. **Submission** feels instant in long threads → optimistic incremental render (§8.1).
2. **Streaming** doesn't re-layout per token → throttle + targeted widget updates (§8.2, §8.3).
3. **Ctrl+T** scales with thinking blocks, not transcript length (§8.4).
4. **Ctrl+P scoped-model switch** is near-instant — avoid re-rendering/re-initializing
   more than needed (issues #263, #266, #264).
5. **Code blocks** don't flicker scrollbars while streaming (issue #254, #255) —
   hide horizontal scrollbars during stream; stabilize on finalize. Horizontal
   scrolling of code blocks must work when not streaming (issue #190).
6. **Autocomplete** window keeps stable height while typing (§10.2).
7. **/tree and session traversal** are **iterative**, never recursive (issue #277,
   PR #298) — long sessions must not hit `RecursionError`.

---

## 18. Testing strategy

- **Adapter/state are Textual-free** — test event→state mapping without a terminal
  (`test_tui_adapter.py`).
- **App-level** tests drive `TauTuiApp` with a fake session/provider
  (`test_tui_app.py`): optimistic render/dedupe/transform, streaming increments,
  cancellation, queued recall, paste placeholders, pickers, completion stability,
  thinking-toggle ordering.
- **Widget/markdown** tests (`test_tui_components.py`, `test_tui_autocomplete.py`,
  `test_tui_config.py`).
- **Layering** tests assert `tau_agent` has no Textual/app imports.
- Reference manual-validation checklist in `dev-notes/architecture/phase-23-tui-polish.md`.

---

## 19. Open requirements / future work (tracked)

- Custom TUI support / frontend swapping (issue #205) — the adapter seam is the
  foundation; this PRD's contract (§2) is the stable surface.
- Drag-and-drop files into the input (issue #170).
- Command palette, diff viewer, dedicated patch slot (issue #323, PR #324).
- Configurable context-usage display (PR #359).
- Desktop notifications via OSC 9 (PR #349).
- Data-driven JSON themes with user/project discovery (PR #374).
- `--continue`/`-c` resume flag (PR #331).
- Pluggable session storage as a public extension point (issue #341).
- Raw LLM API observability (issue #110).

---

## 20. Appendix: edge-case → fix index

| # | Edge case | Fix |
|---|---|---|
| 271/272 | Submit lag in long threads | Optimistic incremental user render + dedupe |
| 278/282 | Custom-prompt shown twice | Skip optimistic render for `/` prompts |
| 166 | Display blocked on persistence | Decouple display from durable event |
| 337 | CJK IME garbled | Require Textual ≥ 8.2.8 |
| 305/307 | Up doesn't recall steering | Pop steering queue too |
| 275 | Thinking jumps to bottom on toggle | Reinsert in position; targeted update |
| 248/256 | Ctrl+T slow in long threads | O(thinking) targeted update |
| 254/255 | Code-block scrollbar flicker | Hide scrollbar while streaming |
| 263/266/264 | Ctrl+P provider-switch lag | Lightweight switch, less re-render |
| 265 | Bad `--model` traceback | Clean actionable error |
| 247/249/258/261 | Provider/model mismatch on /tree,/resume | Atomic validated restore; preserve active model |
| 226 | Unavailable auto-selected model | Validate + better provider error detail |
| 353 | Orphaned provider preferences | Ignore gracefully |
| 356 | Saved thinking level ignored at startup | Apply + validate per-model default |
| 350/351 | `/path` treated as command | Pass unknown slash input through |
| 336 | Header click layout jump | Keep header single-row |
| 339/313 | Missing releases.json crash | Bundle file; guard load |
| 354 | JSONL split on U+2028 bricks session | Split on `\n` only |
| 360 | Concurrent index writes drop entries | Append-only upsert; recover orphans |
| 269/301 | Interrupted tool repair lost on resume | Persist repairs |
| 306/312 | Auto-naming failure blocks turn | Non-fatal naming |
| 325/326 | Windows tests touch real ~/.tau | Isolate to tmp |
| 285/290 | Windows cp1252 crash | Force UTF-8 streams |
| 221/303 | socks:// proxy unsupported | Normalize to socks5:// |
| 333/334 | Anthropic 529/5xx not retried | Retry like other providers |
| 296 | Thinking not interleaved w/ tools | Ordered content blocks (§3.3) |
| 277/298 | /tree RecursionError | Iterative traversal + friendly busy message |
| 179/119/142 | Empty sessions persisted | Defer creation to first message |
| 150/154 | Copy broken on wrapped lines | Correct rendered→source mapping |
| 175/177 | Scrollback locked while streaming | Follow only at bottom |
| 173 | Autocomplete lingers into args | Hide after argument starts |
| 172 | No prompt recall | Up recalls last prompt into empty input |
| 211/300 | Huge paste floods input | Placeholder + restore-on-submit |
| 81 | Duplicate notifications stack | Dedupe active keys |
| 86 | Redundant top status bar | Remove; queue feedback above prompt |
| 143 | Tree replays user msg mid-turn | Restore history + prefill input |
| 146 | `!` commands look foreign | Render like tool calls |
| 125 | Can't change thinking while busy | Allow; applies to next turn |
| 120 | OpenAI reasoning hidden | Surface reasoning deltas to Ctrl+T |
| 281/319 | Two theme systems | Unify on Tau themes, persist |
| 366 | Extension reload ordering | Await shutdown before UI teardown |
| 164 | Input disabled during compaction | Allow typing; guard submit |
| 322 | Per-token re-layout churn | Throttle + boundary flush |
