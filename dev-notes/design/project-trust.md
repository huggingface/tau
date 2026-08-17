# Project trust: compatibility research and Tau design

Status: **design only**. Tau does not implement this policy yet.

This note is the implementation plan for the documentation-first phase of
[issue #535](https://github.com/huggingface/tau/issues/535). It explains the
problem from first principles, records Pi's production behavior, audits Tau's
current behavior, and fixes the intended Tau design before runtime code changes.

A repository can currently influence Tau merely because Tau starts in that
repository. Project trust will put a decision in front of that implicit input
loading. It is not a judgment that every file in the repository is safe.

## Compatibility baseline and research method

Pi was inspected at the exact revision below on **2026-08-03**:

- repository: [`earendil-works/pi`](https://github.com/earendil-works/pi)
- commit: [`fa07e7bd92c90a0210a269354733c274e9fc11e6`](https://github.com/earendil-works/pi/commit/fa07e7bd92c90a0210a269354733c274e9fc11e6)
- commit date: 2026-08-03 19:57:44 UTC

The references below are commit-pinned and inspectable:

- [`docs/security.md`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/docs/security.md)
  is Pi's concise user-facing contract.
- [`core/trust-manager.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/src/core/trust-manager.ts)
  detects resources, canonicalizes keys, searches ancestors, and reads/writes
  `trust.json`.
- [`core/project-trust.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/src/core/project-trust.ts)
  defines decision ordering, defaults, extension participation, and the startup
  choices.
- [`core/resource-loader.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/src/core/resource-loader.ts)
  implements pre-trust and final resource-loading passes.
- [`core/settings-manager.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/src/core/settings-manager.ts)
  prevents project settings from loading while untrusted.
- [`src/main.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/src/main.ts)
  establishes bootstrap and session-cwd ordering.
- [`cli/project-trust.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/src/cli/project-trust.ts)
  limits trust UI exposed to extensions to interactive startup.
- [`interactive-mode.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/src/modes/interactive/interactive-mode.ts)
  contains `/trust`, reload, and resume integration.
- [`core/agent-session-runtime.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/src/core/agent-session-runtime.ts)
  recreates cwd-bound services when replacing a session.
- [`examples/extensions/project-trust.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/examples/extensions/project-trust.ts)
  demonstrates extension decisions and session-only results.
- [`test/trust-manager.test.ts`](https://github.com/earendil-works/pi/blob/fa07e7bd92c90a0210a269354733c274e9fc11e6/packages/coding-agent/test/trust-manager.test.ts)
  verifies persistence inheritance and trigger detection.

These references describe that revision, not an assumed timeless Pi contract.

## Pi's production behavior

### What triggers a decision

Pi first asks whether the cwd has a resource that needs trust. A bare `.pi`
directory does not trigger anything. At the inspected revision, these entries do:

- `<cwd>/.pi/settings.json`
- `<cwd>/.pi/extensions`
- `<cwd>/.pi/skills`
- `<cwd>/.pi/prompts`
- `<cwd>/.pi/themes`
- `<cwd>/.pi/SYSTEM.md`
- `<cwd>/.pi/APPEND_SYSTEM.md`
- `.agents/skills` in the cwd or any ancestor, except the user's own
  `~/.agents/skills`

For the `.pi` directories, existence is enough; they need not contain a valid
resource. This is why only a bare `.pi` directory is explicitly ignored.

If no trigger exists, `resolveProjectTrusted()` returns true without consulting
the store or prompting. Pi remembers this process-local outcome for that cwd.
Its interactive reload has special handling: if a previously empty project gains
a protected resource, Pi saves an implicit trusted decision after reload. Tau
will deliberately differ here; see [Reload](#reload).

### Gated and ungated inputs

When trusted, Pi may load project settings, project `.pi` extensions, skills,
prompts, themes, `SYSTEM.md`, `APPEND_SYSTEM.md`, project package resources, and
install missing project packages. Project extensions and package extensions can
therefore execute only after approval.

Before the decision, Pi loads only:

- global `AGENTS.md`/`CLAUDE.md` and ancestor context files;
- user/global extensions;
- extensions passed explicitly with CLI `-e`;
- inline application extensions.

Those pre-trust extensions can participate in the decision. Project extensions
cannot decide whether they themselves should run.

Pi deliberately leaves `AGENTS.md` and `CLAUDE.md` context ungated unless
context loading is disabled. Explicit CLI resource paths also remain eligible;
they represent a direct user instruction rather than ambient discovery.

### Canonical paths, ancestors, and persistence

Pi stores decisions in `~/.pi/agent/trust.json`. Keys are canonical absolute cwd
paths. `resolvePath()` makes a path absolute and normalized; `canonicalizePath()`
uses `realpathSync()` to follow symlinks, falling back to the normalized raw path
if realpath fails.

Lookup starts at the canonical cwd and walks to the filesystem root. The nearest
entry whose value is `true` or `false` wins. Thus a child decision overrides a
parent decision. Choosing “Trust parent folder” writes trust for the immediate
canonical parent and removes an exact child decision so inheritance can apply.

The file is a sorted JSON object whose values may be `true`, `false`, or `null`;
lookup ignores `null` (normal removal deletes the key). Pi validates the whole
object and uses a lock file for concurrent access. The inspected version writes
the destination directly rather than by atomic replacement and has no
schema-version field. Read/parse/validation/lock/write failures are errors; they
are not converted into approval.

### Decision order, scopes, and headless behavior

Pi resolves in this order:

1. one-run `--approve`/`-a` or `--no-approve`/`-na` override;
2. no trigger means trusted;
3. first decisive pre-trust extension result;
4. nearest saved cwd/ancestor decision;
5. global `defaultProjectTrust`, defaulting to `ask`;
6. if still `ask`, prompt when UI exists, otherwise decline.

The startup prompt offers:

- trust this exact folder and save it;
- trust the immediate parent and save it;
- trust for this session only;
- do not trust this exact folder and save it;
- do not trust for this session only.

Cancellation is a safe decline. The interactive `/trust` selector later edits
saved exact/parent decisions, but says a restart is required; it does not mutate
the current session's loaded set.

Print, JSON, and RPC modes never prompt. With no extension or saved decision,
`ask` and `never` decline, while `always` approves. The CLI overrides apply for
one invocation and are not persisted. The argument parser rejects simultaneous
approve and no-approve flags.

### Bootstrap, extensions, and cwd changes

Pi initially creates a settings manager with `projectTrusted: false`, so its
first proxy bootstrap comes only from global settings. There is an important
implementation caveat at this revision: after migrations, `main.ts` creates a
second startup-cwd settings manager with the default `projectTrusted: true` and
uses it for startup session-directory lookup before final trust resolution.
Final cwd-bound runtime loading does enforce the trust split described below,
but this early startup-settings read is not a pattern Tau should copy.

Pi chooses a session—including a session from another project—before creating
final cwd-bound runtime services. Final trust is therefore resolved against the
session's actual cwd, not blindly against the process's startup directory.

The resource loader's first pass forces project settings off and loads global,
CLI, and inline extensions. It emits `project_trust`; the first extension to
return yes/no wins, while `undecided` falls through. `remember: true` saves the
exact cwd decision. Handler errors are diagnostics and do not approve. The
second pass applies the decision, reloads settings, resolves packages, loads the
final extension set, then loads skills, prompts, themes, context, and system
prompt files.

Session replacement recreates cwd-bound services. A TUI resume supplies an
interactive trust context for the destination cwd. Pi also caches outcomes by
cwd within the process, avoiding an unrelated cwd's decision while avoiding
repeat prompts for one cwd.

### Security boundary

Pi's own security document is explicit: project trust is only an input-loading
guard. Pi still runs with the user's permissions. It is not a filesystem,
process, shell, tool, network, package-manager, extension, credential, model, or
prompt-injection sandbox. Real isolation requires an OS, container, VM, or
similar boundary.

## Current Tau behavior on `main`

This section describes what exists **now**, separately from the proposal.
It was audited on 2026-08-03 at current Tau `main` commit
[`9fffc6938e1873e5430b66117b7ade078d779030`](https://github.com/huggingface/tau/commit/9fffc6938e1873e5430b66117b7ade078d779030).
The behavior-bearing source links below use its parent `cc179435`, before the
release-only version commit.

Tau has no unified project-trust decision or trust store. It does not expose
`--approve`, `--no-approve`, or `defaultProjectTrust`. Starting, reloading, or
resuming can load project Markdown without a trust prompt.

The current sources are inspectable at:

- [`resources.py`](https://github.com/huggingface/tau/blob/cc179435a4e4fb934d51c0032c36f8d46fcd811c/src/tau_coding/resources.py)
- [`context.py`](https://github.com/huggingface/tau/blob/cc179435a4e4fb934d51c0032c36f8d46fcd811c/src/tau_coding/context.py)
- [`session.py`](https://github.com/huggingface/tau/blob/cc179435a4e4fb934d51c0032c36f8d46fcd811c/src/tau_coding/session.py)
- [`paths.py`](https://github.com/huggingface/tau/blob/cc179435a4e4fb934d51c0032c36f8d46fcd811c/src/tau_coding/paths.py)
- [`extensions/loader.py`](https://github.com/huggingface/tau/blob/cc179435a4e4fb934d51c0032c36f8d46fcd811c/src/tau_coding/extensions/loader.py)
- [`shell_config.py`](https://github.com/huggingface/tau/blob/cc179435a4e4fb934d51c0032c36f8d46fcd811c/src/tau_coding/shell_config.py)
- [`cli.py`](https://github.com/huggingface/tau/blob/cc179435a4e4fb934d51c0032c36f8d46fcd811c/src/tau_coding/cli.py)

### Current resource matrix

| Input on current `main` | Current behavior |
|---|---|
| `~/.tau/settings.json` | User-only shell settings such as `shellCommandPrefix`; there is no project `.tau/settings.json` loader. |
| `.agents` settings | Tau has no user or project `.agents` settings loader. |
| Other user provider/TUI settings | Read from user `~/.tau` files; no project equivalents. |
| `<cwd>/.tau/skills/*/SKILL.md` | Automatically discovered and loaded. |
| `<cwd>/.agents/skills/*/SKILL.md` | Automatically discovered and loaded. Tau checks the cwd location, not every ancestor `.agents/skills` as Pi does. |
| `<cwd>/.tau/prompts/*.md` | Automatically discovered and loaded. |
| `<cwd>/.agents/prompts/*.md` | Automatically discovered and loaded. This is broader `.agents` support than Pi's trust trigger. |
| `<cwd>/.tau/themes/*.json` | Loaded by the TUI and takes precedence over user themes. `.agents/themes` is not supported. |
| `<cwd>/.tau/SYSTEM.md` | Automatically read and replaces the default prompt unless an explicit CLI system prompt wins. |
| `<cwd>/.tau/APPEND_SYSTEM.md` | Automatically read and appended unless an explicit CLI append value wins. `.agents` system-prompt files are not supported. |
| Plain `AGENTS.md` | Loaded from the detected project root through the cwd. |
| `<cwd>/.tau/AGENTS.md` | Loaded as project context. |
| `<cwd>/.agents/AGENTS.md` | Loaded as project context. |
| `CLAUDE.md` | Not currently discovered by Tau. |
| `<cwd>/.tau/extensions` | Python code, disabled by default and loaded only with `--project-extensions`. |
| `~/.tau/extensions` | Python code, discovered by default unless `--no-extensions`. |
| `-e/--extension PATH` | Loaded explicitly even with `--no-extensions`; not trust-gated. |
| Extension `pyproject.toml` | `[tool.tau].extensions` is parsed only inside an extension directory being discovered; it is an extension manifest, not a general project package manager. |
| Project packages | Tau has no Pi-style project package resource/install system today. |

User `~/.tau` and `~/.agents` skills, prompts, and `AGENTS.md` are loaded as
user-owned resources. Skills and prompts use increasing precedence: user
`.tau`, user `.agents`, project `.tau`, project `.agents`. Project context layers
root-to-cwd plain `AGENTS.md`, then cwd `.tau/AGENTS.md` and
`.agents/AGENTS.md`. Project system prompt files take precedence over user files.

`/reload` re-discovers resources and can execute opted-in project extensions
without a trust check. `CodingSession.resume()` creates a replacement for the
record's cwd and loads its resources before adopting it; there is no destination
trust step. Explicit startup system-prompt text/path and explicit extension paths
already express user intent and override or augment ambient discovery.

The existing `--project-extensions` switch is a narrow safety opt-in, not a
persisted project-trust system. Published docs correctly warn about project
resources but must not claim enforcement exists until runtime phases land.

## Proposed Tau policy

Everything below is a future contract. Implementation PRs may stage it, but
must not silently weaken it.

### Goals and invariants

1. No protected project file is read for content, parsed, imported, installed,
   or executed before the decision for its canonical cwd.
2. Detection may inspect file type/name/existence only. It must not parse a
   project manifest merely to decide whether to ask.
3. Decline yields one coherent unprotected resource snapshot, never a mixture
   produced by a failed partial load.
4. Global policy cannot come from project settings; a project cannot approve
   itself.
5. A trust outcome is scoped to a canonical cwd and may inherit only from the
   nearest canonical ancestor decision.
6. `tau_agent` remains unaware of trust, local paths, CLI, and Textual.
7. Textual renders a Tau-owned decision request through the existing adapter
   boundary; it does not own trust policy.

### Protected-resource matrix

“Project” means ambient resources discovered because of the active cwd, not a
path the user explicitly supplied on this invocation.

| Resource | Proposed policy | Trigger rule |
|---|---|---|
| Built-in packaged resources | Ungated | Never. |
| User `~/.tau` and `~/.agents` resources | Ungated | Never; they are user-managed inputs. |
| Explicit CLI system/append prompt, extension, and future explicit skill/prompt/theme paths | Ungated | Never; direct invocation is consent. Existing type/read/load errors still apply. |
| `<cwd>/.tau/settings.json` if project settings are added | **Protected** | File exists. Only global settings can choose trust defaults. |
| `<cwd>/.tau/skills` and `.agents/skills` | **Protected** | At least one candidate `*/SKILL.md` exists. |
| `<cwd>/.tau/prompts` and `.agents/prompts` | **Protected** | At least one candidate non-reserved `*.md` exists. |
| `<cwd>/.tau/themes` | **Protected** | At least one `*.json` candidate exists. |
| `<cwd>/.tau/SYSTEM.md` and `APPEND_SYSTEM.md` | **Protected** | File exists. |
| Plain ancestor-to-cwd `AGENTS.md`, cwd `.tau/AGENTS.md`, and cwd `.agents/AGENTS.md` | **Protected** | Any file Tau would include exists. |
| `CLAUDE.md` | Not currently supported; if later discovered, **protected** like `AGENTS.md`. | File exists in the future discovery set. |
| `<cwd>/.tau/extensions` | **Protected and still opt-in initially** | At least one candidate `.py`, `*/extension.py`, or extension-package manifest exists. |
| Future project package declarations, package-managed resources, or auto-install metadata | **Protected** | The declaration/manifest exists; no resolution, download, or install before approval. |

Empty `.tau`, `.agents`, and empty protected subdirectories do not trigger a
decision. A broken or unreadable candidate that could otherwise load **does**
trigger; detection must not treat inspection failure as proof that no protected
input exists. Symlink candidates trigger based on the directory entry, while
content access waits for approval.

Directory scanners should share candidate predicates with the actual loaders so
the trigger matrix cannot drift. Detection returns typed resource categories and
paths for internal policy/diagnostics, but UI reports category/count—not file
content. It must cap detailed paths to avoid startup floods.

### Deliberate differences from Pi

Tau should not copy Pi blindly:

- **Protect all project `AGENTS.md`-style context.** Pi leaves `AGENTS.md` and
  `CLAUDE.md` ungated. Tau will gate project plain, `.tau`, and `.agents`
  instruction files because they become high-priority model input and can cause
  actions indirectly. User-level context remains ungated. This is an
  input-loading guard, not a claim that trusted context is safe.
- **Protect `.agents/prompts` and `.agents/AGENTS.md` as well as
  `.agents/skills`.** Tau intentionally supports more `.agents` locations than
  Pi. Treating only skills as protected would leave equivalent project prompt
  inputs outside the boundary.
- **Do not broaden ancestor resource discovery as a side effect.** Initial trust
  enforcement covers exactly what Tau loaders discover today: cwd `.tau` and
  `.agents` resources plus existing root-to-cwd plain `AGENTS.md`. Adding Pi's
  ancestor `.agents/skills` discovery is a separate compatibility change. If
  added later, those ancestor resources are protected and become triggers.
- **Keep `--project-extensions` during migration.** Initially a project
  extension loads only when the project is trusted *and* the existing opt-in is
  present. Trust must never make currently disabled executable code start
  automatically. A later, separately documented release may reconsider this.
- **Version and atomically replace Tau's store.** Pi's inspected store is an
  unversioned object written directly. Tau should make migration explicit and
  prevent torn writes.
- **Never implicitly save trust on reload.** If a project had no protected
  inputs and gains one, Tau asks (interactive) or follows deterministic
  headless policy. Merely having started in an empty project is not durable
  consent to future inputs.

### `tau_coding` types and ownership

Add a small `tau_coding.project_trust` policy layer. Suggested typed values:

```python
TrustDefault = Literal["ask", "always", "never"]
TrustDecision = Literal["trusted", "untrusted"]
TrustOverride = Literal["approve", "decline"]
TrustScope = Literal["exact", "parent", "run"]

@dataclass(frozen=True, slots=True)
class CanonicalProjectPath:
    value: Path

@dataclass(frozen=True, slots=True)
class ProtectedResourceSummary:
    cwd: CanonicalProjectPath
    categories: tuple[str, ...]
    counts: Mapping[str, int]

@dataclass(frozen=True, slots=True)
class SavedTrustEntry:
    path: CanonicalProjectPath
    decision: TrustDecision

@dataclass(frozen=True, slots=True)
class ProjectTrustRequest:
    cwd: CanonicalProjectPath
    resources: ProtectedResourceSummary
    inherited_entry: SavedTrustEntry | None
    choices: tuple[TrustChoice, ...]

@dataclass(frozen=True, slots=True)
class ProjectTrustResolution:
    trusted: bool
    source: Literal["override", "empty", "extension", "saved", "default", "ui"]
    saved_path: CanonicalProjectPath | None
```

Exact names may change, but keep these separations:

- `ProjectTrustStore`: validate, lock, read, nearest lookup, and atomic update;
- `ProtectedResourceDetector`: metadata-only trigger detection;
- `ProjectTrustPolicy`: pure precedence/default resolution;
- an async coordinator in `tau_coding` for extension/UI requests;
- a resource plan/snapshot that filters project inputs before existing loaders.

The coordinator receives abstract callbacks/protocols for decisions. TUI code
maps `ProjectTrustRequest` to a Textual modal and returns a choice. No Textual
imports enter the policy module. `tau_agent` receives only the already-built
system prompt, tools, and resources as it does now.

### Canonical path rules

A trust key is not a repository root. It is the active cwd after session
selection/replacement. Canonicalization must:

1. expand `~`, make the path absolute against an explicitly supplied base, and
   normalize `.`/`..`;
2. require the active cwd to exist and be a directory;
3. resolve all symlinks (`Path.resolve(strict=True)`);
4. apply the platform's case normalization for comparison/storage where the
   platform is case-insensitive, while retaining a display path separately if
   useful;
5. serialize one normalized native absolute path string.

Do not use raw string-prefix tests: `/work/app2` is not a child of `/work/app`.
Walk `Path.parent` from canonical cwd through the root. The first exact store
entry wins. A child explicit decline therefore overrides trusted parent scope.
Different symlink spellings resolve to one decision. A moved project has a new
key; stale entries remain inspectable and harmless until an explicit cleanup
feature exists.

If canonicalization of the live cwd fails, protected resources are untrusted and
startup emits an actionable error. Do not fall back to a noncanonical key as Pi
does. Saved entries must be absolute and normalized; invalid entries make the
store malformed rather than being silently skipped.

### Versioned, inspectable persistence

Use `~/.tau/trust.json` (equivalently `TauPaths.home / "trust.json"`) with this
initial shape:

```json
{
  "version": 1,
  "decisions": [
    {"path": "/home/alex/src", "decision": "trusted"},
    {"path": "/home/alex/src/example", "decision": "untrusted"}
  ]
}
```

Requirements:

- reject unknown versions, duplicate canonical paths, unknown fields, relative
  paths, and unknown decisions;
- sort decisions by path for stable diffs;
- lock across read-modify-write so concurrent Tau processes cannot lose updates;
- create the Tau home with user-only permissions where supported;
- durably install a restrictive same-directory undo journal before replacing
  the destination; readers fail closed whenever that journal remains;
- write a same-directory temporary file, flush and `fsync` it, set restrictive
  permissions, `os.replace()` it over the destination, then `fsync` the parent
  directory where supported;
- remove the journal only after that commit point; on a reported failure restore
  the prior store, and retain the fail-closed journal if any recovery operation
  fails, so newly granting bytes can never become an effective saved decision;
- clean up unrelated failed temporary files without replacing the last valid
  store.

Malformed/unreadable store means no saved decision can grant trust. Emit one
clear diagnostic and fail closed for protected resources unless the user gives
an explicit run-only approval through UI or `--approve`. A saved UI choice must
be written successfully **before** it grants trust; if persistence is
unwritable, report failure and remain untrusted, while offering the separate
“this run only” choice. An extension result with `remember=True` follows the
same rule. Explicit one-run approval never writes and remains usable, with a
store-error diagnostic, because it is direct consent rather than inferred
state.

Do not rename a malformed store automatically or silently reset it; that hides
why decisions disappeared. Future schema migration must parse the old complete
document, write v1 atomically, preserve a backup, and report what changed.

### Resolution precedence

For each canonical destination cwd:

1. explicit one-run CLI override;
2. no protected resource candidates: allow ambient loading because there is
   nothing protected, but record no durable decision;
3. first decisive eligible pre-trust extension;
4. nearest saved exact/ancestor decision;
5. user-global `defaultProjectTrust` (`ask` when absent);
6. interactive built-in choice for `ask`; otherwise safe decline.

This matches Pi's meaningful order. A project setting can neither set
`defaultProjectTrust` nor alter earlier steps. Cache a completed run resolution
by canonical cwd, including declines, so one cwd does not repeatedly prompt.
Never reuse it for another cwd. Re-run metadata detection on reload so an
“empty” outcome does not become consent after new files appear.

### Startup sequence

The future bootstrap should be explicit and testable:

1. Parse CLI arguments. Reject `--approve` with `--no-approve` before filesystem
   resource loading.
2. Load built-ins and user-global settings only. Read `defaultProjectTrust` only
   here. Do not read `<cwd>/.tau/settings.json`.
3. Resolve the final session target and canonical destination cwd using only
   user-global session configuration. A resumed record's cwd wins after missing
   cwd handling.
4. Detect protected candidates by metadata only.
5. Load a pre-trust extension runtime containing built-ins, user
   `~/.tau/extensions`, and explicit `-e` paths. Never import project extensions
   or resolve project packages in this pass.
6. Resolve trust using the precedence above. Interactive startup asks through
   the adapter; headless startup does not.
7. Build a complete resource plan: global and explicit inputs always; project
   inputs only if trusted; project extensions additionally require the existing
   opt-in during migration.
8. Only now parse project settings/manifests, resolve/install future project
   packages, import project extensions, read project Markdown/JSON, create the
   provider/runtime, and assemble the system prompt.
9. Publish the new coding session only after the complete plan succeeds. Emit
   bounded diagnostics for skipped categories or failures.

Provider construction matters: a protected project setting must not influence
provider, proxy, credentials, shell prefix, model, session directory, extension
flags, or tool configuration before step 8.

### Interactive TUI choices

The built-in modal should state the canonical folder, explain the categories it
would load, and say “This controls project inputs; it is not a sandbox.” Choices:

- **Trust this folder** — atomically save exact trusted, then continue;
- **Trust parent folder (`…`)** — save immediate parent trusted and remove an
  exact child entry in one transaction, then continue;
- **Trust for this run only** — continue without writing;
- **Do not trust this folder** — atomically save exact untrusted, then continue
  with protected resources skipped;
- **Do not trust for this run only** — skip without writing.

Escape/cancel exits Tau during initial startup; users must explicitly select a
run-only decline to continue without project inputs. During reload or session
replacement, cancellation preserves the active snapshot. Parent scope must never default to `$HOME`
or filesystem root without displaying the exact broad scope and requiring the
same explicit selection. Accessibility, focus, and key handling belong to the
Textual adapter/modal; available choices and semantics belong to `tau_coding`.

A future `/trust` command may inspect/edit decisions. Like Pi, edits should not
quietly mutate already loaded resources. It should say restart or `/reload` is
required. It must display inherited source and current run outcome separately.

### Headless modes and explicit overrides

Add `--approve`/`-a` and `--no-approve`/`-na` as mutually exclusive, invocation-
only overrides. They do not write `trust.json`. They apply to every destination
cwd entered by that invocation, including a resumed/replaced session, but each
cwd still receives its own diagnostic and resource plan.

Print, JSON, transcript, export-related runtime modes, and automation must never
wait for UI. With no earlier decisive result:

| Global default | Headless result |
|---|---|
| `ask` | untrusted |
| `always` | trusted |
| `never` | untrusted |

Structured modes must send diagnostics to their established diagnostic channel,
not corrupt stdout protocols. Extension participation is allowed in headless
mode, but `has_ui` is false and UI methods cannot prompt. A handler that tries
to prompt gets a deterministic error/undecided result, not stdin access.

### Extension participation

Add a Tau-owned `project_trust` event only after its result and ordering are
tested. The event exposes canonical cwd, mode, `has_ui`, and category/count
summary—never protected contents. Results are `approve`, `decline`, or `defer`,
plus `remember: bool`.

Only built-in, user-global, and explicit CLI extensions may receive it. First
decisive result wins. Errors become diagnostics and resolution continues.
Project extensions, project package extensions, and resources registered by
them cannot load or participate before approval. Final loading should reuse
already imported eligible extensions where practical so setup does not run
twice.

An extension cannot invent broader parent persistence: `remember` saves only the
exact cwd. Built-in UI owns parent-scope selection. On malformed/unwritable
storage, remembered extension approval fails closed as described above.

### Reload

`/reload` must be transactional:

1. detect candidates again;
2. if protected candidates now exist and this cwd has no cached non-empty
   resolution, resolve trust before reading them;
3. prepare all permitted resources and extensions in a replacement snapshot;
4. swap only after preparation succeeds;
5. emit reload/session lifecycle events from the accepted runtime.

If the user cancels or a trust/storage/load error occurs, keep the previous valid
resource/runtime snapshot. A deliberate decline may successfully replace it
with the global/explicit-only snapshot, after confirmation. Never implicitly
save trust because an empty project gained files. A saved decision changed by a
future `/trust` command takes effect only on an explicit reload/restart.

### Resume, replacement, and cwd changes

Current Tau replacement loads before adoption but has no trust phase. Future
resume/new-session/fork flows must derive and canonicalize the destination cwd,
then run the same coordinator before any destination project resource is read.
Do not carry source-cwd trust to the destination.

For an interactive cross-cwd resume, stage destination selection and trust while
the current session remains valid. Cancel leaves the existing session active.
Only after destination resources and provider/runtime are ready should Tau tear
down/adopt. Headless replacement uses the deterministic table and fails/skips
without prompting. Cache keys are canonical cwd, not session id.

### Diagnostics and observability

Diagnostics should answer:

- canonical cwd and whether a decision was exact or inherited;
- outcome source (`override`, `extension`, `saved`, `default`, or `ui`);
- protected categories skipped, with counts;
- malformed/unreadable/unwritable store and remediation path;
- pre-trust extension errors;
- why project extensions remained disabled despite trust (missing existing
  `--project-extensions` opt-in).

Do not print trust-store contents, resource contents, credentials, or every path
by default. Interactive status may summarize once. Text/JSON/transcript modes
must preserve their output contracts. A debug view may expose bounded paths
only after normal redaction rules.

### Migration and compatibility rollout

Enforcement changes existing behavior, so do not hide it in a refactor.
Recommended first runtime release:

- default global policy is `ask`;
- interactive sessions ask when meaningful protected candidates exist;
- headless unresolved `ask` declines and emits a concise migration diagnostic
  suggesting a saved interactive decision or explicit `--approve`;
- no existing repository is auto-trusted and no decision is inferred from
  `--project-extensions` history;
- user/global and explicit CLI resources continue working;
- `--project-extensions` remains an additional requirement;
- release notes and published security/configuration/CLI docs are updated only
  when enforcement and flags actually ship.

There is no legacy Tau trust store to import. If a prototype/unversioned file is
ever released before v1, migration must be explicit, tested, and backed up.

## Staged future implementation

This design PR adds no runtime code. Follow-up changes should stay reviewable:

1. **Pure policy and persistence.** Add types, strict canonicalization, detector,
   versioned locked atomic store, nearest-ancestor lookup, and pure resolution.
   No CLI/TUI integration yet.
2. **Resource planning.** Introduce trusted/untrusted resource snapshots and gate
   project settings, skills, prompts, themes, context, and system-prompt files.
   Preserve existing precedence among permitted inputs.
3. **CLI/headless integration.** Add mutually exclusive overrides, global default,
   deterministic modes, structured diagnostics, startup/session-cwd ordering.
4. **TUI adapter integration.** Add the accessible startup modal and scopes
   without moving Textual into policy or `tau_agent`.
5. **Extensions and replacement.** Add pre-trust extension event, two-pass reuse,
   transactional reload, and cross-cwd resume/replacement.
6. **Packages, only when Tau has them.** Gate project package declarations,
   resolution, installs, and package resources before exposing the feature.
7. **Published documentation/release notes.** Describe only behavior that has
   landed, include migration guidance, and repeat the non-sandbox boundary.

Each stage should update this note if implementation discovers an invalid
assumption; deliberate policy changes need rationale, not accidental drift.

## Deterministic test plan

All tests must use temporary homes and projects; no test reads or writes the
operator's real home or calls a live provider.

### Policy and store

- canonical aliases/symlinks map to one key; missing/non-directory cwd fails
  closed;
- exact beats nearest parent; parent beats global default; siblings do not
  inherit;
- broad parent trust plus exact child decline; removing child restores
  inheritance;
- v1 round trip is sorted and inspectable;
- malformed JSON, wrong version/schema, duplicate/noncanonical paths, read
  failure, lock failure, and write/replace/fsync failure do not grant trust;
- interrupted atomic writes preserve the prior valid file;
- concurrent fake writers do not lose decisions.

### Detection and matrix

Using temporary directories, cover every matrix row, both `.tau` and `.agents`,
plain root-to-cwd `AGENTS.md`, empty directories, reserved/nonmatching files,
unreadable entries, and symlinks. Assert detection reads no protected content.
Assert current unsupported `CLAUDE.md` and package metadata do not accidentally
load, while future package fixtures trigger once that feature exists.

### Resolution and startup

Table-test override, no-resource, extension, exact/ancestor saved decision, each
default, UI choice, cancellation, and store-error precedence. Validate
conflicting CLI flags before resource reads. Use a fake provider that records
construction and calls; assert it sees no protected project setting/prompt
before approval and receives no call on failed startup.

### Resource and extension ordering

Use fake extensions that record import/setup/events:

- global and explicit extensions may handle trust;
- project extensions never import before approval;
- first decisive handler wins, defer continues, errors diagnose and continue;
- remembered approval must persist before loading project code;
- decline produces only global/explicit resources;
- protected categories load as one final snapshot with existing precedence;
- `--project-extensions` remains required in addition to trust.

### Frontends, reload, and sessions

- Textual pilot tests cover every scope, parent label, escape-as-decline, focus,
  keyboard operation, persistence error, and non-sandbox copy;
- print/JSON/transcript tests prove `ask` never prompts and stdout remains clean;
- reload after an initially empty project asks/declines rather than auto-saving;
- cancelled/failed reload preserves the old snapshot; accepted decline swaps to
  global/explicit-only;
- resume/replacement to a second cwd resolves independently before adoption;
  cancellation retains the first session;
- same canonical cwd uses its process cache, while symlink aliases and unrelated
  cwd behavior are deterministic;
- fake providers and fake extensions prove no live model, network, package
  install, process, or real credential is needed.

Run the repository's complete Python and website checks for every integration
stage.

## Non-sandbox boundary

**Project trust is only an input-loading guard.** It does not restrict what Tau,
the model, extensions, packages, or tools can do after loading. It is not a
filesystem, write, process, shell, subprocess, network, tool, credential,
provider, model, package-install, prompt-injection, or data-exfiltration
boundary. A trusted project may still be malicious, and an untrusted repository
may still influence the model through content the user explicitly asks Tau to
read or through tool output.

Users needing isolation must run Tau inside an appropriate OS sandbox,
container, VM, micro-VM, remote environment, or policy-controlled tool boundary
with limited files, credentials, and network access. This project-trust design
must never be marketed as a substitute.
