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
  - `agent-loop.md`, `harness.md` — harness/loop reference notes
- `architecture/` — per-phase implementation notes (`phase-1` … `phase-24`, plus
  hardening and feature notes). Each answers: what was added, why it exists, how
  later phases use it.
- `adr/` — architecture decision records.
- `catalog-model-safety.md` — checklist for adding providers and models to the built-in catalog safely.

The roadmap is tracked in [GitHub issue #1](https://github.com/alejandro-ao/tau/issues/1).
