# Codex live model catalog

## What changed

Tau now uses the authenticated ChatGPT Codex `/models` response as both a model
inventory and a source of runtime model limits. Previously Tau parsed context
and compaction values from this response but kept the selectable
`openai-codex` inventory entirely in `src/tau_coding/data/catalog.toml`.
Consequently, a newly rolled-out subscription model could be usable by an
account but remain unselectable until Tau released a static catalog update.

`tau_ai` now exposes an optional `ModelCatalogProvider` capability. The Codex
adapter implements it and defensively parses rows that the official schema
marks with `visibility = "list"`. Before discovery, `tau_coding` resolves the
current stable `@openai/codex` version from npm because the backend suppresses
models newer than the supplied `client_version`. The safe version string is
cached for four hours with ETag revalidation; stale cache and the bundled known
version are fallbacks, so newly client-gated models need no Tau release.

The adapter intentionally does not filter on
`supported_in_api`: the official client applies that field to API-key mode but
keeps subscription-visible rows in ChatGPT mode. Tau preserves provider priority
order and reads verified names, text/image modalities,
reasoning efforts, defaults, and limits. One in-memory fetch serves both model
inventory and limit discovery.

`tau_coding` publishes a successful snapshot as a process-local overlay on the
durable `openai-codex` configuration. Active Codex sessions discover during
load. Opening `/model` also discovers Codex while another provider is active by
creating and closing a model-less provider. The picker initially renders its
static snapshot and updates after background refresh, preserving its existing
non-blocking behavior.

## Safety and persistence

The authenticated catalog is account- and rollout-specific. Tau therefore does
not write it into `catalog.toml`, `providers.json`, session JSONL, or the
models.dev cache. The checked-in Codex rows remain the offline and failure
fallback. Empty, malformed, unauthorized, or unavailable responses do not erase
the fallback. `TAU_OFFLINE=1` skips authenticated discovery.

A live inventory is authoritative for picker visibility after successful
discovery. The active session model is not silently changed when absent from a
new snapshot. Scoped references may store only their provider/model pair; they
do not persist discovered metadata. `~/.tau/codex-version-store.json` contains
only public npm release metadata, never account-specific model data or secrets.

This preserves Tau's package boundary: `tau_ai` owns authenticated transport and
wire parsing, while `tau_coding` owns model selection and the ephemeral catalog
overlay. `tau_agent` remains independent of provider catalogs and OAuth.

## Lifecycle validation

Explicit startup and transcript resume now discover live-only Codex IDs before
static selection validation, including when a frontend supplied a fallback
provider. Discovery uses a temporary, closed provider and never mutates durable
settings. Failed/offline discovery leaves unknown IDs rejected, rather than
silently substituting a static model. RPC startup also defers live-only validation
to this session boundary.

Picker visibility is separate from active-runtime validity: when discovery drops
the active model, runtime rebuilds retain its previously selected Codex metadata.
Repeated settings refresh therefore cannot invalidate an otherwise usable session.
Regression tests cover explicit/resumed startup with and without preconstructed
providers and repeated settings reload after the active model disappears.

A follow-up exercises full TUI startup, picker highlighting, and indexed `/resume`,
including a source session that has not discovered Astra and a stale session
index. Provider-aware replacement keeps the destination loader's resolved model;
it neither restores the source session's model nor revalidates against the
source's stale catalog. Newly staged model metadata is replayed after discovery
so the active runtime and initial transcript agree.

Older sessions may already contain a mismatch: the index and assistant metadata
name Astra but the authoritative model-change entry still names Sol. No automatic
migration guesses a selection from assistant metadata (providers can report
routed model aliases). Resume and explicitly select Astra in `/model` to append
the correct model-change entry.

## Validation

Automated tests cover:

- latest stable Codex-version lookup, validation, ETag caching, and fallbacks;
- authenticated catalog parsing and one-request caching with runtime limits;
- filtering hidden rows while retaining subscription-visible, non-public-API rows;
- live names, modalities, reasoning levels, and context limits;
- publication while Codex is active;
- model-less discovery and provider cleanup while another provider is active;
- existing static fallback behavior for discovery failures.

Manual validation:

1. Authenticate with `/login openai-codex`.
2. Open `/model`; observe the checked-in list immediately.
3. Wait for background refresh and confirm it updates to models visible to the
   authenticated account.
4. Select a live-only model and send a tool-using prompt.
5. Restart and resume that session; confirm the exact live-only model is retained.
6. Run `/session` and confirm live context-limit reporting.
7. Repeat with `TAU_OFFLINE=1` and confirm the static list remains available.
