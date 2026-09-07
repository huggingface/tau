# Phase 5: built-in llama.cpp connection and inference

Phase 5 adds a useful first local-inference path without adding llama.cpp
branches to the agent harness or implementing router mutations.

## What changed

- `llama.cpp` is declared as a trusted, hidden built-in extension.
- The extension registers a dormant dynamic provider and a generic `/local`
  backend through the normal extension contracts.
- `/local` configures a normalized server endpoint, optional API key, status,
  refresh, Doctor, and safe reset.
- `/v1/models` discovery publishes exact server model IDs and only allowlisted
  metadata. Unknown context, output, reasoning, modality, pricing, and tool
  compatibility remain unknown.
- The existing OpenAI-compatible streaming provider is reused with an explicit
  local API choice, so IDs resembling `gpt-*` or `codex-*` do not select a
  first-party endpoint.
- Print and TUI explicit startup use the shared staged preparation path. A
  cached snapshot can keep an explicit local startup usable while the server is
  down; local inference is never an implicit ordinary-provider fallback.
- State is stored in the locked, private,
  `~/.tau/state/extensions/llama.cpp.json` integration file. Keys remain in the
  credential store or `LLAMA_API_KEY`, and no fake key is synthesized.
- Existing user `llama-cpp` catalog providers remain separate from built-in
  `llama.cpp`.

## Why this maps to Pi

Pi treats built-in integrations as product extensions that register provider
objects and own protocol-specific behavior. Tau follows that separation while
keeping its reusable layers independent:

```text
tau_ai       OpenAI-compatible transport and streaming
tau_agent    portable harness, tools, events, sessions
tau_coding   trusted extension, provider overlays, /local, state, TUI/CLI
```

The extension owns URL parsing, `/health`, `/v1/models`, authentication
precedence, safe snapshot conversion, diagnostics, Doctor probes, and reset.
The generic registry owns source/generation lifetime and snapshot publication.
The TUI owns field rendering, confirmation, cancellation, and idle checks.

The accepted plan's later router phase is intentionally not included here:
there are no load, unload, Hugging Face search/download, or implicit model-file
mutations in this implementation.

## Failure safety

Configuration writes a generation-specific credential before committing the safe
state reference. If state commit fails, the old configuration stays active and
the new credential is deleted or reported as an integration-owned orphan. After
a successful commit, failure to delete the old credential reports cleanup while
leaving the new configuration usable. Reset removes safe state first and treats
credential deletion as a separate confirmation.

Refresh publishes a complete snapshot only after defensive parsing. Timeout,
HTTP failure, cancellation, malformed data, and endpoint downtime retain the
last safe snapshot and produce bounded diagnostics. If a refreshed catalog loses
the active model, the current runtime remains usable and the local status is
stale; Tau does not silently select the remaining model.

## How to test

The deterministic suite uses `httpx.MockTransport` and fake credential/state
stores. It covers endpoint safety, auth headers, cache/offline behavior,
malformed discovery, metadata allowlisting, stale model handling, atomic state
writes, orphan cleanup, reset, Doctor, real runtime registration, generation
retirement, and explicit print/TUI startup:

```bash
uv run pytest tests/test_llama_cpp_extension.py -q
uv run pytest tests/test_local_backends.py tests/test_tui_local_backends.py -q
```

For a manual smoke test, start a tool-capable llama.cpp server, configure
`/local`, choose the discovered ID in `/model`, run Doctor, and then try:

```bash
tau --provider llama.cpp --model <model-id> --print "summarize this project"
```

Stop the server after setup and repeat the explicit startup to verify the safe
snapshot path. Never put real API keys or private model endpoints in tests,
session exports, or documentation.
