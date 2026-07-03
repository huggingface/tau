---
description: Draft or create a structured GitHub pull request for this repo.
---

Draft or create a structured pull request for the current branch.

User context:

{{ arguments }}

## Goal

Help contributors submit complete, easy-to-review PRs to the remote repository. Be robust in incomplete local environments: contributors may not have `gh`, GitHub authentication, remotes, dependencies, or the project environment configured. Never treat that as fatal. Fall back to producing high-quality Markdown and clear manual next steps.

## Workflow

1. Inspect git and repository state:
   - repository root
   - current branch
   - remotes
   - default/base branch, usually `main`
   - uncommitted changes
   - upstream branch
   - commits and diff against the base branch
   - contribution docs and existing PR template, if present
2. If the branch has uncommitted changes, warn the user. Continue with a draft only unless the user explicitly says those changes should be included.
3. Infer the PR title, motivation, changed behavior, and risk from the commits and diff.
4. Check whether relevant tests, docs, or screenshots are needed.
5. Run focused validation when practical and safe. For Tau, prefer `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy` when appropriate and available.
6. Draft a complete PR title and body using the format below.
7. If `gh` is installed, authenticated, and the GitHub repository can be inferred, show the draft and ask for confirmation before creating the PR.
8. If `gh` is unavailable, unauthenticated, or repository metadata is missing, do not fail. Provide copy/paste-ready title/body, a GitHub compare URL when inferable, and an optional `gh pr create` command the contributor can run later.

## PR body format

```md
## Summary

- 

## Why this change is needed

## What changed

## Testing / validation

- [ ] Not run; reason:

## Screenshots or recordings

<!-- Include for TUI, CLI, website, or visual changes when useful. -->

## Breaking changes / migration notes

## Related issue(s)

Closes #

## Reviewer notes

## Checklist

- [ ] The PR is focused and avoids unrelated changes.
- [ ] Tests were added or updated, if applicable.
- [ ] Documentation was updated, if user-facing behavior changed.
- [ ] Relevant validation was run, or a reason is listed above.
- [ ] Related issue(s) are linked, if applicable.
```

Remove sections that are genuinely not applicable only after confirming the PR remains clear.

## Creation guidance

When creating a PR with `gh`, prefer writing the body to a temporary file so formatting is preserved:

```bash
gh pr create --base <base-branch> --head <current-branch> --title "<title>" --body-file /tmp/tau-pr.md
```

Do not create the PR until the user confirms the final draft, unless the user explicitly asked to create it without confirmation.
