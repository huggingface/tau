# Contributing to Tau

Thanks for helping improve Tau. Tau is both a usable terminal coding agent and a teaching codebase for understanding how coding agents are built. Contributions should preserve that dual purpose: make the tool better while keeping the architecture small, readable, and easy to learn from.

## Project philosophy

Tau is organized around three layers:

```text
tau_ai      provider/model streaming layer
tau_agent   portable agent harness, loop, tools, events, sessions
tau_coding  CLI app, resources, skills, extensions, commands, TUI integration
```

The key boundary is:

```text
AgentHarness = reusable agent brain
AgentSession = coding-agent environment
TUI = one possible frontend
```

Please keep these principles in mind:

- **Small layers beat magic.** Each package should have one clear job.
- **Events are the contract.** The harness emits typed events; UI and renderers consume them.
- **The core stays portable.** `tau_agent` should not depend on the CLI, Textual, Rich, local config paths, or Tau-specific resource loading.
- **Tools are ordinary typed functions.** Prefer explicit schemas and structured results.
- **Sessions are durable and inspectable.** Avoid changes that make history hard to read, resume, or export.
- **Documentation follows implementation.** User-facing behavior and architectural decisions should be documented.

## Local development

Use `uv` for Python commands so they run in the project environment.

```bash
uv sync --dev
uv run tau --version
```

Run Tau from the checkout:

```bash
uv run tau
uv run tau -p "explain this repo"
```

To expose the checkout as a global `tau` command, use:

```bash
uv tool install --editable --force .
```

Repeat that command after `git pull`. The editable link makes source changes
live, but uv must reinstall the tool to refresh package metadata (including the
version), dependencies, and entry points.

## Checks before submitting

Run the relevant focused tests while developing, then run the full checks before opening a pull request when practical:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For the documentation site (a [Hugo](https://gohugo.io/) project):

```bash
cd website
hugo server -D
hugo --minify
```

## Where changes belong

Use the layer boundaries to decide where code should live:

- Provider integrations, model adapters, and provider-neutral streaming belong in `tau_ai`.
- Agent loop behavior, tool abstractions, events, messages, harnesses, and portable session primitives belong in `tau_agent`.
- CLI behavior, slash commands, TUI integration, local config, resources, skills, prompt templates, and coding-specific tools belong in `tau_coding`.
- Textual-specific code should stay behind the TUI layer.
- Rich rendering should not leak into the reusable agent harness.

If a change crosses layers, prefer adding a small typed boundary instead of importing app-specific details into core code.

## Adding a provider or model

The built-in provider catalog is data, not code: edit
`src/tau_coding/data/catalog.toml` and open a PR — no Python changes needed.
Each `[[providers]]` table declares the provider's name, kind
(`openai-compatible`, `anthropic`, or `openai-codex`), base URL, models,
default model, context windows, and thinking configuration. Validation happens
at load time, so a typo fails tests with a pointed error message.

For personal or unreleased providers, create `~/.tau/catalog.toml` with the
same schema — it is overlaid on the built-in catalog (your values win, models
are unioned) and needs no PR at all.

## Testing expectations

- Add or update tests for behavior changes.
- Use fake providers and fake tools for deterministic agent-loop tests.
- Keep core tests free of provider-specific assumptions.
- Add regression tests for bugs.
- Prefer focused tests that describe the behavior being protected.

## Documentation expectations

For substantial architectural or phase-oriented work, add beginner-friendly notes under `dev-notes/` explaining:

- what changed
- why it exists
- how it maps to Tau's architecture
- how to test or use it

For user-facing behavior, update the published docs under:

```text
website/src/content/docs/
```

## Release process

Tau is published to PyPI as `tau-ai`. Publishing is a production release action,
not a side effect of every commit merged to `main`.

To prepare a release, intentionally bump `[project].version` in `pyproject.toml`
and merge that change through a pull request. The PyPI workflow publishes only
when it detects that version change, or when a maintainer uses an explicit
release trigger such as a published GitHub Release or manual workflow dispatch.
See [dev-notes/release-process.md](dev-notes/release-process.md) for the full
process.

## Pull request guidelines

Good Tau pull requests are small, focused, and easy to review. Please include:

- the motivation for the change
- a summary of behavior changes
- tests or checks you ran
- screenshots or terminal output for TUI/CLI changes when useful
- notes about compatibility, migrations, config changes, or provider-specific behavior

Avoid unrelated refactors in feature or bug-fix PRs. If a larger design change is needed, open an issue or discussion first.

## Roadmap alignment

Tau is developed incrementally. For larger changes, check the roadmap issue before starting:

<https://github.com/alejandro-ao/tau/issues/1>

When in doubt, favor the smallest step that preserves the architecture and teaches the design clearly.
