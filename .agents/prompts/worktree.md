---
description: Implement a Tau change from an issue or description in an isolated git worktree.
---

Implement the feature below in a **new git worktree**. The feature may be a description, an issue number, or an issue URL.

Feature description or GitHub issue:

{{ arguments }}

## Goal

Help contributors go from a GitHub issue or feature description to a focused branch and pull request without disturbing their main checkout. Be robust in incomplete local environments: contributors may not have `gh`, GitHub authentication, writable remotes, dependencies, or the project environment configured. Never treat that as fatal. Fall back to clear manual steps and copy/paste-ready commands.

## Worktree location

Create worktrees under:

```text
~/.agents/worktrees/<repo-name>/<feature-slug>/
```

Where:

- `<repo-name>` is the basename of the repository root.
- `<feature-slug>` is a short kebab-case description, such as `add-user-auth` or `fix-session-resume`.

Rules:

- Never create a worktree inside or adjacent to the repository.
- If the target directory already exists, choose another slug or ask the user.
- Perform all edits, testing, commits, pushes, and PR creation from inside the feature worktree.
- Use the original working tree only for read-only discovery, such as `git status --short`, `git remote -v`, and `git rev-parse --show-toplevel`.

## Workflow

1. Inspect repository context:
   - repository root
   - current branch
   - remotes
   - default branch, usually `main`
   - whether `gh` is installed and authenticated
2. If the input is an issue number or URL, read the issue with `gh issue view` when available. If `gh` is unavailable, continue from the user-provided text and mention the limitation.
3. Create a branch name matching the slug, prefixed with `feat/`, `fix/`, `docs/`, or `chore/`.
4. Fetch the latest default branch and create the worktree:

```bash
git fetch origin <default-branch>
git worktree add -b <branch> <worktree-path> origin/<default-branch>
```

5. Implement the change inside the worktree, preserving Tau's architecture boundaries:
   - `tau_ai` for provider/model streaming concerns
   - `tau_agent` for portable harness, loop, tools, events, and sessions
   - `tau_coding` for CLI, resources, skills, commands, TUI integration, and coding tools
   - keep Textual and Rich concerns out of the reusable harness
6. Add or update tests for behavior changes.
7. Update `dev-notes/` for substantial implementation or workflow changes.
8. Update `website/src/content/docs/` when user-facing behavior changes.
9. Run relevant validation through `uv`, such as:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Use focused checks when the full suite is impractical, and record exact commands and results.

10. Make clear, atomic commits.
11. Push the branch when remote access is available:

```bash
git push -u origin <branch>
```

12. Draft or create a pull request targeting the default branch. If `gh` is available and authenticated, ask for confirmation before creating the PR unless the user explicitly asked to create it. If `gh` is unavailable, provide the branch name, compare URL when inferable, suggested PR title, and copy/paste-ready body.

## PR expectations

The PR should include:

- summary of the change
- why it is needed
- what changed
- exact validation commands and results
- docs or dev-note updates, if applicable
- linked issue, using `Closes #<number>` when the PR fully satisfies it
- reviewer notes and known limitations

## Cleanup reminder

After the PR is merged or abandoned, remove the worktree from the original repository:

```bash
git worktree remove <worktree-path>
git branch -d <branch>
```

Use `git branch -D <branch>` only when intentionally discarding an unmerged branch.

## Final report

Return:

- worktree path
- branch name
- commit SHA(s)
- test/check results
- PR URL or fallback PR instructions
- cleanup command
