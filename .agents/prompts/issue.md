---
description: Draft or create a structured GitHub issue for this repo.
---

Create a structured GitHub issue for this repository from the user's request:

{{ arguments }}

## Goal

Help contributors file complete, actionable issues for the remote repository. Be robust in incomplete local environments: contributors may not have `gh`, GitHub authentication, remotes, dependencies, or the project environment configured. Never treat that as fatal. Fall back to producing high-quality Markdown and clear manual next steps.

## Workflow

1. Inspect repository context when available:
   - repository root
   - current branch
   - remotes
   - default branch, usually `main`
   - existing issue templates or contribution docs
2. Classify the request as one of: bug, feature, docs, refactor, chore, question, or other.
3. Draft a concise, specific issue title.
4. Draft a complete issue body using the format below.
5. Suggest labels when obvious, but do not invent project-specific labels unless they already exist or the user provided them.
6. If `gh` is installed, authenticated, and the GitHub repository can be inferred, show the draft and ask for confirmation before creating the issue.
7. If `gh` is unavailable, unauthenticated, or repository metadata is missing, do not fail. Provide copy/paste-ready title/body plus manual browser instructions and an optional `gh issue create` command the contributor can run later.

## Issue body format

For feature requests:

```md
## Summary

## Motivation / problem

## Desired behavior

## Proposed direction

## Acceptance criteria

- [ ] 

## Notes / questions

## Related files or areas
```

For bug reports:

```md
## Bug summary

## Steps to reproduce

1. 

## Actual behavior

## Expected behavior

## Environment

- OS:
- Python version:
- Tau version / commit:

## Proposed direction

## Acceptance criteria / verification

- [ ] 

## Related files or areas
```

For docs, refactors, chores, or questions, adapt the sections while preserving:

- Summary
- Motivation / context
- Proposed direction or question
- Acceptance criteria / done when
- Related files or areas
- Open questions

## Creation guidance

When creating an issue with `gh`, prefer writing the body to a temporary file so formatting is preserved:

```bash
gh issue create --title "<title>" --body-file /tmp/tau-issue.md
```

Do not create the issue until the user confirms the final draft, unless the user explicitly asked to create it without confirmation.
