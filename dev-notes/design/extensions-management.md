---
title: "Extensions inventory and management boundaries"
---

# Extensions inventory and management boundaries

Issue #454 introduces `/extensions` as a searchable, read-only TUI inventory.
It shows successful loads with source metadata and extension diagnostics in one
place. This solves the immediate visibility problem without implying package
management Tau does not yet provide.

## Data model

`ExtensionInfo` is runtime metadata, rebuilt during extension discovery:

- `name`: loader identity
- `path`: resolved entry file shown to the user
- `scope`: `project`, `user`, or `explicit`
- `status`: currently `loaded`

Failures remain `ResourceDiagnostic` values. They are rows in the inventory,
not installations. No registry or manifest is persisted.

## Ownership and removal

Current sources have these boundaries:

| Source | Tau owns files? | Removal meaning |
| --- | --- | --- |
| `~/.tau/extensions` manually populated | No | Explain the path; user removes it |
| `<project>/.tau/extensions` | No | Explain the project path; never delete |
| explicit `-e` file/directory | No | Restart without the flag; never delete |
| future Tau-managed user install | Yes | Remove only its registered managed directory, after confirmation |

Therefore this phase exposes no Remove action. A future installer must keep a
registry that distinguishes Tau-managed directories from coincidentally
located user files before destructive actions are safe.

## Enable and disable

The runtime currently persists only global discovery choices. Per-extension
enablement needs a registry recording a stable installation identity, source,
path, scope, revision, and enabled state. Name alone is unsuitable because
load precedence permits duplicate names. Changes must use the existing
`session_shutdown` → reset/reload → `session_start` lifecycle. On failure, Tau
must preserve or restore the prior registry, files, and usable runtime.

## Repository installation trust model

Installing from a URL is deferred. Before implementation, define:

1. initially accepted canonical public GitHub HTTPS repository URLs;
2. an installation ID and destination under a Tau-owned directory, separate
   from manually populated extensions;
3. pinned revision and entry discovery metadata in an atomic registry;
4. clone into a temporary sibling, validate manifest/entries, then rename;
5. explicit confirmation that arbitrary Python executes in Tau's process;
6. rollback of files and registry on clone, validation, setup, or reload error;
7. update and removal behavior, including local modifications;
8. deterministic tests using local fake repositories, never the network.

SSH/private authentication, automatic updates, and project installation should
remain follow-ups until the public HTTPS flow and trust language are proven.

## Validation

Textual pilot tests cover populated inventory, case-insensitive filtering, no
matches, empty state, and Escape cancellation. Command tests cover registration
and argument validation.
