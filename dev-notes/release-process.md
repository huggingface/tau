# Tau release process

Tau is published to PyPI as `tau-ai`. Publishing is intentionally tied to a
release decision, not to every commit that lands on `main`.

## What runs on ordinary `main` commits

Ordinary commits merged to `main` should run validation workflows, documentation
builds, and other checks, but they should not create a PyPI release unless the
package version changes.

The PyPI workflow listens to pushes that touch `pyproject.toml`, but it still
checks the previous commit before publishing. If `[project].version` did not
change, the workflow exits without building or uploading a package.

## Version source of truth

The package version lives in `pyproject.toml`:

```toml
[project]
version = "0.1.0"
```

A production release starts by intentionally changing that value.

## How to publish a release

1. Choose the next version number.
2. Update `[project].version` in `pyproject.toml`.
3. Open a PR with the version bump and any release notes or documentation
   updates that should accompany it.
4. Merge the PR to `main` after checks pass.
5. The `Publish Python package` workflow detects that the version changed and
   attempts to publish the new package to PyPI.
6. Verify the release at <https://pypi.org/project/tau-ai/>.

Maintainers may also publish from an explicit GitHub Release or by manually
running the workflow. Those paths are reserved for intentional release actions,
not routine development commits.

## Duplicate-version protection

Before building or uploading, the workflow checks whether the package name and
version already exist on PyPI. If the version is already present, publishing is
skipped instead of attempting a duplicate upload.

## Safe failure behavior

If `pyproject.toml` changes without a version bump, or a normal `main` commit
lands without touching release metadata, the workflow does not publish. This
keeps package versions meaningful and makes the production release process easy
to audit.
