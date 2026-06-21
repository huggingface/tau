# LLM API Observability

Issue #54 adds the first opt-in path for inspecting the HTTP traffic Tau sends
to model providers.

## Placement

Observation is provider-adjacent but not app-owned:

```text
tau_ai adapter builds provider-specific request
        |
        v
tau_ai.observability emits redacted LLMObservation records
        |
        v
tau_coding.diagnostics writes JSONL when enabled
```

`tau_agent` is intentionally not involved. The harness still sees only
provider-neutral messages, tools, and events. It does not know about HTTP
headers, provider URLs, local log paths, environment variables, Textual, Rich, or
CLI flags.

`tau_ai` also does not write files. It defines:

- `LLMObservation`
- `LLMObserver`
- redaction helpers
- small helper functions for request, response, and error observations

`tau_coding` owns enablement and persistence. It creates an observer from
`TAU_LLM_OBSERVABILITY`, threads it through runtime provider construction, and
writes to Tau's diagnostic log directory.

## First captured data

The first implementation captures one JSONL record per provider lifecycle point:

- request: adapter name, model, method, URL, attempt, stream flag, redacted
  request headers, and redacted serialized request body
- response: HTTP status code and redacted response headers
- error: HTTP status error bodies and network exception metadata, redacted

Each retry attempt is recorded separately. Streaming requests are marked with
`"stream": true`, but individual streamed chunks are not logged yet. That keeps
the first version low-volume and avoids accidentally retaining full assistant
output, reasoning deltas, or tool-call argument streams.

## Redaction and privacy

Redaction is mandatory in this implementation.

Credential-like request and response headers are replaced with `[REDACTED]`.
This includes authorization, cookie, API-key, token, session, credential, secret,
and ChatGPT account-id style headers.

Prompt-like body fields are replaced with metadata instead of raw text:

```json
{
  "redacted": true,
  "kind": "text",
  "length": 42,
  "sha256": "..."
}
```

This applies to fields such as `content`, `text`, `instructions`, `system`,
`output`, `arguments`, `body`, `description`, and streamed-fragment-shaped
fields. Structured metadata such as `model`, `role`, `type`, tool names, status
codes, and provider URLs remains visible.

The log is still diagnostic material. Lengths, hashes, URLs, model names, tool
names, and schema shape can reveal sensitive context, so the file should be kept
local and shared deliberately.

## Enable and view

Set:

```bash
TAU_LLM_OBSERVABILITY=1 tau --provider local -p "debug this request"
```

or start the TUI with the same environment variable.

Tau appends JSONL records to:

```text
~/.tau/logs/llm-observations.jsonl
```

## Deferred work

Useful follow-ups are:

- streamed chunk capture with explicit retention limits
- a TUI view or export command for recent observations
- per-session or per-run controls beyond the environment variable
- an intentionally unsafe raw-body mode with stronger warnings, if needed
