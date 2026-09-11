# Learning Loop (memory + lessons + curator)

Date: 2026-09-11
Status: Implemented behind `/learn` (opt-in)

## What was added

Tau now has a compact port of Hermes Agent's learning loop:

1. `~/.tau/MEMORIES.md` — a small, durable, declarative memory file. Entries
   are joined by a `\n§\n` delimiter and injected into the system prompt as a
   *frozen snapshot* per session (budgeted at 2,200 chars, so it stays cheap).
2. `~/.tau/skills/lessons/<name>/SKILL.md` — durable lesson files written in
   the existing skill-directory format, so `/reload` (and the regular skill
   loader) picks new lessons up with no extra plumbing.
3. An LLM **curator** pass (`/learn`) that reviews a settled session
   transcript and writes both stores, using the session's own provider and
   model.

## Why it exists

Pi (and therefore Tau) is stateless between sessions by design. Hermes showed
that a tiny amount of durable, declarative state — *memory* (facts) plus
*lessons* (procedures) — compounds across sessions without growing prompt cost
linearly, because both stores are hard-budgeted and deduplicated. This phase
ports that idea to Tau's architecture instead of copying Hermes's tool
surface.

## How it maps to Pi / Tau's design

- `tau_coding/learning.py` is pure store logic: no I/O beyond the two
  directories it owns, no provider calls, fully deterministic and testable
  (`tests/test_learning.py`).
- `tau_coding/learning_curator.py` is the async bridge: it mirrors
  `branch_summary.py`'s `stream_response` → final-text collection pattern.
- Prompt injection reuses `BuildSystemPromptOptions` (new optional
  `learned_context` field) instead of a parallel prompt path, so custom
  prompts, extra sections, and append-prompts all compose unchanged.
- The snapshot is taken once at session construction and never re-rendered
  mid-session (Hermes's frozen-snapshot invariant), refreshed only after a
  successful `session.learn()`.

## How to use / test it

```bash
# inside a Tau session, after finishing real work:
/learn

# tests
uv run pytest tests/test_learning.py -q
```

Durable layout after a run:

```text
~/.tau/MEMORIES.md            # §-delimited declarative facts
~/.tau/skills/lessons/<name>/SKILL.md   # lesson = skill-format file
```

Invariants worth keeping when extending this:

- Writes are additive-or-replace; the curator never deletes existing lessons
  or memory entries.
- A full memory store rejects the whole batch with a consolidation hint
  (reject-and-show, not silent truncation).
- Lesson names are sanitized to kebab/snake case and capped at 48 chars.
- Nothing clears the quality bar → the curator returns nothing (the normal
  outcome; `/learn` reports "nothing new" instead of writing filler).

## Known limitations / follow-ups

- `/learn` is manual. An automatic end-of-turn curator pass is possible later
  behind a config flag, but was deliberately deferred.
- Memory consolidation (merging stale entries) is manual today: edit
  `~/.tau/MEMORIES.md` when the store rejects new entries.
- The lessons directory is loaded by the standard skill loader, so lessons
  appear in `<available_skills>` like any other skill — intentional, but a
  future phase may want a dedicated "learned lessons" index with provenance.