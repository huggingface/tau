# Phase 24: Session Tree Branching

This phase exposes Tau's existing append-only session tree in the Textual TUI.
It follows Pi's core behavior: moving through the tree is a structural session
mutation, not a transcript edit.

## What Changed

`/tree` opens a modal tree picker for the active session. The picker lists
branchable conversation entries as a flat list, marks the active leaf, and
supports two actions:

- `Enter` moves the active leaf to the selected entry.
- `S` moves the active leaf through a new `branch_summary` entry.

Both actions preserve all existing JSONL entries. Tau records navigation by
appending a new `leaf` entry, so reopening the session restores the selected
branch. The picker intentionally hides metadata entries such as model changes,
thinking-level changes, leaf pointers, and session info; it only shows user
messages, assistant messages, tool calls, compaction summaries, and branch
summaries.

## Branch Summaries

Tau already had a `BranchSummaryEntry` type. This phase makes it replay-aware:
when the active root-to-leaf path contains a branch summary, `SessionState`
converts it into a user-context summary message.

The first implementation uses the same deterministic summary helper as automatic
compaction to summarize active-path messages after the selected entry. That keeps
the feature self-contained and testable. A later phase can replace this with a
model-generated summary flow without changing the storage shape.

## Boundaries

`tau_agent.session` owns generic replay semantics for `branch_summary` entries.
`tau_coding.CodingSession` owns branch navigation, summary creation, storage
mutation, and harness message replacement. The Textual TUI only displays choices
and calls the session method selected by the user.

## Validation

Useful checks:

```bash
uv run pytest tests/test_session.py tests/test_coding_session.py tests/test_commands.py tests/test_tui_app.py -q
uv run ruff check src/tau_agent/session/memory.py src/tau_coding/session.py src/tau_coding/commands.py src/tau_coding/tui/app.py tests/test_session.py tests/test_coding_session.py tests/test_commands.py tests/test_tui_app.py
```
