# Tau/Pi RPC runtime interchangeability plan

## Objective

An Electron host should be able to select either subprocess with one product setting:

```json
{"agent_runtime": "tau"}
```

Runtime-specific startup configuration (binary path, provider/model, cwd, and session location)
may differ. After startup, the host must use one command/event contract without branching on the
runtime for ordinary coding-agent behavior.

The compatibility target is Pi's documented JSONL RPC protocol in
`packages/coding-agent/docs/rpc.md`, because Pi already publishes a typed TypeScript client. Tau
must match command names, request fields, response envelopes, response data shapes, event names,
and lifecycle semantics for the shared surface. Persisted session files do not need to be mutually
readable; interchangeability is at the process boundary.

## Definition of done

1. The same black-box contract suite can launch Pi or Tau and exercise prompting, streaming,
   cancellation, model selection, thinking controls, direct shell commands, compaction, session
   inspection, and command discovery.
2. Shared commands have the same required request fields and success response shapes.
3. Shared events use the same camel-case wire vocabulary and lifecycle meaning.
4. Unsupported optional behavior returns a deterministic failed response; it never silently
   changes semantics.
5. Runtime-specific capabilities are isolated to optional features rather than ordinary chat/tool
   operation.
6. Electron consumes generated/shared protocol types or its own normalized domain model, never
   provider-specific chunks or either runtime's persisted JSONL.

## Gap inventory

### Wire shapes

Tau's first RPC version used model references where Pi returns complete model objects, omitted
state fields, returned a flat tree, exposed Tau session-stat names, and returned a display string
from compaction. These are wire compatibility issues even when the underlying behavior exists.

### Missing commands

Pi additionally supports model/thinking cycling, queue delivery modes, auto-compaction/retry
controls, direct bash cancellation, HTML export, cloning, fork-message discovery, entry cursors,
last-assistant lookup, and session naming.

### Input and extension UI

Pi accepts image blocks and implements an extension dialog request/response subprotocol. Tau's
provider-neutral messages support images, but `CodingSession.prompt()` currently accepts text and
Tau's extension bridge is callback-oriented. Both require dedicated session seams before they can
be made protocol-compatible.

### Session identity

Pi exposes session file paths; Tau primarily exposes indexed IDs. Tau should accept either an ID
or an indexed Tau session path at the RPC boundary while keeping `SessionManager` authoritative.
The two runtimes' persisted files remain intentionally different.

## Delivery phases

### Phase A — frontend-critical parity (this change)

- Normalize state and model responses to Pi field names.
- Add model/thinking cycling.
- Add direct bash, HTML export, entry cursors, tree projection, fork-message discovery,
  last-assistant lookup, and session naming.
- Add auto-compaction control through a public `CodingSession` method.
- Keep session restoration behind `SessionManager` while accepting Pi's `sessionPath` field.
- Add fixture-style response tests and update the published compatibility matrix.

This phase enables one Electron adapter for text chat, streaming tools, cancellation, model and
thinking selection, direct commands, transcript restoration, and session browsing.

### Phase B — execution controls

- Add a cancellable direct-bash task owned by `CodingSession`; emit Pi-compatible
  `bash_execution_update` records and implement `abort_bash`.
- Add runtime auto-retry enable/disable and retry-delay cancellation seams.
- Add queue delivery modes (`all` and `one-at-a-time`) to the portable harness rather than faking
  them in the RPC frontend.
- Return structured compaction data (summary, replaced boundary, token estimates, usage) from a
  session API while preserving current TUI messages.

### Phase C — multimodal and extension UI

- Extend session prompt/queue APIs with provider-neutral image content.
- Implement an RPC extension UI bridge for select, confirm, input, editor, notifications, status,
  widgets, titles, and editor text.
- Correlate dialog responses and handle cancellation/timeouts without blocking stdin dispatch.

### Phase D — conformance and client validation

- Vendor protocol fixtures derived from Pi's public documentation (not Pi runtime code).
- Run the same subprocess scenarios against both installed binaries in an optional integration
  job.
- Add a small TypeScript smoke fixture using Pi's RPC client against Tau; pin the tested Pi
  protocol version in documentation.
- Classify future Pi protocol additions as supported, intentionally different, or pending before
  claiming a newer compatibility level.

## Electron integration guidance

Use subprocess RPC for both runtimes, even though Pi can also be imported directly in Node. Keep
process launch in the Electron main process and expose a narrow IPC API to the renderer. Store
runtime-specific launch configuration separately from the shared session state. The renderer
should key running state off `agent_settled`, not `agent_end`.

A production host should still maintain a capability table for optional Phase B/C behavior.
Choosing `agent_runtime` alone is sufficient for the Phase A common surface; optional controls
should be hidden or disabled when the selected runtime does not advertise/support them.
