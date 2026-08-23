# Phase 7: llama.cpp router management and scoped models

Phase 7 completes issue #602 without changing the Phase 5 single-model path.

## What changed

- The llama.cpp package capability-detects router mode through `/props` and
  enables mutations only for the official API contract tested across builds
  **b9688 through b10595**. Unknown, malformed, older, and newer routers degrade
  to standard `/v1/models` discovery.
- Compatible routers expose all server model states through `/local`. Only
  `loaded` and `sleeping` models publish into the dynamic inference provider.
- Load, unload, and server-side `owner/repository[:quantization]` download use
  documented `/models`, `/models/load`, and `/models/unload` requests. Load
  waits for reconciled loaded/sleeping state. Unload and download require
  confirmation; loading with active peers asks whether to keep or unload them.
- Polling emits bounded progress. Cancellation requests the documented unload
  operation for load/download and refreshes state. Timeout or connection loss
  attempts refresh and never replays a mutation.
- Hugging Face search/details use its public API through the injected HTTP
  client. Results include repository gating and GGUF quantization/size data;
  `Q4_K_M` is only a recommended UI option.
- Search discovers `HF_TOKEN` from the environment or standard token files. It
  is never stored or forwarded. Gated diagnostics explain that the independent
  llama.cpp process separately needs authorized server-side credentials.
- The trusted built-in provider opts into stable scoped references. Tau stores
  only `llama.cpp` plus the exact model ID. Unloaded/stale rows remain visible
  as unavailable, cannot be selected/cycled, and cause no discovery or mutation.
- Generic `/local` contracts gained backend-neutral search artifacts and
  structured confirmations. After explicit backend confirmation, the Textual
  host probes its effective endpoint, renders model states and actions in an
  arrow-key navigable picker, and lets users select search variants for download
  without router, Hugging Face, GGUF, or quantization branches in host logic.
  Expensive load/download operations use backend-owned confirmations with Cancel
  preselected.

Tau never calls llama.cpp's delete endpoint.

## Why this maps to Pi

Pi keeps protocol-specific model management in its llama extension while its
host renders extension-owned actions. Tau preserves the same boundary:

```text
tau_ai       unchanged OpenAI-compatible inference transport
tau_agent    unchanged provider-neutral harness
tau_coding   generic local actions, confirmations, progress, scoped references
llama_cpp    router/Hugging Face URLs, parsing, policy, reconciliation
```

Tau deliberately differs by supporting ordinary single-model servers, using a
provider-neutral `/local`, version-gating mutations, and never silently
unloading a shared router. Dynamic definitions remain process-local.

## Failure and security behavior

A mutation is sent at most once per explicit operation. Retry means refresh and
then a new user decision, not replay. Provider snapshots publish after observed
state transitions. If reconciliation also loses the connection, cached state is
marked stale and the user is told to refresh before retrying.

The llama.cpp API key and Hugging Face token are independent. Neither enters
safe state, sessions, exports, diagnostics, or search results. Hugging Face
gating terms and server-side token responsibility are shown without printing a
token.

## How to test

All HTTP behavior is deterministic through `httpx.MockTransport`:

```bash
uv run pytest tests/test_llama_cpp_extension.py -q
uv run pytest tests/test_local_backends.py tests/test_tui_local_backends.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
```

The fake router covers version fallback, state parsing, confirmation, load,
unload, download, cancellation, connection loss, non-replay, provider refresh,
and stale scoped references. The fake Hugging Face API covers gating,
quantizations, sizes, recommendation, and search-only token use. No test makes a
real network request.
