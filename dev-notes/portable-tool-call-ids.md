# Portable tool-call IDs across provider switches

## Problem

Provider transcripts contain tool-call identifiers that correlate an assistant's
request with the later tool result. Those identifiers are not portable by
default. OpenAI Codex, for example, needs both a `call_id` and a response item ID,
so Tau's persisted compatibility representation joins them with `|`:

```text
call_XwFnGCtoQNIN2ID9ahtpLvsI|fc_0ca1b059695ff22f
```

Anthropic only accepts letters, digits, `_`, and `-` in `tool_use.id`. Switching a
session with Codex tool history to Claude therefore produced an HTTP 400 before
Claude could answer.

## Implementation

`src/tau_ai/tool_call_ids.py` defines the common outbound correlation format.
Already-portable IDs remain unchanged. IDs containing provider-specific syntax,
or exceeding the conservative shared length, become a deterministic ID derived
from SHA-256:

```text
tc_<40 lowercase hex characters>
```

Hashing rather than replacing punctuation prevents IDs such as `a|b` and `a_b`
from collapsing to the same value. Because conversion is deterministic, each
provider adapter can translate a tool call and its later result independently
and still emit matching IDs. Parallel calls remain distinct.

The Anthropic, Google, Mistral, OpenAI Responses, and OpenAI-compatible Chat
serializers apply this conversion at their provider boundary. Persisted JSONL is
not rewritten, so old sessions remain intact and are repaired in memory whenever
they are sent to a target provider. Native IDs that already fit the shared format
retain same-provider replay behavior.

Anthropic compilation also drops thinking blocks from non-Anthropic assistant
messages. Thinking signatures are opaque provider-owned state; forwarding an
OpenAI or Google signature as an Anthropic signature would simply move the
cross-provider validation failure from the tool ID to the thinking block.

## Architecture

The provider-neutral transcript remains in `tau_agent`. Provider constraints and
history translation stay in `tau_ai`, where wire payloads are built. This keeps
the agent loop and session storage independent of any vendor's identifier regex.

## Verification

Focused regression tests build Codex-style history entirely in memory and compile
it for Anthropic. They verify that:

- call and result IDs match after conversion;
- parallel calls remain distinct;
- generated IDs satisfy Anthropic's accepted alphabet;
- foreign thinking signatures are omitted;
- native Anthropic thinking and already-safe IDs remain unchanged.

Run:

```bash
uv run pytest tests/test_cross_provider_history.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
