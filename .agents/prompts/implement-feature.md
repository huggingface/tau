---
description: Implement a feature from a GitHub issue in an isolated worktree.
---

Implement the feature described in this GitHub issue:
{{ issue_url }}

## Workflow

1. Read the issue carefully and understand the acceptance criteria
2. Create a branch following naming conventions: `feat/issue-<number>-short-description`
3. Implement the feature following Tau's architecture:
   - Keep `tau_ai`, `tau_agent`, and `tau_coding` separated
   - Provider/model streaming belongs in `tau_ai`
   - Agent loop, tools, events, sessions belong in `tau_agent`
   - CLI, TUI, resources, skills belong in `tau_coding`
4. Add or update tests for behavior changes
5. Run checks through `uv`:
   ```
   uv run pytest
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy
   ```
6. Update `dev-notes/` if the change is substantial
7. Update `website/src/content/docs/` if user-facing
8. Commit with a clear message: `feat: short description`
9. Open a PR referencing the issue: `Fixes #<number>`
