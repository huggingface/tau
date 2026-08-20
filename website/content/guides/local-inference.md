---
title: Local inference with llama.cpp
description: Configure Tau's built-in llama.cpp backend through /local.
---

Tau ships llama.cpp as a trusted, hidden built-in local backend. The
provider-neutral entry point is `/local`; there is no `/llama` or `/llama-cpp`
command. The built-in provider ID is `llama.cpp`, distinct from an older
user-created `llama-cpp` catalog provider.

## Start llama.cpp

Install llama.cpp separately and start its OpenAI-compatible server with a
model suitable for your work:

```bash
llama-server -hf <tool-capable-gguf>
```

The default endpoint is `http://127.0.0.1:8080`. Tau does not install, start,
stop, scan for, or download llama.cpp models. Keep the server running while Tau
uses it.

## Configure `/local`

Start Tau and enter:

```text
/local
```

Choose the recommended `llama.cpp` backend and confirm the choice, even when it
is the only backend. Enter the server URL with or without `/v1`, then optionally
enter an API key. Tau probes only the endpoint you provide; it never scans ports,
processes, or the local network.

Endpoint precedence is:

1. the endpoint currently entered in `/local`;
2. the saved endpoint;
3. `LLAMA_BASE_URL` for the current process;
4. `http://127.0.0.1:8080` as the offered default.

The default alone does not configure the backend or trigger a network request.
An explicit saved endpoint or non-empty `LLAMA_BASE_URL` counts as configured.
Successful setup discovers exact IDs from `/v1/models`; it never requires a fake
model ID.

### Authentication

Use the optional key in one of these ways:

- enter it through the secret `/local` field; Tau stores it in
  `~/.tau/credentials.json`;
- set `LLAMA_API_KEY` for an environment-only setup;
- leave both empty for an unauthenticated server.

A stored key wins over `LLAMA_API_KEY`. Without a key Tau sends no
`Authorization` header. Keys never enter the llama.cpp state file, sessions,
exports, or diagnostics. See [Project trust and security]({{< relref
"./project-trust.md#security-boundary" >}}).

## Choose a model

After setup, `/model` shows server-reported model IDs and display names. Start
explicitly when using scripts or a new TUI session:

```bash
tau --provider llama.cpp --model <model-id>
tau --provider llama.cpp --model <model-id> --print "summarize this project"
```

Both print mode and the TUI load the built-in provider before validating an
explicit selection. A saved safe snapshot can keep an explicit startup usable
while the server is temporarily down. If several models are available,
headless startup requires the exact `--model`; Tau never silently chooses a
different explicit model. Local inference is not an automatic global-provider
fallback.

The safe integration snapshot is stored at
`~/.tau/state/extensions/llama.cpp.json`. It contains only the normalized
endpoint, selected model reference, allowlisted model metadata, and a timestamp.
The file is versioned, locked, atomically replaced, and private. Dynamic provider
definitions are never copied into `catalog.toml` or `providers.json`.

## Status and Doctor

`/local` provides status and refresh. Refresh publishes a complete model
snapshot atomically. Temporary downtime retains the last safe snapshot and marks
it stale; it does not erase the active provider or block unrelated providers. If
the server stops reporting the active model, Tau keeps the current runtime
usable, marks the snapshot stale, and does not offer a replacement model without
an explicit selection after the original model returns.

Doctor is an explicit action. It reports endpoint reachability, model discovery,
streaming, tool-schema acceptance, and observed tool-call emission. A model that
streams but does not emit the probe tool receives a compatibility warning rather
than a connectivity failure. Use a tool-capable instruct model and the server's
required chat-template options when tool calls are unavailable.

## Reset

Reset removes only Tau's llama.cpp integration settings and safe snapshot. It
never stops the external server or deletes model files. Settings reset and stored
credential deletion are separate actions; a stored credential remains until its
separate deletion confirmation succeeds.

## Troubleshooting

- **Connection refused or timeout:** start `llama-server`, check the exact URL,
  and choose `/local` → Refresh. Tau does not discover another port.
- **HTTP 401/403:** enter the server's configured key in `/local`, or set
  `LLAMA_API_KEY`. A stored key wins over the environment value.
- **Loading:** wait for llama.cpp to finish loading, then refresh.
- **Malformed or empty `/v1/models`:** inspect the server response and model
  loading state. Tau does not invent an ID or metadata.
- **Print mode reports an unavailable model:** configure `/local` first and pass
  `--provider llama.cpp` plus the exact discovered `--model`. A cached snapshot
  can work offline, but a first-time explicit model needs discovery.
- **Tools are not called:** run Doctor. Streaming can work while a model's GGUF
  chat template does not support tools. Use a tool-capable instruct GGUF and
  check llama.cpp's template/server flags.
- **An old `llama-cpp` provider remains:** it is a separate manually configured
  provider. Use `llama.cpp` for the built-in backend, or retain the old provider
  for its existing catalog setup.

For other OpenAI-compatible endpoints, keep using [`/login custom`]({{< relref
"../guides/providers-and-models.md#adding-a-custom--local-provider" >}}) or
`tau setup`.

Router model management, Hugging Face search/download, and implicit
load/unload are not part of this phase. Standard OpenAI-compatible loaded-model
inference remains supported; mutating router workflows require a later phase.
