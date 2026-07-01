---
title: Contributing
description: How Tau is developed, where the build journals live, and how to run the project locally.
---

Tau is built in small, documented phases — partly to ship a usable agent, partly
so the codebase reads as a teaching example of how a coding agent is assembled.

## The build journals

The detailed, phase-by-phase implementation notes, design docs, and architecture
decision records live **in the repository**, under `dev-notes/`:

- `dev-notes/design/` — the high-level design docs (`00-roadmap`, `01-architecture`, …).
- `dev-notes/architecture/` — per-phase build notes (`phase-1` … `phase-24`), each
  answering: what was added, why it exists, how later phases use it.
- `dev-notes/adr/` — architecture decision records.

These are intentionally **not** published on this site — they're contributor
material. The published docs distill the result; see
[How Tau works](./internals/architecture.md).

## Roadmap

The roadmap and phase status are tracked in
[GitHub issue #1](https://github.com/alejandro-ao/tau/issues/1).

## Running the project locally

```bash
git clone https://github.com/alejandro-ao/tau.git
cd tau
uv sync --dev
uv run tau --version
```

Checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## The docs site

The site (this site) is an [Astro Starlight](https://starlight.astro.build/)
project under `website/`:

```bash
cd website
bun install
bun run dev      # http://localhost:4321/tau/
bun run build    # static output in website/dist/
```

User-facing docs live in `website/src/content/docs/`; the landing and "Why Tau?"
pages are standalone routes in `website/src/pages/`.

## Contributor workflow

For a complete contributor guide — including Git worktrees, branch naming
conventions, and reusable prompt templates — see
[CONTRIBUTING.md](https://github.com/huggingface/tau/blob/main/CONTRIBUTING.md)
in the repository root.

Tau ships with contributor prompt templates in `.agents/prompts/` for common
tasks like implementing features, fixing bugs, writing dev notes, and updating
docs. These help coding agents follow Tau's conventions automatically.

## Documentation expectations

Each substantial phase should leave beginner-friendly notes in `dev-notes/`
explaining what was added, why it exists, how it maps to Pi's design, and how to
test or use it. When a feature is user-facing, also update or add the relevant
page under `website/src/content/docs/`.
