# Tau TUI

Tau's interactive interface uses Textual behind an adapter boundary. The
portable `tau_agent` harness emits provider-neutral events; the TUI renders
them and owns interaction.

## `/local`

Type `/local` to open the generic local-backend host. It explicitly chooses a
registered backend even when only one is available; the recommended backend is
preselected but still requires confirmation. Once confirmed, Tau probes its one
effective saved/environment/default endpoint. The built-in `llama.cpp` backend
provides endpoint/API-key fields, clickable model rows, Hugging Face
search/download, status, refresh, use, Doctor, and reset.

Configuration fields are structured text, secret, or choice values. Secret input
is not echoed into diagnostics or session history. Backends perform async
validation and return typed status, model, diagnostic, and progress data; they
do not construct Textual widgets.

Refresh may show a cached/stale model snapshot when the server is down. Use an
exact discovered model with `--provider llama.cpp --model ...` for print or TUI
startup. A missing active model is marked stale rather than silently replaced.
State-changing local actions require an idle agent. Closing the screen cancels
its owned work, and results from a retired or replaced extension generation are
ignored.

Reset removes only Tau's llama.cpp settings and safe snapshot. Stored credential
deletion is separately confirmed. Tau never stops the external server or
deletes model files. See `local-inference.md` and `security.md`.

Do not introduce Textual dependencies into `tau_agent`. Keep reusable behavior
in the harness/session layers and UI behavior in this adapter. Use Textual pilot
tests and deterministic fake providers/backends for interaction tests.
