# Tau TUI

Tau's interactive interface uses Textual behind an adapter boundary. The
portable `tau_agent` harness emits provider-neutral events; the TUI renders
them and owns interaction. Ctrl+P cycles forward through scoped models;
Shift+Ctrl+P cycles backward.

The sidebar usage section shows `avg TPS` and `avg TTFT` across timed session
history. Effective TPS includes request startup, prefill, and TTFT, and is
token-weighted. TTFT is the arithmetic mean from request start to Tau's first
text, thinking, or tool-call output event. Older assistant messages without
persisted timing still count toward token usage but not these metrics.

## `/model`

The picker renders cached/bundled choices immediately, then refreshes remote
catalogs in the background and updates the open list. Refreshes are throttled to
four hours and failures leave the existing list usable. Use
`tau update --models` for forced revalidation or `TAU_OFFLINE=1` to disable
catalog network access.

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

Do not introduce Textual dependencies into `tau_agent`. Keep reusable behavior
in the harness/session layers and UI behavior in this adapter. Use Textual pilot
tests and deterministic fake providers/backends for interaction tests.
