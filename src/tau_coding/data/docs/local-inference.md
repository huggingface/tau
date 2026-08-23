# Local inference with llama.cpp

Tau ships llama.cpp as a trusted, hidden built-in local backend. It is exposed
through the provider-neutral `/local` command; there is no `/llama` or
`/llama-cpp` command. The built-in provider ID is `llama.cpp`, which is distinct
from an older user-created `llama-cpp` catalog provider.

## Start a server

Install llama.cpp separately and start its OpenAI-compatible server with a
model that supports the work you want to do:

```bash
llama-server -hf <tool-capable-gguf>
```

The default endpoint is `http://127.0.0.1:8080`. Tau does not install, start,
stop, or scan for llama.cpp. In compatible router mode it can explicitly ask the
independent server to download a model; Tau never writes or deletes model files.

## Configure `/local`

Open Tau and run:

```text
/local
```

Choose the recommended `llama.cpp` backend and confirm the choice, even when it
is the only backend. Enter the server URL, with or without `/v1`, and optionally
enter an API key. Tau probes only the endpoint you provide. It never scans ports,
processes, or the local network.

Endpoint precedence is:

1. the value currently entered in `/local`;
2. the saved endpoint;
3. `LLAMA_BASE_URL` for the current process;
4. `http://127.0.0.1:8080` as the offered default.

The default alone does not configure the backend or cause a network request. An
explicitly saved endpoint or non-empty `LLAMA_BASE_URL` counts as configured.
Successful setup discovers the exact IDs returned by `/v1/models`; it never
requires a fake model ID.

Use the optional key in one of these ways:

- enter it in the secret `/local` field; Tau stores it in `~/.tau/credentials.json`;
- set `LLAMA_API_KEY` for an environment-only setup;
- leave both empty for an unauthenticated server.

A stored key takes precedence over `LLAMA_API_KEY`. Without a key Tau sends no
`Authorization` header. Keys are never written to the llama.cpp state file,
sessions, exports, or diagnostics.

## Select and use a model

After setup:

```text
/model
```

The picker shows server-reported model IDs and display names. Use an explicit
provider and exact model ID for startup, especially in scripts:

```bash
tau --provider llama.cpp --model <model-id>
tau --provider llama.cpp --model <model-id> --print "summarize this project"
```

Both print mode and the TUI load the built-in provider before validating an
explicit selection. A saved safe snapshot lets an explicit startup continue
when the server is temporarily down. If there are several models, headless
startup requires the exact `--model`; Tau never silently picks a different
explicit model. Local inference is not an automatic global-provider fallback.

The safe integration snapshot is stored at:

```text
~/.tau/state/extensions/llama.cpp.json
```

It contains only the endpoint, selected model reference, allowlisted model
metadata, and a timestamp. The file is versioned, locked, atomically replaced,
and private. Dynamic provider definitions are not copied into `catalog.toml` or
`providers.json`.

## Router management and scoped models

Refresh enables management only when `/props` identifies a router in Tau's
tested llama.cpp build range, **b9688–b10595**. Unknown/incompatible routers
fall back to standard `/v1/models` discovery without mutation controls.
Single-model servers remain fully supported.

`/local` lists every server-reported router state, while only loaded and sleeping
models appear in `/model`. Load, unload, and server-side download are explicit;
unload and download require confirmation, and loading asks whether to keep or
unload other active shared-router models. Cancellation uses llama.cpp's
documented unload operation and refreshes state. Connection loss also refreshes
when possible and never replays an interrupted mutation.

Search queries Hugging Face GGUF repositories and reports gating, quantizations,
and sizes. `Q4_K_M` is a UI recommendation only. Tau discovers `HF_TOKEN` from
the environment or standard Hugging Face token files for search, but never saves
or forwards it. The independent llama.cpp process separately needs its own token
to download gated repositories after their terms are accepted.

Loaded/sleeping models can be toggled through `/scoped-models`. Only the exact
`llama.cpp` provider/model pair is persisted. If unloaded later, the reference
stays visible as unavailable and cannot synthesize availability or trigger a
load/download; remove it or explicitly manage it through `/local`.

## Status, refresh, doctor, and reset

`/local` provides status and refresh. Refresh updates the complete model
snapshot atomically. Temporary downtime retains the last safe snapshot and marks
it stale; it does not erase the active provider or block unrelated providers.
If the server no longer reports the active model, Tau keeps the current runtime
usable, marks the snapshot stale, and does not offer a replacement model without
an explicit selection after the original model returns.

`doctor` is an explicit action. It reports endpoint reachability, model discovery,
streaming, tool-schema acceptance, and observed tool-call emission. A model that
streams but does not emit the probe tool receives a compatibility warning, not a
connectivity failure. Use a tool-capable instruct model and the server's required
chat-template options when tool calls are unavailable.

Reset removes only Tau's llama.cpp integration settings and safe snapshots. It
never stops the external server or deletes model files. Settings reset and stored
credential deletion are separate actions; a credential is retained until its
separate deletion confirmation succeeds.

## Troubleshooting

- **Connection refused or timeout:** start `llama-server`, check the exact URL,
  and run `/local` → Refresh. Tau does not discover a different port.
- **HTTP 401/403:** enter the server's configured key in `/local`, or set
  `LLAMA_API_KEY`. A stored key wins over the environment value.
- **Loading:** wait for llama.cpp to finish loading, then refresh.
- **Malformed or empty `/v1/models`:** inspect the server response and model
  loading state. Tau does not invent an ID or metadata.
- **Print mode says the model is unavailable:** configure `/local` first and
  pass both `--provider llama.cpp` and the exact discovered `--model`. A cached
  snapshot can work offline, but a first-time explicit model needs discovery.
- **Tools are not called:** run Doctor. Streaming can work while a model's GGUF
  chat template does not support tools. Use a tool-capable instruct GGUF and
  check llama.cpp's template/server flags.
- **An old `llama-cpp` provider still exists:** it is a separate manually
  configured provider. Use `llama.cpp` for the built-in backend, or keep the old
  provider for its existing catalog configuration.

## Migration from manual local providers

Existing custom OpenAI-compatible providers continue to work. To move a
manually configured llama.cpp server to the built-in integration, open
`/local`, choose and confirm the recommended backend, enter the endpoint and
optional key, then use the exact ID returned by `/v1/models`. The built-in
provider ID is `llama.cpp`; an older `llama-cpp` catalog entry is not migrated,
rewritten, or removed automatically. Remove that entry only after verifying the
new session. Ollama and other local servers remain supported through the custom
provider path and are not registered as Tau backends.

Tau never copies old fake keys, fake model IDs, catalog definitions, project
settings, or environment endpoints into the built-in state. Reset removes only
built-in settings and safe snapshots; it never stops a server or deletes model
files.

For project trust and the security boundary, see `security.md`. For command
flags and print/TUI startup, see `cli.md`, `tui.md`, and `models.md`.

Router actions never silently load, unload, download, restore, or delete
anything. Review refreshed shared-router state before every manual retry.
