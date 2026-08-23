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
stop, or scan for llama.cpp. Keep the server running while Tau uses it. In
compatible router mode, Tau can explicitly ask that independent server to
download a selected Hugging Face model; Tau itself never writes or deletes the
model file.

## Configure `/local`

Start Tau and enter:

```text
/local
```

Choose the recommended `llama.cpp` backend and confirm the choice, even when it
is the only backend. Tau immediately probes the saved endpoint,
`LLAMA_BASE_URL`, or the default `http://127.0.0.1:8080`. Use **Configure** for a
server URL elsewhere (with or without `/v1`) and an optional API key. Tau probes
only that one effective endpoint; it never scans ports, processes, or the local
network.

Endpoint precedence is:

1. a URL submitted through **Configure**;
2. the saved endpoint;
3. `LLAMA_BASE_URL` for the current process;
4. `http://127.0.0.1:8080` as the offered default.

Opening the confirmed backend triggers the probe. Probing the offered default
makes discovered models available for the current Tau process but does not save
the endpoint; use **Configure** to persist it. Successful discovery uses exact
IDs from `/v1/models` and never requires a fake model ID.

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

## Router model management

A current `llama-server` started without a model runs in router mode. Tau enables
management only when `/props` identifies the router and its `build_info` is in
the tested **b9688–b10595** range. Unknown, older, or newer builds safely degrade
to `/v1/models` discovery: inference remains available, but Tau sends no router
mutation. Single-model servers remain fully supported.

After the automatic probe or Refresh confirms a compatible router, `/local`
lists loaded, sleeping, unloaded, loading, downloading, failed, and unknown
server states in a dedicated model section. A separate actions section contains
Hugging Face search/download, configuration, refresh, Doctor, and reset. Arrow
keys move within and between both sections. Only loaded or sleeping models enter
`/model`. Press Enter on an unloaded row to review a confirmation before loading;
Enter on a loaded/sleeping row offers use or unload. Actions are always explicit:

- Confirming an unloaded row waits until refreshed router state reports loaded
  or sleeping. If other models are active, choose whether to keep or unload
  them; Tau never decides for a shared router. Cancel is preselected as a safety
  default but is not labelled as recommended.
- **Unload model** asks for model-specific confirmation.
- **Search Hugging Face models…** accepts a model ID or search text, then opens
  an arrow-key navigable repository/quantization list with gating and reported
  sizes. `Q4_K_M` is marked recommended only as a UI preference, not persisted
  model metadata.
- **Download an exact Hugging Face model…** accepts
  `owner/repository[:quantization]`. Both search and exact-ID paths open a
  separate confirmation showing the selected model and known size before
  requesting the server-side download. During transfer, `/local` shows a
  full-width block progress bar, transferred bytes, and bytes remaining. Closing
  `/local` detaches from the transfer without stopping llama.cpp. Reopening it
  refreshes server status, reattaches to the latest byte progress, and offers
  **Cancel active download…**
  to stop the download explicitly. The
  completed model appears as an unloaded row ready to select.

Progress is bounded and explicitly cancellable. llama.cpp documents
`/models/unload` as the cancel operation for load/download, so Tau requests it
and then refreshes. On a
timeout or lost connection Tau refreshes if possible and never replays the
interrupted POST; review state before manually retrying. Tau never restores,
unloads, downloads, or deletes a model without a displayed decision.

### Hugging Face tokens and gated repositories

Tau uses `HF_TOKEN`, `$HF_HOME/token`, or the standard Hugging Face token file
only for Hugging Face **search/details** requests. It does not save this token,
copy it into integration state, or forward it to llama.cpp. A gated repository
requires accepting its terms on `huggingface.co`.

The independently running llama.cpp server performs downloads, so that server
process separately needs an authorized `HF_TOKEN` in its own environment. A Tau
search succeeding does not prove the server can download a gated model.

## Scoped llama.cpp models

Loaded or sleeping llama.cpp models can be added and removed through
`/scoped-models`, then selected from `/model` or cycled like ordinary scoped
models. Tau persists only `{provider: "llama.cpp", model: "<exact-id>"}`; the
dynamic provider definition, endpoint, credentials, and metadata stay out of
`providers.json`.

If another client unloads the model, its scoped row remains visible as
**unavailable**. It is inert: selecting or cycling cannot synthesize a model,
contact Hugging Face, or trigger router load/download. Remove it through
`/scoped-models`, or load the model explicitly through `/local` and Refresh.

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
- **`model limit reached` during download:** the shared router rejected the
  request. Review `/local`; unload another model only if intended, or try again
  later. Tau refreshes state and shows the router's exact rejection message.
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

## Migration from manual local providers

Existing custom OpenAI-compatible providers continue to work. To move a
manually configured llama.cpp server to the built-in integration, open
`/local`, choose and confirm the recommended backend, enter the endpoint and
optional key, then use the exact ID returned by `/v1/models`. The built-in
provider ID is `llama.cpp`; an older `llama-cpp` catalog entry is not migrated,
rewritten, or removed automatically. Remove it only after verifying the new
session. Ollama and other local servers remain on the custom-provider path and
are not shipped Tau backends.

Tau never copies old fake keys, fake model IDs, catalog definitions, project
settings, or environment endpoints into built-in state. Reset removes only
built-in settings and safe snapshots; it never stops a server or deletes model
files. For other OpenAI-compatible endpoints, keep using [`/login custom`]({{<
relref "../guides/providers-and-models.md#adding-a-custom--local-provider" >}})
or `tau setup`.

Router management never changes the safety boundary: all mutations are explicit,
model files are never deleted, and standard OpenAI-compatible single-model
inference remains supported.
