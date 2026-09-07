# Recover malformed tool history

## What was added

Tau now validates tool-call history before replaying it to a provider. The
provider-neutral repair lives in `tau_agent.tool_history` and enforces one
simple invariant: every assistant tool call is immediately followed by exactly
one matching tool result.

`CodingSession` applies that repair when loading or resuming an active branch
and after `/tree` selects a branch. If history changes, Tau appends a new valid
branch and a `tau.session-history-repair` custom entry containing repair counts.
The original JSONL entries remain untouched. The agent loop repairs its
provider input in memory as a final safety backstop for callers that construct a
harness without `CodingSession`.

## Why it exists

Older cancellation and persistence paths could save either side of a tool
exchange without the other. Providers reject those transcripts with errors such
as:

```text
No tool call found for function call output with call_id call_...
```

Prevention stops new corruption, but it does not make existing user sessions
usable. Recovery must therefore tolerate several shapes:

- a call without a result;
- a result without a call;
- a result separated from its call by another message;
- duplicate results for one call;
- parallel results saved in the wrong order.

## Repair policy

The policy is deterministic and deliberately conservative:

1. Existing results move directly after their calls and follow call order.
2. A call without a result receives `Tool call interrupted by user`.
3. A result without any call is omitted. Tau cannot safely invent the missing
   call because its arguments are unavailable.
4. Duplicate results collapse to one; a real result wins over Tau's synthetic
   interruption result.
5. The raw append-only history is retained. A repaired branch and diagnostic are
   appended instead of rewriting the JSONL file. Active model, thinking-level,
   and label state are snapshotted onto the repaired branch, and application
   custom entries from the rewritten suffix are copied forward.

Applying the policy again is a no-op, so repeated resumes do not add more repair
branches or diagnostics.

## Architecture

- `tau_agent.tool_history` owns the pure, provider-neutral transformation.
- `tau_agent.loop` applies it only to provider input; durable harness history is
  not silently mutated at this safety boundary.
- `tau_coding.session` owns append-only branch repair and durable diagnostics.
- Provider adapters remain unchanged and receive valid canonical history.

This preserves Tau's package boundary: portable transcript correctness belongs
in `tau_agent`, while disk-session workflow belongs in `tau_coding`.

## How to test

```bash
uv run pytest tests/test_tool_history.py tests/test_agent_loop.py tests/test_coding_session.py
```

Manual validation:

1. Resume a session containing an orphan `ToolResultMessage`.
2. Send a prompt that previously returned provider status 400.
3. Confirm the prompt succeeds.
4. Inspect JSONL and find one `tau.session-history-repair` custom entry followed
   by the repaired branch.
5. Resume again and confirm no second repair diagnostic is appended.
