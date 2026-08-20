# Tau CLI and commands

`tau` opens the interactive TUI by default. Print mode is selected with
`-p/--print` or `--mode` and uses the same staged session/provider preparation
as the TUI.

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

## Safety boundary

Project trust controls ambient project-resource loading; it is not a sandbox.
Built-in local backends are trusted package code and do not create a project
trust prompt. They probe only configured endpoints and never stop external
servers or delete model files. See `security.md`.
