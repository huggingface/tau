---
title: "Pre-extension Hardening Summary"
---

This note summarizes the production hardening work completed before Phase 21
extensions. Phase 21 remains intentionally deferred.

## Agent And Provider Runtime

Tau now has provider-neutral progress events for retries, thinking/reasoning
deltas, and queued prompt state:

- `RetryEvent` reports provider retry attempts without exposing provider-specific
  HTTP details to frontends.
- `ThinkingDeltaEvent` carries optional streamed provider reasoning text without
  recording it as a durable assistant message.
- `QueueUpdateEvent` carries pending steering and follow-up prompt text for
  frontend status displays.

OpenAI-compatible, Anthropic, and OpenAI Codex subscription providers retry
transient transport failures before emitting a final provider error. The retry
policy is configurable through provider settings and environment variables.

Credential resolution is explicit: stored Tau credentials from
`~/.tau/credentials.json` take precedence over environment-variable fallbacks for
providers with a `credential_name`.

## Context, Skills, And Sessions

Context accounting now returns a structured `ContextUsageEstimate` with total,
system, message, and tool token estimates. The TUI refreshes from the event
stream, so sidebar and compact-session context numbers update after user
messages, assistant responses, tool results, compaction, and resume/new-session
flows.

Loaded skills are available through two paths:

- the system prompt lists skill names, descriptions, and file locations so the
  model can read relevant skill files with the normal `read` tool
- `/skill:<name> [request]` expands the full skill markdown into the next prompt

Session export is available through `tau export`. It writes a self-contained
HTML view for an indexed session id or JSONL session path and preserves both the
session tree structure and storage-order transcript.

## TUI Behavior

The Textual frontend remains behind the adapter boundary:

```text
CodingSession emits AgentEvent values
        ↓
TuiEventAdapter updates TuiState
        ↓
Textual widgets render transcript, status, and controls
```

Recent TUI hardening added:

- responsive sidebar behavior with provider/model, thinking mode, tools, skills,
  prompt templates, and context files
- context-size refresh after streamed user, assistant, tool, compaction, and
  resume events
- Textual transcript text selection for visible transcript output
- message selection with `Alt-Up` / `Alt-Down`
- selected-message copy with `Ctrl-C`
- inline tool result expansion with `Ctrl-O`
- animated activity status while an agent run is active
- thinking-mode cycling with `Shift-Tab`
- optional thinking-token display with `Ctrl-T`, hidden by default
- queued steering with `Enter` while running
- queued follow-ups with `Alt-Enter` while running

Queued prompts are not persisted when first queued. They become durable session
messages only when `AgentHarness` injects them into the active run and emits the
normal user-message events.

## Architecture Boundary

The added behavior preserves Tau's package split:

- `tau_ai` owns provider-specific retry, token, and stream parsing.
- `tau_agent` owns portable messages, events, loop coordination, harness state,
  queue semantics, tools, and session primitives.
- `tau_coding` owns provider configuration, credentials, resources, commands,
  persistence workflows, docs, and Textual UI policy.

The reusable agent package still does not import Textual, Rich rendering, Typer,
local config paths, slash-command registries, or project resource loading.

## Verification

The hardening slices are covered by focused tests across:

```text
tests/test_agent_harness.py
tests/test_agent_loop.py
tests/test_coding_session.py
tests/test_context_window.py
tests/test_provider_config.py
tests/test_rendering.py
tests/test_session_export.py
tests/test_skills.py
tests/test_tau_ai.py
tests/test_tui_adapter.py
tests/test_tui_app.py
tests/test_tui_config.py
```

Before merging the final pre-extension changes, the full gate passed:

```bash
uv run pytest
uv run ruff check .
uv run mypy
```
