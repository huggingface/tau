# Breaking the tau_agent ↔ tau_ai import cycle

Issue: https://github.com/alejandro-ao/tau/issues/317

## What changed

`tau_agent.loop` and `tau_agent.harness` used to import `ModelProvider`,
`CancellationToken`, and the `Provider*Event` types from `tau_ai`, while every
`tau_ai` adapter imported message/tool types from `tau_agent`. That made the
two packages mutually dependent, contradicting the documented one-way layering.

The fix applies dependency inversion: the portable core now owns the contract.

- `tau_agent/provider.py` — canonical `ModelProvider` and `CancellationToken`
  protocols.
- `tau_agent/provider_events.py` — canonical `ProviderEvent` types.
- `tau_ai/provider.py` and `tau_ai/events.py` are pure re-export shims, so all
  existing `from tau_ai import ...` call sites keep working.

Dependencies now point inward: `tau_coding → tau_agent ← tau_ai`, and
`tau_agent` imports nothing from the other layers.

## Why re-export shims, not copies

The agent loop dispatches provider events with `isinstance`. If the classes
were redefined in both packages, adapters would emit old-class instances that
the loop's checks silently ignore. The shims import the same class objects, so
identity (and `isinstance`) is preserved across both import paths.

## How it is tested

`tests/test_layering.py`:

- scans `tau_agent` sources for any `tau_ai` import (the boundary itself)
- asserts the `tau_ai` re-exports are identical objects to the `tau_agent`
  definitions (the isinstance trap)
