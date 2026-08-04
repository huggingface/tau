---
title: Project trust
description: Control ambient repository inputs before Tau loads them.
---

Tau asks before loading protected inputs discovered because of the active working
directory. The decision is keyed to the canonical, existing cwd—not a guessed
repository root. Symlink aliases therefore share a decision, and the nearest
saved parent decision is inherited.

## Protected inputs

Tau gates project `.tau` and `.agents` skills and prompts, `.tau` themes,
`SYSTEM.md` and `APPEND_SYSTEM.md`, plain and scoped `AGENTS.md`, project
extension candidates, and project `settings.json` reserved for future support.
Detection checks names and metadata only; Tau does not read, parse, or import a
candidate before deciding.

User resources under `~/.tau` and `~/.agents`, built-ins, and paths explicitly
passed on the CLI remain eligible. Trusted project extensions still require the
additional `--project-extensions` opt-in.

## Decisions

Interactive startup offers:

- trust this exact folder and save it;
- trust the displayed immediate parent and save it;
- trust for this run only;
- decline this exact folder and save it;
- decline for this run only.

Escape/cancel exits Tau during startup without loading the project. During
`/reload` or cross-project session replacement, cancellation preserves the
current session snapshot and keeps Tau open.
Saved decisions live in `~/.tau/trust.json`, version 1. Tau validates the whole
file and updates it under a lock with restrictive permissions and atomic
replacement. A malformed, unreadable, locked, or unwritable store never grants
saved trust. Run-only approval remains available with `--approve`.

A child decision overrides an inherited parent decision. Parent trust is broad:
Tau displays the exact parent before selection. There is no automatic cleanup
when a project moves.

## Headless and automation

`--approve` (`-a`) and `--no-approve` (`-na`) are mutually exclusive, apply only
to the invocation, and never edit the store. Print, JSON, transcript, and other
headless paths never prompt. Their unresolved behavior follows the user-global
`defaultProjectTrust` setting:

| Value | Headless result |
| --- | --- |
| `ask` (default) | decline |
| `always` | approve |
| `never` | decline |

Trust diagnostics use stderr in structured modes, so stdout remains machine
readable. They report bounded category/count and decision-source information,
not protected contents.

## Reload and sessions

`/reload` detects again. An initially empty project that gains protected inputs
requires a new decision; Tau never infers durable trust from the earlier empty
state. Resource preparation is coherent: decline builds a global/explicit-only
snapshot. Resume and replacement resolve the destination record's canonical cwd
instead of reusing the source project's outcome.

## Security boundary

**Project trust is an input-loading guard, not a sandbox.** It does not restrict
filesystem reads/writes, processes, shell commands, tools, network access,
credentials, providers, models, package installation, prompt injection, or data
exfiltration. A trusted project may still be malicious. Use an OS sandbox,
container, VM, remote environment, and restricted credentials/network when you
need isolation.
