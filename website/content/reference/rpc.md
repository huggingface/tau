---
title: RPC protocol
description: Control Tau as a subprocess with Pi-compatible JSONL commands and events.
---

Run Tau as a headless subprocess:

```bash
tau --mode rpc [--provider NAME] [--model MODEL] [--cwd PATH] [--session ID]
```

RPC mode reads one JSON object per LF-terminated line from stdin and writes responses and
session events as compact JSON lines to stdout. Diagnostics use stderr. Clients may include an
`id`; the corresponding response echoes it. Event records are asynchronous and do not normally
carry request IDs.

```json
{"id":"1","type":"prompt","message":"Inspect this project"}
{"id":"1","type":"response","command":"prompt","success":true}
{"type":"agent_start"}
```

The protocol supports Pi-compatible prompting (`prompt`, `steer`, `follow_up`, `abort`), state
and message inspection, complete Pi-shaped model references, model and thinking cycling,
auto-compaction control, direct shell commands, HTML export, new/resumed sessions, entry cursors,
session statistics and trees, forking, last-assistant lookup, session naming, and command
discovery. Unknown commands and invalid arguments return `success: false` without stopping the
process.

Use `agent_settled`, not `agent_end`, to decide that a run is fully idle: retries, overflow
compaction, or queued continuations can follow `agent_end`.

## Framing

Split records only on LF (`\n`). A trailing CR is accepted for CRLF input. Unicode line
separators such as U+2028 and U+2029 are ordinary characters inside JSON strings. Records are
limited to 16 MiB.

## Minimal Node/Electron client

```js
import { spawn } from "node:child_process";

const tau = spawn("tau", ["--mode", "rpc"], { stdio: ["pipe", "pipe", "inherit"] });
tau.stdout.setEncoding("utf8");
let buffer = "";
tau.stdout.on("data", chunk => {
  buffer += chunk;
  for (;;) {
    const index = buffer.indexOf("\n");
    if (index < 0) break;
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    console.log(JSON.parse(line));
  }
});
tau.stdin.write(JSON.stringify({ id: "1", type: "prompt", message: "Hello" }) + "\n");
```

RPC compaction preserves recent entries and returns the first pre-existing retained entry as
`firstKeptEntryId`, matching Pi. Older Tau compaction records and TUI compactions that replaced
all active context have no such boundary; session inspection exposes those honestly as
`customType: "tau.compaction"` entries instead of fabricating Pi compaction metadata.

Tau mirrors Pi where its public `CodingSession` has equivalent behavior. Direct `bash` is
supported, but `abort_bash` requires a future cancellable session API. Queue delivery modes,
retry controls, cloning, image prompts, and the extension UI request/response subprotocol remain
staged compatibility work. See `dev-notes/design/rpc-runtime-interchangeability-plan.md` for the
contract, completed frontend-critical phase, and remaining production phases.
