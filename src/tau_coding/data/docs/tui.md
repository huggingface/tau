# Tau TUI

Tau's interactive interface uses Textual behind an adapter boundary. The
portable `tau_agent` harness emits provider-neutral events; the TUI renders
them and owns interaction. Ctrl+P cycles forward through scoped models;
Shift+Ctrl+P cycles backward.

## `/system`

`/system` displays the effective prompt as read-only transcript Markdown and
labels each contiguous section with its source. Every section has a separate
faint background matching its theme-colored source name and border. File-backed
prompt overrides, append files, project instructions, and skills show their
paths; extension sections and runtime values show their owning component. The
command does not add content to model context or session history. If an effective prompt cannot
be verified against Tau's deterministic builder inputs, Tau labels the whole
prompt as runtime-composed rather than guessing provenance.

The sidebar usage section shows `avg TPS` and `avg TTFT` across timed session
history. Effective TPS uses the accumulated time Tau spends awaiting provider
events, including provider queueing, network waits, prefill, and TTFT; it
excludes Tau's rendering and persistence between stream pulls. TPS is
token-weighted. TTFT is the arithmetic mean of provider-wait time through Tau's
first text, thinking, or tool-call output event. Older assistant messages
without persisted timing still count toward token usage but not these metrics.

## `/model` and `/scoped-models`

The pickers render cached/bundled choices immediately, then refresh remote
catalogs in the background and update the open list. This includes the
account-scoped OpenAI Codex model snapshot, so models discovered in an earlier
session are available before a refresh. Both commands refresh the Codex catalog;
refresh failures leave the existing list usable. Use `tau update --models` for
forced public-catalog revalidation or `TAU_OFFLINE=1` to disable catalog network
access.

## `/sidebar`

Use `/sidebar` to toggle the detailed session sidebar for the current TUI
session. It preserves a configured `left` or `right` position and never writes
`~/.tau/tui.json`; the choice is forgotten when Tau restarts. When
`sidebar_position` is `"off"`, an explicit show temporarily uses the default
right position.

## Sidebar file editor

Click a prompt template, context file, or skill row in the sidebar to open its
file in an editor in the main UI area. Skill rows open only their main
`SKILL.md`, not supporting files from the skill directory. Editable rows use a
highlighted, underlined hover state. Use the arrow keys to move the editing
cursor. Press Ctrl+S to write changes to disk; Tau keeps the editor open and
reports either success or the write error. Tau refuses to overwrite a file
that changed on disk after you opened it, and keeps the current editor open if
you click another file while it has unsaved changes. Save or close the current
file first. Press Escape to restore the transcript. Run `/reload` afterward to
apply changed resources to the active session.

## `/resume`

The resume picker uses separate project and recent-session columns. Project
rows show compact folder names, while the session-column header shows the
selected project's full path. Its shell opens immediately, then the current
project and other project indexes load in the background. Press Left to
select the project column, Up/Down to choose a project, and Right to return to
its sessions. Enter resumes the selected session. Search filters names and
models within the selected project.

## `/local`

Type `/local` to open the generic local-backend host. It explicitly chooses a
registered backend even when only one is available; the recommended backend is
preselected but still requires confirmation. Once confirmed, Tau probes its one
effective saved/environment/default endpoint. The built-in `llama.cpp` backend
provides endpoint/API-key fields plus separate arrow-key navigable model and
action sections. Only the focused section has a `focused` marker, accent border,
and selection highlight. Up/Down moves continuously across section boundaries;
Tab switches sections directly. Enter selects from the focused section and
Escape closes. Loading and downloading open a
separate confirmation with model details before work begins. Downloading shows a
full-width block bar and router-reported byte counts, including after reopening
`/local` during a transfer. The actions section exposes
Hugging Face search/download, explicit active-download cancellation, status,
refresh, Doctor, and reset.

Configuration fields are structured text, secret, or choice values. Secret input
is not echoed into diagnostics or session history. Backends perform async
validation and return typed status, model, diagnostic, and progress data; they
do not construct Textual widgets.

Refresh may show a cached/stale model snapshot when the server is down. Use an
exact discovered model with `--provider llama.cpp --model ...` for print or TUI
startup. A missing active model is marked stale rather than silently replaced.
State-changing local actions require an idle agent. Closing the screen cancels
its owned work except an active server-side download, which continues in
llama.cpp and can be explicitly cancelled from the Actions section after
reopening `/local`. Results from a retired or replaced extension generation are
ignored.

Reset removes only Tau's llama.cpp settings and safe snapshot. Stored credential
deletion is separately confirmed. Tau never stops the external server or
deletes model files. See `local-inference.md` and `security.md`.

## Herdr compatibility

Herdr 0.9.0 can advertise SGR pixel mouse support while forwarding cell
coordinates. Textual then interprets those coordinates as pixels, collapsing
mouse interactions into the pane's top-left corner. When `HERDR_ENV=1`, Tau
defaults `TEXTUAL_SMOOTH_SCROLL` to `0` before starting the TUI and updates
Textual's already-loaded setting. This retains standard resize signals and cell
mouse coordinates. An explicit user value is preserved.

Do not introduce Textual dependencies into `tau_agent`. Keep reusable behavior
in the harness/session layers and UI behavior in this adapter. Use Textual pilot
tests and deterministic fake providers/backends for interaction tests.
