# ACI feedback and per-run policies

## Why these changes exist

Previously a provider adapter silently wrapped broken tool JSON in
`_raw_arguments`. Tools then complained about missing fields rather than the
actual syntax error. Separately, the coding session could retry and keep using
tools without any opt-in per-run spending or repeated-failure guardrail.

The implementation follows `plans/aci-feedback-and-run-policies.md` and keeps
the [project roadmap's](https://github.com/huggingface/tau/issues/1) separation:
provider adapters decode; the portable agent executes tools and emits events;
the coding application owns configurable run policy. No dependencies were added.

## P0: actionable malformed-argument feedback

`ToolCall.malformed_arguments_text` is an in-process marker excluded from wire
serialization. The Anthropic, Mistral, Codex, OpenAI-compatible chat, and
OpenAI-compatible Responses builders set it when their final arguments cannot
be decoded as an object. Arguments themselves become `{}`. Valid JSON arrays,
scalars, and null are also rejected because tool input must be an object; an
empty argument stream retains its existing `{}` behavior.

`_execute_tool_call` runs the extension gate and cancellation check first, then
returns an error containing the original text and a concrete retry instruction.
Tool lookup/execution is skipped. The existing start/end and message events,
after-tool hook, and persistence paths remain intact. The durable tool result
retains the diagnosis even though the transient marker is not serialized.

## P1: policy in the coding application

`RunPolicyMonitor` subscribes to harness events. Completed assistant messages
accumulate usage (even on failure); completed turns evaluate cost, token, and
consecutive-failure thresholds. Every limit is off by default. Counters reset on
public prompt/continue, not queued messages or internal retries. A fired set
prevents repeated action for the same threshold; simultaneous limits are grouped.

`ShellSettings.run_policies` reads `runPolicies` in Tau home. Settings reuse the
same validated limits as embedding callers, rejecting nonpositive, nonfinite,
boolean, and wrongly typed values. CLI print/RPC and TUI startup pass them through
`CodingSessionConfig`. Session replacement rebinds the listener and callbacks;
close unsubscribes it.

Two implementation adjustments make the plan's intended behavior work with the
actual code:

1. Count a turn containing a failed tool as a failure, not only an assistant
   provider error. Otherwise repeated malformed arguments never reach the very
   failure threshold intended to stop them. Any failed tool makes the turn fail
   once; a successful turn resets the streak; an aborted turn leaves it unchanged.
2. The existing cancellation token alone may be checked only after the next
   provider request starts. A small session-side event adapter closes the harness
   iterator at the completed-turn boundary for policy cancellation, then emits
   the existing terminal event to the session frontend and extension runtime.
   It also prevents automatic retries and post-run compaction from starting a
   new uncancelled run. The core loop's round structure and queue semantics are
   unchanged. Direct harness subscribers see teardown, as for other iterator
   cancellation; the session adapter owns this policy-generated terminal event.

Steering uses the existing queue API and `QueueUpdateEvent`; no new event type or
frontend rendering code is needed. It is a request to summarize, not a hard
guarantee that the model obeys. After a terminal provider error, a summary request
may remain queued until a continuation; no extra retry policy was introduced.

## Accounting boundary

Checks happen after the entire turn, including its tools. Tokens use a reported
total or a component fallback, without counting reasoning/cache subcategories
twice. Pricing reuses the session resolver and existing cache/tier calculation;
positive provider-reported cost is the fallback. Unknown prices are skipped,
while token and failure limits still work. Thus the cost total can be partial.
Auxiliary naming, compaction, and branch-summary requests do not emit harness
assistant events and are outside these counters. Limits are not strict billing
caps and cannot undo completed requests or tools.

## Configuration and verification

See [configuration](../website/content/reference/configuration.md#run-policies)
for a complete settings example and the soft/hard-stop tradeoff. Tests use
deterministic providers rather than paid model calls:

```sh
uv run pytest tests/test_malformed_tool_arguments.py tests/test_run_policies.py -q
uv run pytest tests/test_agent_loop.py tests/test_agent_harness.py tests/test_tau_ai.py -q
uv run pytest -q
uv run ruff check src tests
uv run mypy
```

The new tests cover all five parsing paths, valid and invalid objects, gate
precedence, correction feedback reaching the next model turn, unchanged event
shape, JSONL round trips, pricing/cache accounting, unknown prices, error streaks,
one-shot actions, defaults, public-run reset, internal retries, queued input,
hard cancellation before another request, listener replacement/teardown, and
settings reaching a real print-mode session with a fake provider.

### Verification on 2026-09-30 (Windows)

- New coverage: 70 passing cases (42 malformed-argument cases and 28 policy cases).
- Combined new tests, core loop/harness/provider regression, and shell settings:
  184 passed. The final persistence assertions also pass in the 28 policy cases.
- Full suite: 2031 passed, 46 failed, 5 skipped, 1 warning. This run preceded the
  last three policy test additions, which were verified separately above.
- All 46 failing full-suite cases were replayed against an isolated unmodified
  snapshot of `c66fb879c1058f7b3d8514fb7f92c919d3c3e3b3`; all 46 failed there too.
  These include platform/path/shell/permissions assumptions and environment or
  subprocess timing failures. The failure sets match; the full suite is not green.
- `ruff check src tests`, formatting checks for all changed Python files, and
  `git diff --check` pass.
- `mypy`: 14 errors in the existing `project_trust.py` and session `storage.py`
  platform-locking code. The original snapshot reports the identical 14 errors;
  none are reported in the changed policy/adapter code.
- Commands used `uv --cache-dir .uv-cache run --no-sync ...` because the default
  Windows user cache was not writable in this execution environment. Dependencies
  were not changed. No live/paid model invocation, commit, or push was performed.
