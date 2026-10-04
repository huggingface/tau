---
title: "Phase 6: Local inference hardening and second-backend validation"
---

Phase 6 closes the generic local-provider validation loop from issue #602. It
keeps the shipped implementation intentionally small: llama.cpp remains the
only built-in local backend, while a deterministic second fake and a test-only
Ollama adapter exercise the public seams.

## What changed

- A permanent fake second backend covers different normal, secret, and choice
  configuration fields, source-bound provider/backend layers, atomic failed
  configuration, cancellation, stale-result rejection, progress redaction, and
  optional capabilities.
- The Ollama adapter spike lives under `tests/fixtures/`; it is not imported by
  Tau production code and does not register an Ollama backend. It uses the
  official OpenAI-compatible `/v1/models` shape for provider discovery and the
  native `/api/tags` and `/api/ps` shapes for installed/running status.
- Refresh/reload stress tests cover coalescing, generation retirement, hostile
  cancellation, bounded cleanup, and late-result containment. Credential tests
  cover generation references, offline setup, state-file redaction, and
  cleanup-orphan behavior.
- A missing llama.cpp selection remains a stable reference rather than being
  overwritten by a newly discovered model. Status and model selection mark that
  snapshot stale. Explicit Doctor may probe a sole currently reported model for
  diagnostics, but it never changes the active selection.

## Contract pressure from Ollama

The spike did not require llama.cpp vocabulary in the generic APIs. It did
confirm three useful boundaries:

1. Provider discovery and local status can use different protocol endpoints.
   `DynamicProvider.refresh_models` returns one complete provider snapshot,
   while `LocalBackend.status` returns installed/running state through the
   open-ended `LocalModel.state` field.
2. `NoAuth` is a real use case. Ollama's local OpenAI-compatible endpoint does
   not need a synthetic key or an `Authorization` header.
3. Backend capability operations remain optional and host-renderable. The host
   owns fields, confirmation, cancellation, and rendering; protocol adapters
   return structured values and never construct Textual widgets.

The observed official API references are:

- <https://docs.ollama.com/api/openai-compatibility>
- <https://docs.ollama.com/api/tags>
- <https://docs.ollama.com/api/ps>

The spike deliberately does not promise native Ollama model management. It
only validates that an adapter with separate discovery/status endpoints fits the
provider-neutral contracts.

## Lifecycle and security decisions

Registries remain generation-local. Retirement invalidates source/layer tokens,
cancels owned work once, waits only through the documented bounded cancellation
window, and keeps a still-running callback supervised until it finishes. A late
callback cannot publish into a replacement generation. A prepared runtime owns
its provider and backend resources; a failed candidate closes only its own
resources, and final close is idempotent.

Built-in llama.cpp state is user-level, versioned, locked, private, and
atomically replaced. It stores only normalized endpoints, exact model IDs,
allowlisted metadata, a selected reference, and a timestamp. API keys remain in
the credential store or environment. The old `llama-cpp` catalog provider is
not migrated or overwritten, and Ollama remains a documented custom-provider
option rather than a shipped backend.

No Phase 7 router mutations are included. There is no production Ollama
backend, Hugging Face search/download, llama.cpp load/unload implementation, or
implicit model-file operation in this phase.

## Migration guidance

Existing manually configured OpenAI-compatible local providers continue to
work. To move a llama.cpp setup to the built-in integration:

1. Start the server independently.
2. Open `/local`, choose and confirm the recommended llama.cpp backend, and
   enter the endpoint and optional key.
3. Use the exact model ID returned by `/v1/models` in `/model` or with
   `--provider llama.cpp --model <id>`.
4. Remove an old `llama-cpp` catalog entry only after the new session works.

Tau does not copy old fake keys, fake model IDs, catalog definitions, project
settings, or environment endpoints into the built-in state. Reset removes only
built-in settings; server processes and model files are never touched.

## Verification

```bash
uv run pytest tests/test_local_backends.py tests/test_extension_providers.py \
  tests/test_llama_cpp_extension.py tests/test_phase6_hardening.py \
  tests/test_ollama_adapter_spike.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
