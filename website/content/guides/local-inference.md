---
title: Local backends

description: Configure and use provider-neutral local inference backends in Tau.
---

Tau's `/local` command is the interactive entry point for local inference. It is
backend-neutral: the host can present llama.cpp, Ollama, vLLM, LM Studio, or a
future extension without protocol-specific branches in the TUI.

## Open `/local`

Start Tau normally and enter:

```text
/local
```

Tau explicitly asks you to choose a registered backend. When there is one backend
it is preselected, and a recommended backend may be marked, but you still confirm
the choice. The backend screen then offers only the capabilities that backend
supports: configure, refresh, use, doctor, reset, and optional model actions.

Configuration forms are supplied as structured fields. Normal, secret, and choice
fields are rendered by Tau, while the backend performs validation and its
connection transaction. Cancelled or failed configuration leaves the previous
settings and active model untouched. Close the screen to cancel owned work; late
results from an old extension generation are ignored.

## Security and persistence

`/local` does not scan ports, processes, or the local network. A backend probes
only an endpoint the user configured or explicitly selected. Optional or absent
authentication does not receive a fake API key. Secret fields are not written to
session history, diagnostics, or safe integration state.

Dynamic local providers are runtime overlays, not catalog entries. A backend may
store endpoint metadata or a safe model snapshot in a backend-owned user-level
state store, but provider definitions and credentials remain separate. Reset
never stops an external server or deletes model files.

## Headless use

`/local` is interactive-only. Print mode reports that limitation rather than
starting setup, probing endpoints, or selecting a model implicitly. Configure a
backend in the TUI, then select it explicitly:

```bash
tau --provider <provider-id> --model <model-id> -p "summarize this project"
```

The generic host in this phase is the contract used by built-in and extension
backends. The backend's own guide should document protocol-specific setup and
troubleshooting. Existing custom OpenAI-compatible providers continue to use
[`tau setup`]({{< relref "../reference/cli.md#provider-setup-options" >}}).
