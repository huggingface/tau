# Tau extensions

Tau extensions are Python modules that can register custom tools, slash commands,
and process-local provider definitions; observe lifecycle events; intercept tool
calls and results; show UI dialogs and sidebar sections; and customize message
rendering.

For Hugging Face-specific routing extensions, `set_inference_provider(<provider>)` selects a fixed route while `set_inference_provider(None)` restores recoverable automatic routing. Read both `context.inference_provider` (current route) and `context.inference_provider_mode` (`automatic` or `fixed`) when presenting route status.

## Start here

For complete API documentation, read the repository's published guide when working in a Tau checkout:

- `website/content/guides/extensions.md`
- `dev-notes/architecture/phase-21-extensions.md`

Installed examples are under `examples/extensions/` next to these docs. Read the relevant example completely before implementing an extension.

## Locations

- `~/.tau/extensions/`: discovered by default.
- `<project>/.tau/extensions/`: requires project approval and `--project-extensions`.
- `tau -e PATH`: explicitly load a file or directory.

## System-prompt contributions

Use `tau.add_prompt_guideline(text)` for one behavioral bullet in Tau's
`Guidelines` section. Use `tau.add_prompt_section(title, body)` for structured,
always-on context containing paragraphs, lists, or code blocks. `title` may be
`None`; otherwise Tau renders it as a level-two Markdown heading.

Extension sections follow cumulative user and project `APPEND_SYSTEM.md` files
and explicit `--append-system-prompt` content, then compose in extension load and
registration order. Empty bodies and multi-line titles are ignored with
diagnostics. Prompt registrations are source-owned and disappear on failed setup, reload, or runtime
retirement. See `examples/extensions/prompt_section.py`.

## Installing extensions

Install a trusted local or Git extension for future runs with:

```bash
tau install git:github.com/owner/repository
tau install git:github.com/owner/repository@v1.2.0
tau install ./path/to/extension.py
tau install ./path/to/extension-directory
```

Git repositories and local directories install under `~/.tau/extensions/` and
must contain `extension.py` or a `[tool.tau].extensions` manifest. Use `--force`
to replace an existing install. The installer does not install Python
dependencies or provide package remove/update commands yet.

## Trusted built-in sources

Tau can declare product capabilities as `BuiltInExtension` values in
`tau_coding.built_in_extensions`. These call the same synchronous `setup(tau)`
and use the same tool, command, provider, hook, and lifecycle APIs as filesystem
extensions. Generic loading never branches on a capability's name.

Built-ins are trusted because their code ships inside the installed Tau package;
they are not discovered from the cwd. They load before user, explicit, and
trusted project extensions, including when `--no-extensions` disables directory
discovery. Hidden declarations stay active but are omitted from ordinary
extension-name counts; detailed runtime metadata retains `source="built-in"`,
the stable `built-in:<name>` source ID, and the hidden flag. A setup failure is a
normal extension diagnostic and rolls back that source's registrations without
blocking later extensions or startup.

Every staged runtime loads each declaration once. Reload, resume, new-session,
and cwd replacement create a fresh generation; retiring the old generation
invalidates captured APIs, removes source registrations, and cancels its dynamic
provider work. Built-ins do not create protected project inputs or a trust prompt.
They can only observe or decide an already-required trust flow through the same
explicit `project_trust` hook available to eligible pre-trust extensions.

An extension defines `setup(tau)`. Built-in, user, and explicit extensions may
handle `project_trust` before protected loading; first decisive result wins.
Project extensions cannot approve themselves. They execute arbitrary Python and
remain disabled without both approval and the explicit code opt-in. Trust is not
a process/filesystem/network/tool/model sandbox.

## Dynamic providers

`tau.register_provider(DynamicProvider(...))` adds a frontend-free provider layer
to the current staged extension runtime. Definitions can be dormant (zero models)
and may provide either `OpenAICompatibleTransport` or a custom runtime factory.
Authentication uses `RequiredApiKey`, `OptionalApiKey`, or `NoAuth`; optional and
absent auth omit the `Authorization` header instead of inventing a key. Static
transport/model headers cannot contain `Authorization`; custom schemes belong in
a runtime auth strategy so credentials never become registered definitions. Custom
auth-resolution failures become categorical host errors; required-key failures keep
Tau's actionable missing-credential guidance.

Provider layers are process-local and never written to `catalog.toml`,
`providers.json`, session files, or generic extension storage. Latest registration
wins for a provider ID. The host derives stable source ownership from the canonical
extension entry path, not its display name. All IDs are frozen before any extension
import and stored through setup, so symlink retargeting cannot alter duplicate
detection, registration ownership, or complete failed-setup cleanup. Separately
loaded same-name files shadow and restore independently. Repeating the exact entry
source in one runtime is ignored with first-loaded precedence. Tools and commands
keep their existing first-registration-wins behavior. Source replacement is atomic;
source removal or generation retirement reveals the preceding complete dynamic layer or the exact
durable `ProviderConfig` baseline.

A `refresh_models` callback returns one complete `ProviderModelSnapshot`. Refreshes
are coalesced only when source/layer/generation and network policy match; every
caller keeps its own timeout. A final timeout or explicit cancellation is removed
from coalescing before returning, so an immediate retry starts fresh. Incompatible
network policies run separately. Cancellation is generation-owned: Tau issues one
task cancellation and waits up to 0.25 seconds from that request without
re-cancelling a callback's `finally` cleanup. Reload, replacement, reset ownership,
and final close await that cooperative drain. Cancellation after reload/replacement
publication is contained, and the adopted result returns only after outgoing
cleanup. Before publication, the replacement owns its candidate providers: shutdown
or start cancellation/failure closes them exactly once without closing the active
provider. Successful adoption transfers that ownership once. Final close propagates
cancellation only after its registry is discharged and each owned runtime provider
has closed exactly once. Work still running at the
bound is reported as contained rather than drained; a process-owned supervisor retains its task and
generation registry until actual completion, and it can never publish after replacement or
retirement. Failures retain the current snapshot and emit one bounded, secret-free
diagnostic. Nested compatibility data is deeply immutable while registered.
Callbacks receive structured data, not Rich or Textual objects.

Phase 6 validates these contracts with a permanent second fake backend and a
small test-only Ollama adapter. The trusted built-in `llama.cpp` provider uses
the same seams; no production Ollama backend is shipped. Provider discovery and
backend status may use different protocol endpoints, and `NoAuth` is a first-
class option. The built-in llama.cpp package now contributes version-gated
router management and Hugging Face search through these generic host contracts;
no router vocabulary or mutation logic enters extension core.

## Local-backend registrations

Extensions can pair a provider layer with a provider-neutral local backend:

```python
def setup(tau):
    tau.register_provider(provider)
    tau.register_local_backend(backend)
```

`LocalBackend` supplies structured text, secret, and choice fields plus async
configure, refresh, status, and optional doctor/reset/model-management actions.
The host renders these values; backend code never receives Textual widgets.
Configuration is submitted as one ephemeral transaction. Validation errors and
failed safe-state/credential commits leave the previous configuration active;
secret values are excluded from representations and host diagnostics.

Backends are owned by the same extension source and runtime generation as their
paired provider. If another source shadows that provider, inspection remains
possible but use, reset, and model-management actions are unavailable. Retiring
or reloading a generation cancels its backend operations and discards late
results. The built-in `/local` command opens the generic TUI host; print mode
reports that the command is interactive-only.

## Development checklist

1. Read this document and the closest installed example under `examples/extensions/` completely before implementing.
2. In a Tau checkout, also read `website/content/guides/extensions.md` and the relevant public extension API implementation.
3. Confirm the requested capability exists in the extension API before inventing a workaround.
4. Define `setup(tau)` and use documented registration APIs; do not reach into private session or Textual internals. For sidebar status, feature-detect `context.ui.sidebar`, check `sidebar.supported`, and call `set_section(...)` from `session_start` or another runtime event.
5. Keep extension behavior out of `tau_agent`; extensions belong to `tau_coding`. Use `tau_agent` types for portable messages and tools, and keep Textual behind Tau's UI adapter APIs.
6. Put user extensions in `~/.tau/extensions/`. Project extensions require explicit trust through `--project-extensions`; never enable one from an untrusted repository. Use `tau -e PATH` for isolated testing.
7. Test through the real extension runtime so discovery, imports, and `setup` registration are exercised. For Tau core changes, add deterministic tests with fake providers/tools and cover reload and lifecycle behavior when applicable.
8. Run focused tests followed by the repository's full pytest, Ruff, formatting, and mypy checks.
9. Update `website/content/guides/extensions.md` and add a development note for user-facing architectural changes.
