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

The initial protocol supports Pi-compatible prompting (`prompt`, `steer`, `follow_up`, `abort`),
state and message inspection, model and thinking controls, compaction, new/resumed sessions,
session statistics and trees, forking, and command discovery. Unknown commands and invalid
arguments return `success: false` without stopping the process.

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

Tau mirrors Pi where its public `CodingSession` has equivalent behavior. This first version does
not yet implement Pi's direct `bash`/`abort_bash`, extension UI request/response protocol,
session naming, auto-compaction toggling, entry cursors, cloning, or export commands.
