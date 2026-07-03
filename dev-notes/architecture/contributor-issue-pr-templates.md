---
title: "Contributor issue and PR templates"
---

This note documents the shared contributor templates added for issues and pull requests.

## What was added

Tau now includes two project-level prompt templates:

```text
.agents/prompts/issue.md
.agents/prompts/pr.md
```

Tau loads these from the repository when a contributor works in this checkout.
They become slash commands named `/issue` and `/pr` in the TUI.

The repository also includes GitHub-native templates:

```text
.github/ISSUE_TEMPLATE/bug_report.md
.github/ISSUE_TEMPLATE/feature_request.md
.github/PULL_REQUEST_TEMPLATE.md
```

These appear in GitHub's web UI and help contributors who are not using Tau.

## Why it exists

Maintainers often have private prompt templates for creating well-structured
issues and PRs. Project-level templates move that process into the repository so
contributors get the same structure without copying local dotfiles.

The prompts are deliberately tolerant of partial local setup. A contributor may
not have:

- the GitHub CLI installed
- GitHub CLI authentication configured
- dependencies synced
- a writable remote
- enough local repository metadata to infer every field

In those cases the prompts ask Tau to produce copy/paste-ready Markdown, manual
browser instructions, and optional `gh` commands instead of failing.

## How it maps to Tau's design

Prompt templates are coding-environment resources, so they live under
`tau_coding`'s resource system rather than in the reusable `tau_agent` harness.
They are plain Markdown files with frontmatter and `{{ arguments }}` expansion.

The GitHub templates are not Tau-specific. They provide the same structure at
the remote repository boundary for contributors using the GitHub website.

## How to use it

In Tau's TUI, run:

```text
/issue describe the bug or feature request
```

or:

```text
/pr summarize any extra context reviewers need
```

After adding or editing prompt files while Tau is already open, run:

```text
/reload
```

The prompts should draft structured content first and only create GitHub issues
or PRs after confirmation, unless the user explicitly asks to create them without
confirmation.
