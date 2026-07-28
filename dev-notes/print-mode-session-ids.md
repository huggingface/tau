# Machine-safe print-mode session ids

Issue #499 asks how an orchestrator can record the durable Tau session created
for a print-mode worker without parsing human output or searching the session
index.

## Pi investigation

Pi was inspected at `earendil-works/pi` commit
`47ca25fcd8535b80710fad5be758f1f2cf81443c`. Its CLI exposes:

```text
--session-id <id>  Use exact project session ID, creating it if missing
```

Pi validates ids as file-safe names. If the id already exists in the project,
Pi opens that session; otherwise it warns and creates it. This means automation
can generate a unique id before launching Pi and already knows the exact id
without consuming another output channel.

## Tau design

Tau now follows Pi's option name and caller-selected-id approach:

```bash
tau --print --new-session --session-id worker-01 "..."
```

The option is intentionally scoped to print mode because Tau already uses
`--session` for TUI resume behavior and issue #499 concerns isolated workers.
Tau also refuses an existing id instead of resuming it. This small semantic
difference protects the existing guarantee that every Tau print invocation
creates an isolated session and prevents an orchestrator typo or collision from
appending to another worker's transcript.

No id is written to stdout or stderr. Text, JSON, and transcript rendering are
therefore unchanged. The caller should generate a unique id (for example, a
UUID), pass it to Tau, record it beside the worker report, and use the process
exit status to determine whether startup/the model turn succeeded.

Custom ids use Pi's validation rule: alphanumeric characters plus `.`, `_`, and
`-`, beginning and ending with an alphanumeric character. Validation happens
before provider/resource startup. A collision also fails before the coding
session starts. Failures later in startup or during the model turn retain the
same requested id and normal non-zero exit behavior, so diagnostics can inspect
the session if it reached durable initialization.

## Testing

`tests/test_cli.py` covers all output modes, validation, print-only use, exact id
creation, and collisions. `tests/test_session_manager.py` verifies the filename
safety rule at the persistence boundary.
