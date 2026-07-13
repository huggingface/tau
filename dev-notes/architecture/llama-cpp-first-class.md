# First-class llama.cpp integration

## What was added

Tau now includes a built-in `llama-cpp` provider and dedicated commands:

```bash
tau llama-cpp setup
tau llama-cpp doctor
```

Setup connects to a running llama.cpp server, queries its OpenAI-compatible
`/v1/models` endpoint, and persists the discovered models and selected default.
Doctor checks discovery, streaming chat completions, and tool-call support.

## Why it exists

llama.cpp was previously documented as a generic custom endpoint. Users had to
invent a model ID and configure a fake API key even though llama.cpp does not
enforce authentication by default. That made a supported local runtime feel
like an unsupported workaround.

## Architecture

The split remains:

- `tau_ai` supports OpenAI-compatible endpoints whose bearer token is optional.
- `tau_coding.llama_cpp` owns llama.cpp discovery and diagnostics.
- `tau_coding` owns CLI setup and durable provider preferences.
- `tau_agent` remains unaware of llama.cpp.

There is intentionally no `LlamaCppProvider` adapter. llama.cpp speaks the
OpenAI chat-completions protocol already, so Tau reuses
`OpenAICompatibleProvider`. A dedicated adapter should only be introduced if a
future llama.cpp behavior cannot be represented by compatibility metadata.

## Provider metadata

Provider catalog entries now declare:

- `auth = "required" | "optional" | "none"`
- `model_discovery = "static" | "openai"`

The built-in llama.cpp entry uses optional auth and OpenAI model discovery. Tau
omits the `Authorization` header when no key is available, but still supports
servers launched with `llama-server --api-key` through `LLAMA_API_KEY` or a
stored credential.

## Testing

```bash
uv run pytest tests/test_llama_cpp.py tests/test_provider_catalog.py \
  tests/test_provider_config.py tests/test_provider_runtime.py tests/test_cli.py \
  tests/test_tau_ai.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For a live smoke test:

```bash
llama-server -hf <tool-capable-gguf>
tau llama-cpp setup
tau llama-cpp doctor
tau --provider llama-cpp -p "Use the bash tool to print the current directory"
```
