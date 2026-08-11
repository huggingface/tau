---
title: "Generated model catalog metadata"
---

Issue: https://github.com/huggingface/tau/issues/571

## What changed

Tau now generates model limits, prices, reasoning flags, display names, and input
modalities from `https://models.dev/api.json`. The reviewable, offline runtime artifact
remains `src/tau_coding/data/catalog.toml`.

The generation inputs are deliberately split:

- `catalog_overrides.toml` owns Tau provider configuration, the curated model selection,
  authentication, API choice, compatibility flags, thinking maps, cache behavior,
  Tau-only models, and corrections to upstream data.
- models.dev owns metadata that it can describe reliably: context/output limits, standard
  prices, reasoning support, names, and text/image inputs.
- `catalog_manifest.json` records the source hash, final catalog hash, per-provider hashes,
  schema version, and reviewed model-ID sets for strict providers.

This boundary keeps network access out of Tau startup and package installation. Generation
is a maintainer action; users still receive and load a committed TOML resource offline.

## Generation pipeline

Run:

```bash
uv run python scripts/generate_catalog.py --generate --strict
uv run python scripts/generate_catalog.py --check
```

The generator fetches models.dev, keeps tool-capable models selected by the overlay, maps
upstream fields, then deep-merges Tau's corrections. `--strict` compares the complete
upstream tool-capable model sets for OpenAI, Anthropic, and GitHub Copilot with the reviewed
allowlists in the existing manifest. This initial scope covers the providers that motivated
the change without turning unrelated provider additions into automatic catalog expansion.

When models.dev adds or removes IDs, strict mode reports the exact drift. Review it and the
curated model list first; then run:

```bash
uv run python scripts/generate_catalog.py --generate --update-allowlist
```

Commit the overlay, generated catalog, and manifest together. Provider hashes make catalog
diffs independently auditable.

## Integrity and CI

The offline check does not fetch the network. It validates the TOML schema, model metadata,
Anthropic-protocol output limits, catalog SHA-256, and per-provider SHA-256 values. CI runs:

```bash
uv run python scripts/generate_catalog.py --check
```

This catches direct edits or stale generated files while keeping CI deterministic. Existing
provider-catalog golden tests continue to guard runtime interpretation of the generated
metadata.

## Runtime integration

The generator belongs to `tau_coding`'s data-maintenance boundary; neither `tau_agent` nor
provider streaming performs generation. OpenAI-compatible runtime configuration now also
copies a selected model's generated `max_tokens`, matching the existing Anthropic path.
