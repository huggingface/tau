---
title: "ADR 0003 — Unify skill discovery on the Agent Skills spec"
---

## Status

Accepted.

## Context

Tau discovers Markdown skills from four locations, in increasing precedence:

```text
~/.tau/skills/
~/.agents/skills/
<cwd>/.tau/skills/
<cwd>/.agents/skills/
```

Historically Tau accepted **either** layout in any of these directories:

- a `foo.md` file at the root (skill name = `foo`), or
- a `foo/SKILL.md` subdirectory (skill name = `foo`).

PR [#280](https://github.com/huggingface/tau/pull/280) tightened discovery for
the `.agents/` locations to match the [Agent Skills
spec](https://agentskills.io/specification#directory-structure), which requires
the `subdir/SKILL.md` form. It intentionally left `.tau/skills/` on the
permissive "any `.md`" rule.

That left Tau with two different rules for two visually-identical locations:

| Location | `foo.md` (bare file) | `foo/SKILL.md` (directory) |
|---|---|---|
| `.tau/skills/` | ✅ loaded | ✅ loaded |
| `.agents/skills/` | ❌ ignored | ✅ loaded |

## Decision

Apply the Agent Skills spec uniformly to **every** skills location Tau
scans. A skill is always `<skills-dir>/<name>/SKILL.md`. Bare `.md` files at
the root of a skills directory are not loaded as skills; instead, Tau emits an
informational `ResourceDiagnostic` telling the user how to migrate:

```text
mv foo.md foo/SKILL.md
```

The `agents_mode` branch introduced by #280 is removed from
`_load_skills_from_dir_with_diagnostics`.

## Why diverge from Pi here

Pi maintains an explicit `SkillDiscoveryMode = "pi" | "agents"` split in
`packages/coding-agent/src/core/package-manager.ts`:

- `.pi/skills/` and `~/.pi/agent/skills/` use "pi mode" (bare `.md` allowed).
- `.agents/skills/` uses "agents mode" (strict `SKILL.md` only).

Reading Pi's own changelog makes clear that this split is **not a philosophical
choice** — it is a **backward-compatibility carveout**:

> **Pi skills now use `SKILL.md` convention**: Pi skills must now be named
> `SKILL.md` inside a directory, matching Codex CLI format. Previously any
> `*.md` file was treated as a skill. Migrate by renaming
> `~/.pi/agent/skills/foo.md` to `~/.pi/agent/skills/foo/SKILL.md`.

And the fix that mirrors #280:

> Fixed skill discovery to stop recursing once a directory contains
> `SKILL.md`, and to ignore root `*.md` files in `.agents/skills` while
> keeping root markdown skill files supported in `~/.pi/agent/skills`,
> `.pi/skills`, and package `skills/` directories (pi-mono#2603)

Pi's direction of travel is clearly "`SKILL.md` everywhere." Pi cannot finish
that migration cleanly because it has a large installed base of bare-`.md`
skills that would break.

Tau does not share that constraint. We are pre-1.0 (`0.1.3`), have no
meaningful backward-compat contract for `.tau/skills/` layouts, and can ship
the endgame directly instead of carrying a legacy path forward.

## Consequences

- One rule for every skills directory. The `agents_mode` heuristic
  (`skills_dir.parent.name == ".agents"`) disappears.
- User skills authored as bare `.md` files under `.tau/skills/` are no longer
  loaded and must be migrated. Tau surfaces this via a diagnostic at load time
  so the failure mode is visible rather than silent.
- Skill loader behavior no longer depends on parent directory names, so it
  stays correct when callers pass unusual `TauResourcePaths.agents_root`
  values (for example a custom skills root during testing or embedding).
- Documentation (`website/content/guides/skills-and-prompts.md`) now shows the
  spec layout as the only supported form.

## Migration

For each bare `.md` skill under any Tau-scanned skills directory:

```bash
cd ~/.tau/skills          # or .tau/skills, .agents/skills, etc.
for f in *.md; do
  name="${f%.md}"
  mkdir -p "$name"
  mv "$f" "$name/SKILL.md"
done
```

The load-time diagnostic points users at exactly the destination path they
should rename each file to.

## References

- PR [#280](https://github.com/huggingface/tau/pull/280) — narrower fix for
  `.agents/` only.
- Issue [#292](https://github.com/huggingface/tau/issues/292) — this
  unification.
- Agent Skills spec: <https://agentskills.io/specification#directory-structure>
- Pi's `SkillDiscoveryMode`: `packages/coding-agent/src/core/package-manager.ts`
  in the pi-mono repo.
