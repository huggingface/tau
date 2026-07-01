---
description: Add or update tests for a specific area of Tau.
---

Add or update tests for:
{{ area }}

## Testing conventions

- Use fake providers and fake tools for deterministic agent-loop tests
- Keep core tests free of provider-specific assumptions
- Add regression tests for bugs
- Prefer focused tests that describe the behavior being protected
- Tests go in `tests/` alongside the code they protect

## Steps

1. Find existing tests for the area (or create new test files)
2. Understand what behavior needs testing
3. Write tests using the existing patterns in the codebase
4. Run the tests: `uv run pytest tests/`
5. Ensure all tests pass
6. Commit: `test: add tests for <area>`
