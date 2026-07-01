---
description: Update website documentation for user-facing changes.
---

Update the website documentation for:
{{ change }}

## Documentation lives in `website/src/content/docs/`

Choose the right location:
- `guides/` — how-to guides for features
- `reference/` — CLI, configuration, tools reference
- `internals/` — architecture and design docs
- `concepts.md` — high-level concepts

## Steps

1. Read the existing docs for the area
2. Update or add the relevant page
3. Keep language clear and beginner-friendly
4. Include code examples where helpful
5. Test the docs site builds:
   ```
   cd website
   bun install
   bun run build
   ```
6. Commit: `docs: update <area> for <change>`
