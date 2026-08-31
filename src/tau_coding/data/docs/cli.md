# Tau CLI and commands

Tau supports print mode, Pi-compatible JSONL RPC mode, and a Textual interactive
TUI. `tau` opens the TUI by default. Print mode is selected with `-p/--print` or
`--mode` and uses the same staged session/provider preparation as the TUI. The
CLI entry point is `tau_coding.cli:app`.

## Local inference

`/local` is interactive-only. It opens the provider-neutral local-backend host
in the TUI; print mode never runs setup, probes endpoints, or picks a model
implicitly. Configure llama.cpp through `/local`, then use its exact provider
and model explicitly in headless mode:

```bash
tau --provider llama.cpp --model <server-reported-id> --print "summarize this project"
```

The built-in provider is loaded before explicit provider/model validation, so
both print and TUI startup work after setup. A saved safe snapshot can allow
startup while the server is temporarily unavailable. A first-time explicit
model still requires a successful discovery; an unavailable built-in backend
never blocks unrelated ordinary-provider startup.

See `local-inference.md` for endpoint precedence, optional/no authentication,
state and credential storage, Doctor, reset, and troubleshooting.

## Common flags

```text
tau [OPTIONS] [PROMPT]
```

- `-p, --print`: run one prompt without the TUI.
- `--mode text|json|transcript`: choose print output and imply print mode.
- `--provider NAME`: select an explicit provider.
- `-m, --model ID`: select an explicit model.
- `-t, --thinking LEVEL`: set the initial thinking level for this run
  (`off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`). Overrides
  remembered and catalog defaults without persisting them; an unsupported level
  for the selected model is an error listing the available modes.
- `--session ID`: resume a session in the TUI or print mode.
- `--cwd PATH`: set the coding-session working directory.
- `-e, --extension PATH`: load an explicit extension.
- `--no-extensions`: disable discovered extension directories; trusted built-ins
  remain available.
- `--project-extensions`: opt in to trusted project extensions after approval.
- `-a, --approve` / `-na, --no-approve`: run-only project-trust decisions.

Explicit `--provider` and `--model` overrides take precedence over a resumed
provider-aware transcript entry. Print mode reports actionable errors instead of
opening an interactive login or local setup flow.

## Custom provider setup

```bash
tau --provider company-ai \
  --base-url https://ai.company.example/v1 \
  --model company/openai/gpt-5.6 \
  --api openai-responses \
  setup
```

`--api` accepts the exact protocol names `openai-completions` (the default) and
`openai-responses`. Setup saves the capability to `~/.tau/catalog.toml`;
`~/.tau/providers.json` remains preference-only. `/login custom` exposes the
same choice interactively.

## Model catalog refresh

```bash
tau update --models
```

This forces ETag revalidation of models.dev and the live NVIDIA model filter,
then atomically caches the transformed catalog at `~/.tau/models-store.json`.
Opening `/model` performs the same refresh in the background, subject to a
four-hour freshness window. Cached/bundled models remain available on failure;
set `TAU_OFFLINE=1` to disable catalog network access.

## Safety boundary

Project trust controls ambient project-resource loading; it is not a sandbox.
Built-in local backends are trusted package code and do not create a project
trust prompt. They probe only configured endpoints and never stop external
servers or delete model files. See `security.md`.
