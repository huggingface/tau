---
description: Write developer notes for a completed phase or feature.
---

Write developer notes for:
{{ phase_or_feature }}

## Notes should go in `dev-notes/`

Use the appropriate subdirectory:
- `dev-notes/architecture/` — for phase implementation notes
- `dev-notes/design/` — for high-level design docs
- `dev-notes/adr/` — for architecture decision records

## Each note should explain

1. **What was added** — the concrete changes
2. **Why it exists** — the motivation and problem it solves
3. **How it maps to Tau's architecture** — which layer(s) it touches
4. **How to test or use it** — verification steps

## Format

Use Markdown with a clear title. Follow the pattern of existing phase notes in `dev-notes/architecture/`.

## After writing

- Run `uv run ruff check .` to ensure no formatting issues
- Commit: `docs: add notes for <phase_or_feature>`
