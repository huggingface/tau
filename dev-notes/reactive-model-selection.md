# Reactive model and thinking selection

The interactive TUI now previews model and thinking changes in memory. Ctrl+P and Shift+Tab do not construct a provider, save defaults, or append session entries on each keystroke. The status display and model/thinking capability queries read the preview while the harness keeps its last committed provider/model until the next accepted idle prompt. Durable provider entries in the registry do not indicate an absent preview, so preview capabilities resolve by the displayed provider ID; runtime rebuilding continues to use only the committed provider. The session stats used by the sidebar are cached until the session state or configuration changes, so repeated shortcut presses do not recount the entire transcript. Before starting that turn, `CodingSession.prompt` commits the final choice and thinking level ahead of the user message. If preparation fails, no user prompt is sent with the old provider, and the preview remains for retry.

This follows Pi's separation of model selection from request-time streaming, but differs in history behavior: Pi appends selection entries immediately; Tau batches unsent choices into their final values at the next prompt. Previews are session-only, discarded on restart or branch/resume/new-session navigation. Existing explicit session APIs still support durable selection and settings persistence for non-TUI callers.

A picker regression exposed a second boundary: `/model` and `/scoped-models` used to synchronously reload settings before opening, and provider reload read the preview model when rebuilding the committed runtime. Both pickers now open from the cached snapshot and refresh later. Provider rebuilding uses the committed harness model; when the refreshed catalog drops that model, Tau retains the working provider while publishing the new catalog to the picker. An empty catalog can still open the picker with a stale active model. Picker
commands now return directly after opening instead of rebuilding the transcript,
and the catalog refresh starts only after the first screen refresh. The model
list uses Textual's virtualized `OptionList` rather than mounting every model
widget twice; unchanged catalog results leave existing options untouched.
Picker refresh workers belong to their screens and are cancelled on dismissal;
results from an old picker cannot update a replacement picker. Since Textual
calls result callbacks before removing the screen, selection work is scheduled
after the main screen's next refresh. Scoped-model cancellation skips chrome
rebuild unless the active thinking level actually changed.

Test with `uv run pytest tests/test_commands.py tests/test_coding_session.py tests/test_tui_app.py`. Try Ctrl+P and Shift+Tab repeatedly before sending a message: status changes immediately; the session file does not. Send a prompt and inspect the entries before the user message. Restart without sending to verify the old committed selection returns.
