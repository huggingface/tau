---
title: Contributing
description: How Tau is developed, where the build journals live, and how to run the project locally.
type: doc
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
[How Tau works]({{< relref "./internals/architecture.md" >}}).

## Roadmap

The published, phase-by-phase roadmap and status lives on the
[roadmap page]({{< relref "./roadmap.md" >}}). The underlying checklist is
tracked in [GitHub issue #1](https://github.com/huggingface/tau/issues/1).

## Running the project locally

```bash
git clone https://github.com/huggingface/tau.git
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

The site (this site) is a [Hugo](https://gohugo.io/) project under `website/`:

```bash
cd website
hugo server -D    # http://localhost:1313/
hugo --minify     # static output in website/public/
```

User-facing docs live in `website/content/`; the landing and "Why Tau?" pages
are `website/content/_index.md` and `website/content/why-tau.md`, rendered by
templates in `website/layouts/`.

## Documentation expectations

Each substantial phase should leave beginner-friendly notes in `dev-notes/`
explaining what was added, why it exists, how it maps to [Pi](https://pi.dev)'s
design, and how to test or use it. When a feature is user-facing, also update or
add the relevant page under `website/content/`.
