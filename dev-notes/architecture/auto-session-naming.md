# Automatic session naming

Tau automatically gives a new managed session a short title based on the first user message.

## What was added

- New managed sessions are named automatically after the first user message is durably persisted.
- Tau asks the currently selected provider/model for a concise title of at most four words.
- The title is sanitized before it is stored in session metadata.
- If the provider call fails or returns unusable text, Tau falls back to a local title derived from the first message.
- `/name` remains the manual override and Tau does not replace an existing session title.

## Why it exists

Session ids are useful for exact references, but they are hard to scan in `/resume`, `tau sessions`, and id completions. Automatic names make saved sessions easier to recognize without requiring users to pause and name each one manually.

## How it differs from Pi

This is one of Tau's small intentional product divergences from Pi's minimalist baseline. Pi-style session persistence focuses on durable transcripts and explicit user actions. Tau adds automatic session metadata to improve resume/discovery workflows.

The divergence is kept in `tau_coding` because it is application metadata behavior, not reusable agent-harness behavior. `tau_agent` still only owns the portable agent loop, messages, tools, and events.

## Model choice

Tau uses the session's currently selected provider/model for the naming request. There is no separate title model setting yet. This keeps the implementation simple and follows the active session configuration, but it does mean the first turn may make a short extra model call before the main assistant response.

## Persistence behavior

Naming happens only after the first user message has been persisted. This preserves deferred indexing for newly prepared sessions: a session that is cancelled before its first durable message should not appear in the resume index just because naming started.

## How to test

```bash
uv run pytest tests/test_coding_session.py -q
uv run ruff check src/tau_coding/session.py tests/test_coding_session.py website/content/guides/sessions.md
```
