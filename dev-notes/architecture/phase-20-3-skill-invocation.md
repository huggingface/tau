---
title: "Phase 20.3: Skill Invocation Reliability"
---

Phase 20.3 hardens how Tau exposes and invokes markdown skills.

## Automatic Skill Availability

Loaded skills are included in the system prompt when the `read` tool is
available:

```xml
<available_skills>
  <skill>
    <name>testing</name>
    <description>Use when writing tests</description>
    <location>/repo/.agents/skills/testing.md</location>
  </skill>
</available_skills>
```

The prompt now uses the Pi wording:

```text
Read the full skill file when the task matches its description.
```

This means a model can choose the relevant skill from the index, call `read` on
the listed location, and receive the full skill markdown as a normal tool result.

## Manual Skill Invocation

`/skill:<name> [instructions]` remains a prompt expansion directive rather than
a slash command that ends the turn. It now expands to the same Pi-style skill
block:

```text
<skill name="testing" location="/repo/.agents/skills/testing.md">
References are relative to /repo/.agents/skills.

# Testing
Run pytest.
</skill>

add parser tests
```

The block is self-contained: it includes the source file and tells the model
where relative references inside the skill should resolve.

## TUI Display

The session still sends and persists the full expanded skill block so the agent
has the complete instructions. The TUI parses that structured block when
rendering user messages and displays a compact skill item instead:

```text
Using skill: testing
```

If the original `/skill:<name>` input included additional instructions, those
instructions render as the visible user message after the compact skill item.
The full skill markdown is not shown in the normal conversation view.

## Boundary

Skill discovery and prompt expansion remain in `tau_coding`. `tau_agent` only
sees ordinary provider-neutral messages and tool calls. The model-visible skill
index uses the existing `read` tool instead of adding a special skill tool to
the reusable harness. TUI parsing is presentation-only and does not change the
stored or provider-visible message content.

## Tests

The phase is covered by:

```text
tests/test_skills.py
tests/test_cli.py
tests/test_coding_session.py
tests/test_system_prompt.py
tests/test_tui_adapter.py
tests/test_tui_app.py
```

The tests verify both paths:

- explicit `/skill:<name>` expansion sends full skill content to the provider
- the system prompt lists loaded skill locations, and a model-triggered `read`
  call can load the skill file into the next provider turn
