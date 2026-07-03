# Tau release process

Tau is published to PyPI as `tau-ai`. Publishing is intentionally tied to a
release decision, not to every commit that lands on `main`.

## What runs on ordinary `main` commits

Ordinary commits merged to `main` run validation workflows, documentation
builds, and other checks, but they do not create a PyPI release.

The PyPI workflow publishes only when a maintainer publishes a GitHub Release.
This keeps package uploads tied to an explicit release action instead of a
routine merge to `main`.

## Version source of truth

The package version lives in `pyproject.toml`:

```toml
[project]
version = "0.1.0"
```

A production release starts by intentionally changing that value.

## How to publish a release

1. Choose the next version number.
2. Update `[project].version` in `pyproject.toml` and any checked-in version
   constants that back `tau --version`.
3. Run the release checks locally, for example:

   ```bash
   uv run pytest
   uv run ruff check .
   uv run mypy
   ```

4. Open a PR with the version bump and release notes.
5. Merge the PR to `main` after checks pass.
6. Create and publish a GitHub Release from `main` using a tag that matches the
   package version, for example `v0.1.1`.
7. The `Publish Python package` workflow runs from the published GitHub Release
   and uploads the package to PyPI.
8. Verify the release at <https://pypi.org/project/tau-ai/>.

Do not rely on the version-bump PR merge alone to publish the package. The
release is intentionally triggered by publishing the GitHub Release.

## Duplicate-version protection

Before publishing, confirm that the package name and version do not already
exist on PyPI. PyPI does not allow replacing an existing file for the same
version, so a duplicate upload must be fixed with a new version number.

## Safe failure behavior

If `pyproject.toml` changes without a GitHub Release, or a normal `main` commit
lands without a release being published, the workflow does not publish. This
keeps package versions meaningful and makes the production release process easy
to audit.
