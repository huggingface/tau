---
title: "Extension installer"
---

# Extension installer

Tau now has a small `tau install <source>` command for making an extension
available on future runs. It intentionally implements the useful first slice of
Pi's package installer without introducing a settings/package-management system.

## What was added

The command accepts:

```text
tau install ./extension.py
tau install ./extension-directory
tau install git:github.com/owner/repository
tau install git:github.com/owner/repository@ref
tau install https://github.com/owner/repository.git
```

Local sources are copied and Git sources are cloned into
`~/.tau/extensions/`. Existing destinations are preserved unless the caller
passes `--force`.

## Why this shape

Before this change, users had to know Tau's resource directory and manually
copy or clone into it, or repeat `tau -e PATH` on every run. Pi solves that
problem with `pi install`. Tau now mirrors that command's source-oriented UX and
Pi-style `git:` spelling while reusing Tau's existing extension discovery.

The first version is deliberately narrower than Pi's package manager:

- it installs extensions only, not skills, prompts, or themes;
- it supports local paths and Git, not npm/PyPI packages;
- it does not install Python dependencies;
- it has no package registry, remove command, or update command.

This keeps package policy in `tau_coding` and avoids changing the portable
`tau_agent` harness.

## Installation flow

```text
source argument
      ↓
resolve local path or normalize Git source/ref
      ↓
copy or clone into a temporary root/extensions/<name> layout
      ↓
validate that normal user-directory discovery can see it
      ↓
atomically rename into ~/.tau/extensions
      ↓
load through the existing runtime on the next Tau startup
```

Validation does not import or execute extension code. A single local file must
end in `.py`. A directory must contain `extension.py` or declare entries in
`[tool.tau].extensions`; a loose directory of Python files is rejected because
`-e` could discover it but user-directory discovery would not.

Staging happens inside the destination directory so publication is a
same-filesystem rename. The temporary tree reproduces the exact
`root/extensions/<name>` discovery layout, preventing nested-only packages from
passing explicit-path validation and then disappearing after installation.
Failed copies, clones, checkouts, validation, and filesystem operations remove
their staging artifacts and surface as `ExtensionInstallError`. Forced
replacement keeps the previous destination as a temporary backup and restores
it if publication fails.

## Security boundary

Installation is not sandboxing. The command prints a warning, but the user is
responsible for reviewing the source. Installed modules execute in the Tau
process with the user's filesystem, network, credentials, and process access.

## Main files

| File | Responsibility |
| --- | --- |
| `src/tau_coding/extension_installer.py` | Source parsing, staging, validation, and publication |
| `src/tau_coding/cli.py` | `tau install` command dispatch and user-facing output |
| `src/tau_coding/paths.py` | Canonical user extension directory |
| `tests/test_extension_installer.py` | Local/Git installs, validation, replacement, cleanup |
| `tests/test_cli.py` | CLI dispatch and errors |

## Verification

Run:

```bash
uv run pytest tests/test_extension_installer.py tests/test_cli.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
