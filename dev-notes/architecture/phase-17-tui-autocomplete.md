---
title: "Phase 17: TUI Slash-command Autocomplete"
---

Phase 17 adds prompt autocomplete to Tau's Textual TUI while keeping the logic in
`tau_coding`.

The implementation lives in:

```text
src/tau_coding/tui/autocomplete.py
src/tau_coding/tui/app.py
src/tau_coding/tui/widgets.py
```

## What was added

Tau now builds completion suggestions for the prompt input from coding-session
metadata:

- registered slash commands from the active `CommandRegistry`
- loaded skills for `/skill:<name>`

The pure completion model is:

```python
CompletionItem
CompletionState
build_completion_state(...)
```

This keeps completion matching and replacement behavior testable without
Textual. The Textual app only owns rendering, key handling, and applying the
selected completion to the prompt input.

## TUI behavior

When the prompt starts with `/`, Tau shows matching slash commands.

Examples:

```text
/st       -> /status
/ski      -> /skill:
```

Command completion reads canonical command names, executable aliases, and
non-executable search terms from the active `CommandRegistry`. Search terms are
filters only: accepting one inserts the canonical command. For example, `/res`
can match `/resume` directly and `/new` through the `/new` command's `reset`
search term, so autocomplete ranks the direct command or alias match first and
then keeps fallback search-term matches available below it.

When the prompt starts with `/skill:`, Tau shows matching loaded skills.
Accepting a skill completion preserves the rest of the request:

```text
/skill:r fix tests -> /skill:review fix tests
```

The prompt input handles:

- `Tab` to accept the selected completion
- `Down` to select the next completion
- `Up` to select the previous completion

Suggestions are rendered in a small strip below the prompt instead of being mixed
into the transcript.

## Prompt templates

Prompt templates are already loaded and available on `CodingSession`, but Tau
does not yet have a user-facing `/prompt:<name>` or equivalent template
invocation command. Phase 17 therefore does not expose prompt-template argument
completion yet; it should be added when a template invocation command exists.

## Boundary

Autocomplete stays out of `tau_agent`.

The reusable agent harness has no dependency on Textual, command registries,
skills, prompt templates, or Tau resource paths. Those remain application
concerns owned by `tau_coding`.

## Tests

The phase is covered by:

```text
tests/test_tui_autocomplete.py
tests/test_tui_app.py
```

The tests verify:

- registered command suggestions
- `/skill:` suggestions
- preserving request text after skill completion
- completion selection wrapping
- TUI completion acceptance
