---
description: Fix a bug from a GitHub issue with reproduction steps.
---

Fix the bug described in this GitHub issue:
{{ issue_url }}

## Workflow

1. Read the issue and understand the bug and reproduction steps
2. Reproduce the bug locally using `uv run tau`
3. Identify the root cause in the relevant code file
4. Write a failing test that captures the bug behavior
5. Fix the bug — make the minimal change that resolves it
6. Verify the test passes
7. Run the full test suite:
   ```
   uv run pytest
   ```
8. Update `dev-notes/` if the fix is non-trivial
9. Commit with a clear message: `fix: short description`
10. Open a PR referencing the issue: `Fixes #<number>`
