# Extension session metadata

Tau extensions can read the active session name and thinking level through
`ExtensionContext.session_name` and `ExtensionContext.thinking_level`.
These values are live views over the bound `CodingSession`, like the existing
model, provider, and session id fields.

Tau also dispatches the existing `session_info_changed` event after automatic
naming or an awaited rename through `CodingSession.set_session_name()`.
It dispatches `thinking_level_changed` after a successful explicit
thinking-mode change. Event handlers are awaited before the
mutation method returns. Failed and no-op updates do not emit events.

Initial values do not produce change events during load. Extensions read them
from the context in `session_start`. Session replacement also uses the existing
shutdown/start lifecycle, so an extension receives the replacement values from
the new context.

The `/name` command now returns a rename intent. Async hosts apply that intent
through `await CodingSession.set_session_name(...)`. This is the one public
session-name mutation path: it persists the change, then awaits extension event
delivery before returning. The setter was synchronous before this change, so
SDK callers must now await it. Keeping one notifying setter avoids a silent
mutation path that could leave extensions stale.

Focused coverage is in `tests/test_extensions.py`, `tests/test_commands.py`,
`tests/test_coding_session.py`, and `tests/test_tui_app.py`.
