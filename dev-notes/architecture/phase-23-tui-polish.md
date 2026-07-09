---
title: "Phase 23: Advanced TUI and Product Polish"
---

Phase 23 improves the Textual frontend while keeping the reusable agent harness
independent of UI concerns.

The boundary remains:

```text
CodingSession emits AgentEvent values
        ↓
TuiEventAdapter updates TuiState
        ↓
Textual widgets render the transcript and controls
```

## Current polish slices

Live tool results now render successful output previews in the transcript,
matching restored session history. The TUI shows the first few lines and a
preview hint when additional content is hidden, so large `read` or `bash`
results do not flood the conversation while the durable session still keeps the
complete tool result for model context and replay.

Transcript blocks now render fenced code and persisted edit patches with Rich
syntax renderables inside the same Pi-style stacked message blocks. The
transcript state still stores plain role/text items; this is renderer-only
polish in the Textual frontend.

The transcript surface is a scroll container of selectable message widgets, not
one large `RichLog`. Each message widget owns selected-text extraction for its
rendered block, so partial mouse selection stays scoped to the intended message
and adjacent-message copies do not accidentally expand to the full
conversation.

Assistant transcript blocks now also render common Markdown constructs such as
headings, bullets, blockquotes, links, inline code, and emphasis through Rich
Markdown. User, tool, status, and error blocks stay literal unless they are
handled by the explicit code or patch renderers, which keeps pasted prompts and
tool output predictable.

Live `edit` tool results now include their unified patch in the tool block. This
provides an inline diff view for file edits while keeping the event adapter and
Textual widgets decoupled. Tool-result metadata is now preserved in
`ToolResultMessage`, so restored session history can render the same edit patch
blocks from persisted JSONL entries.

The TUI also has a command-palette entry point. Pressing `Ctrl+K` focuses the
prompt, inserts `/`, and shows all slash-command completions using the existing
completion engine. Selection uses the same `Tab`, `Up`, and `Down` bindings as
ordinary slash-command autocomplete. Pressing `Enter` while a highlighted
completion would change the prompt now applies that completion without
submitting the prompt, matching common terminal picker behavior.

Slash-command output is now transient UI instead of transcript content. Short
command results use Textual notifications, and multi-line output such as
`/help`, `/skills`, `/resume`, `/status`, `/resources`, and `/context` opens a
dismissible modal. This keeps command reference material out of the agent
conversation while preserving access to the information.

The same completion engine now suggests available values for `/model` and
`/login` arguments. `/model` can also open a modal picker for configured
provider/model choices, while `/login` remains the path for adding providers.

The prompt also suggests indexed session ids for `/resume <session-id>`, and
plain `/resume` opens the same modal session picker as the session-picker
keybinding. Those rows include session metadata such as title, model, and
working directory while preserving newest-first order for the current project
from `SessionManager`. Submitting the command reloads the selected session
through `CodingSession` and rebuilds the visible transcript in place.

The TUI also has a small modal session picker bound to `Ctrl+R` by default.
It lists indexed sessions with the same metadata used by resume completions,
then resumes the selected session through `CodingSession.resume()`. The picker
lives entirely in the Textual frontend; the portable harness still has no
session-selection policy.

The built-in Textual frontend now reads optional keybinding settings from
`~/.tau/tui.json`. This lets users remap the command palette, completion
navigation, session picker, cancellation, and quit keys while keeping the
configuration in `tau_coding.tui` instead of the reusable agent harness.

The same TUI settings file now supports named built-in themes. `tau-dark`
remains the default, and `high-contrast` provides a brighter dark palette. The
default theme is inspired by Toad's Textual UI: a darker surface, transparent
chrome, muted separators, a focused bottom prompt, and stacked conversation rows
with slim left accents instead of boxed cards. Theme selection feeds Textual CSS
variables plus Rich transcript/sidebar renderers, so the app chrome and message
blocks stay visually consistent without adding UI policy to `tau_agent`.

The sidebar is now responsive. It remains visible on medium or larger terminal
windows, but hides automatically when the terminal is narrow or short so the
conversation and prompt keep enough room to breathe. The visibility rule lives
in the Textual frontend; session metadata and agent state are unchanged.
When visible, the sidebar includes loaded context files so project instructions
such as `AGENTS.md` are inspectable without opening a separate command modal.

The status line now shows a small animated activity indicator while a run is
active and resets to `Ready` when the run completes, is cancelled, or fails. It
also shows pending steering/follow-up queue counts while queued prompts are
waiting to be injected.

Thinking controls now include two distinct TUI behaviors: `Shift-Tab` cycles the
active thinking mode when the current provider/model supports it, while `Ctrl-T`
toggles display of streamed thinking tokens. Thinking-token transcript blocks
are hidden by default and rendered with their own role style when shown.

Model controls now include a Pi-style scoped-model flow. `/model` still opens
the provider/model picker, but `Space` toggles the highlighted model into the
persisted scoped list and `Tab` switches the picker between all models and
scoped models. `Ctrl-P` cycles through the scoped list directly from the prompt.
The TUI asks `CodingSession` to mutate and cycle this list; Textual does not
read or write provider settings itself.

The activity indicator now lives in a stable row directly above the prompt
instead of in the top status line. This keeps the bottom input area visually
active while an agent turn is running, and leaves the top status area focused on
provider, model, queue, and session state.

The Textual footer now carries Tau's shortcut hints through ordinary visible
bindings. It describes the active submission, newline, picker, thinking,
follow-up, and prompt clear shortcuts, switches to autocomplete-focused bindings while
completions are open, and switches again while an agent turn is running. Tau
does not add a separate custom hint row; the built-in bottom toolbar remains the
single shortcut surface.

The TUI treats `Esc` as a two-step cancellation flow. The first press requests
graceful cancellation through `CodingSession.cancel()` and leaves the active
worker visible while the provider or tool observes the cancellation token. The
second press interrupts the current Textual worker immediately. This mirrors
Pi's cancellation boundary: UI code requests cancellation, the portable agent
loop carries a cancellation token, and long-running tools such as `bash` honor
that token without making Textual a dependency of `tau_agent`. Because
cancellation is an intentional user action, the built-in TUI renders the final
cancellation event as status text instead of adding an error row to the
transcript.

Assistant code block rendering is now more defensive. Known fence languages use
Rich/Pygments syntax highlighting, while unknown or custom fence labels fall
back to plain code rendering instead of producing a broken transcript block.

The built-in theme set now includes `tau-light` alongside `tau-dark` and
`high-contrast`. Theme choice stays in `tau_coding.tui` configuration and feeds
Textual CSS variables plus Rich renderers without leaking UI policy into the
portable harness. Textual's native theme registry is constrained to these same
Tau themes, so Textual's menu/command-palette theme entry changes Tau's durable
`~/.tau/tui.json` setting instead of becoming a second, non-persistent theme
system.

Sessions can now be renamed from the TUI with `/name <new name>`. The command
updates the indexed session metadata used by `/resume`, resume completions, and
the session picker; the underlying append-only transcript remains the durable
source of conversation events.

The frontend boundary is now documented in [Building a Custom TUI](../custom-tui.md).
That guide describes how another terminal UI can consume `CodingSession`,
`AgentEvent`, `TuiState`, and `TuiEventAdapter` without coupling to Textual
internals.

## Manual validation checklist

These checks exercise the Phase 23 polish in the Textual TUI. Use a clean
worktree at `origin/main` so local experimental branches do not affect the
result:

```bash
git fetch origin
git worktree add /tmp/tau-tui-validate origin/main
cd /tmp/tau-tui-validate
uv run tau
```

1. Check `/name` by starting a session, running `/name Manual validation`, then
   opening `/resume`. The renamed session should appear in the resume picker and
   in `/resume <session-id>` completions.
2. Check the working indicator by submitting a prompt that takes a few seconds.
   The prompt border should slowly fade between activity colors while the turn
   runs, without a separate spinner row above the prompt.
3. Check shortcut hints in the built-in bottom footer. It should show prompt
   actions such as submit, newline, commands, sessions, thinking, clear, and
   quit. Open slash-command autocomplete and confirm the same footer switches to
   complete, choose, and close actions. Submit a prompt that takes a few seconds
   and confirm the footer switches to steer, follow-up, cancel, thinking, and
   tools actions. There should not be a second custom shortcut row above the
   footer.
4. Check code block rendering by asking the model for one fenced `python` block
   and one fenced block with an unknown language such as
   `not-a-real-language`. The Python block should be highlighted, and the
   unknown-language block should render as plain code without breaking the
   transcript.
5. Check the light theme by writing this file:

   ```json
   {
     "theme": "tau-light"
   }
   ```

   to `~/.tau/tui.json`, restarting `uv run tau`, and confirming the app uses a
   light palette with readable transcript, sidebar, footer, and prompt colors.
   Restore your preferred theme after the check.

## Boundaries

These changes live in `tau_coding.tui`. The command registry still owns command
metadata, and `tau_agent` remains unaware of Textual, keybindings, slash
commands, and rendering.

## Still deferred

Phase 21 extensions remain intentionally unimplemented. Future polish may add
more advanced picker surfaces, but the current Phase 23 checklist items now have
foundational implementations.

## Tests

Coverage lives in:

```text
tests/test_tui_adapter.py
tests/test_tui_app.py
tests/test_tui_config.py
```
