---
title: "Phase 28: Pi-compatible RPC mode"
---

Phase 28 adds a headless frontend at `tau_coding.rpc`. It consumes the same `CodingSession`
events as print mode and Textual, preserving `tau_coding → tau_agent → tau_ai`.

The process uses strict JSONL on stdin/stdout. A serialized writer prevents response and event
bytes from interleaving; prompt work runs in an AnyIO task group so cancellation and queued input
remain available while events stream. EOF cancels active work, waits for owned tasks, and closes
the session.

## Pi compatibility

| Area | Status |
| --- | --- |
| prompt, steer, follow-up, abort | supported |
| state/messages | supported |
| models and thinking | Pi-shaped models plus set/cycle/list supported |
| manual compaction | supported; detailed usage remains approximate |
| new/switch session, stats, tree, fork | Pi-shaped wire responses; Tau ID or indexed path accepted |
| command discovery | supported with synthetic Tau source metadata |
| direct bash | supported |
| abort_bash | deferred; needs a cancellable public session API |
| HTML export, session naming, entry cursor | supported |
| auto-compaction toggle | supported |
| extension UI RPC | deferred; Tau currently uses frontend bridge callbacks |
| queue/retry controls, clone, image prompts | deferred |

The protocol intentionally maps only public `CodingSession` behavior. It does not read raw session
JSONL, depend on Textual, or duplicate the agent loop.

## Verification

`tests/test_rpc.py` drives a real `CodingSession` with `FakeProvider` through in-memory JSONL
streams. It covers correlated acceptance, event streaming, malformed records, CRLF, and Unicode
line separators. CLI tests cover `--mode rpc` routing separately.
