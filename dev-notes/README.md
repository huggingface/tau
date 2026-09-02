# Tau dev notes (contributor build-log)

These are the internal, phase-by-phase build journals and design records for Tau.
They are **not** published on the docs site — they live here for contributors who
want to trace how the system was assembled.

User-facing documentation lives in `website/content/` and is published at
<https://twotimespi.dev/>.

## Contents

- `design/` — high-level design docs written alongside the build:
  - `00-roadmap.md` — phased roadmap
  - `01-architecture.md` — the three-layer split
  - `02-agent-loop.md` — agent loop responsibilities
  - `03-tools.md` — built-in tool design
  - `04-sessions.md` — session tree / persistence design
  - `05-core-types-and-events.md` — provider-neutral types and events
  - `project-trust.md` — researched, implementation-ready project-trust design
    (design only; enforcement is not implemented)
  - `agent-loop.md`, `harness.md` — harness/loop reference notes
- `architecture/` — per-phase implementation notes (`phase-1` … `phase-24`, plus
  hardening and feature notes). Each answers: what was added, why it exists, how
  later phases use it.
- `adr/` — architecture decision records.
- `catalog-model-safety.md` — checklist for adding providers and models to the built-in catalog safely.
- `google-stream-completion.md` — why native Google streams require an explicit
  `finishReason` before Tau reports successful completion.
- `startup-thinking-level-fallback.md` — why startup resolves a valid thinking
  level per model instead of assuming the global `medium` default.
- `models-dev-catalog.md` — Pi-compatible build-time models.dev catalog
  generation, offline fallback, and snapshot refresh workflow.
- `llama-cpp-phase-5.md` — built-in llama.cpp connection, safe state, `/local`,
  failure handling, and Phase 5 validation.
- `architecture/phase-6-local-inference-hardening.md` — second-backend contract
  validation, lifecycle hardening, migration, and security decisions.

The roadmap is tracked in [GitHub issue #1](https://github.com/huggingface/tau/issues/1).
