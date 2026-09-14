# Markdown-rendered `/system` transcript output

## What changed

The TUI keeps `/system` output inside the transcript as a local-only status item. The output is separated from the command label with a blank line so the existing Markdown transcript renderer can provide readable paragraph, heading, list, and inline-code spacing.

Each contiguous prompt section now has a numbered heading and source label.
Tau's deterministic builder attributes the built-in/custom base, each append
file, extension section, project instruction file, skill, date, and working
directory while preserving the exact provider-facing prompt when section
contents are concatenated. Explicit CLI values use their flag as the origin.
An exact `CodingSessionConfig.system` override is labeled directly. If live
prompt text ever differs from reconstructed inputs, inspection falls back to a
single runtime-composed section rather than showing incorrect provenance.

The system prompt is display-only: it is not sent back to the provider, persisted as a session message, or counted as conversation context.

## Why

System prompts can include tool instructions, project context, and loaded-skill metadata. A large unformatted block is difficult to scan. Reusing the transcript's Markdown rendering keeps the command output in the user's normal reading flow while making Markdown structure visible.

Markup tags are protected from Markdown's HTML handling and rendered as inline code, using the active theme's code color so the tags stay visible and distinct from prompt prose. More detailed tag-aware syntax highlighting can build on this seam later without changing the prompt sent to the model.

## Architecture

The behavior stays in `tau_coding.tui`: slash-command semantics remain in `tau_coding.commands`, while the Textual frontend chooses how local command output is presented. The prompt itself remains owned by `CodingSession` and is never added to `TuiState` as a model message.

## Testing

Builder and coding-session tests verify source ordering, paths, exact text
coverage, and conservative fallback behavior. The Textual pilot test for
`/system` verifies that the command remains in the transcript, uses the Markdown
widget, and leaves no modal or model-context entry:

```bash
uv run pytest tests/test_tui_app.py -k system
```
