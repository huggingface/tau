# Per-turn agent re-rendering (`render_turn`)

## What

`run_agent_loop()` and `AgentHarnessConfig` now accept an optional
`render_turn` callback. It runs before every provider request and may return a
`(model, system, tools)` tuple to override the agent's configuration for that
turn — `None` keeps the current configuration. Both sync and async callables
are supported.

When the model changes, a new `ModelChangeEvent` is emitted before the request,
so listeners (UI, sessions) can observe the switch. System-prompt and tool-set
changes apply silently.

```python
def render_turn() -> tuple[str, str, list[AgentTool]] | None:
    return ("bigger-model", system, tools) if step == "hard" else None

run_agent_loop(..., render_turn=render_turn)
```

## Why

The loop previously locked `model`/`system`/`tools` for an entire run, so an
agent could not change what it sees or can do between turns of one
conversation. Workflows that need conditional capabilities — a support agent
that only gains escalation tools after verifying a customer, a triage agent
that switches models mid-task — had to rebuild the harness between messages.
`render_turn` makes turn-level configuration changes a loop concern, where the
tools, system prompt, and model already live.

## How it maps to the architecture

Per `AGENTS.md`, the agent loop is a portable `tau_agent` concern: events are
the contract, and the loop owns tools/system/model per turn. The change adds
one optional callback plus one event; no layer boundaries move. `tau_coding`
and the TUI are untouched and can consume `ModelChangeEvent` like any other
agent event.

## How to test or use it

- `tests/test_agent_render_turn.py` covers per-turn config swaps, withdrawn
  tools erroring on the next turn, `None` passthrough, async renderers, and
  harness pass-through.
- Default behavior is unchanged: `render_turn=None` is identical to before.
