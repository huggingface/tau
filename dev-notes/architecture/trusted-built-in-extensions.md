# Trusted hidden built-in extensions

## What Phase 2 adds

Issue #606 adds a generic way for Tau to run extension code bundled in the
installed package. It does not add llama.cpp, startup provider preparation,
`/local`, a local-backend API, or network work.

The declaration is intentionally small:

```python
BuiltInExtension(name="capability", setup=setup, hidden=True)
```

Declarations live in `tau_coding.built_in_extensions.BUILT_IN_EXTENSIONS`.
Later phases can add product capabilities to that tuple without teaching the
loader their names. The Phase 2 production tuple is empty; deterministic tests
inject a minimal fake declaration.

## Why built-ins use extensions

A bundled capability still needs normal lifecycle and failure boundaries. If it
registered a tool, command, or provider through private host branches, reload and
source cleanup would differ from user extensions and each capability would make
core more provider-specific.

Instead, a built-in receives the ordinary `ExtensionAPI`. The Phase 2 fake proves
that one setup function can register:

- an `AgentTool`;
- a slash command;
- a Phase 1 `DynamicProvider` layer.

The real `ExtensionRuntime` composes all three. No test-only loader bypass is
used.

## Load order

`ExtensionRuntime.load()` first calls its once-per-generation built-in loader,
then performs normal filesystem discovery:

```text
trusted built-in declarations
→ user extension directory
→ explicit -e paths
→ trusted, opted-in project extension directory (later staged call)
```

`include_resource_dirs=False`, which implements `--no-extensions`, skips user
and project directory discovery but not built-ins or explicit paths. A session
now always calls `ExtensionRuntime.load()`, even when discovery is disabled, so
built-ins cannot accidentally disappear in that mode.

Project extensions remain a separate post-trust load. Built-ins are direct
installed-package callables, not filesystem candidates, so they neither add a
protected-input category nor cause a trust prompt. They may use the existing
pre-trust hook only when a project already has protected inputs; this phase's
fake does not.

## Provenance and hidden metadata

Filesystem discovery now labels every source as `user`, `explicit`, or
`project`. Built-ins use:

```text
source      = built-in
source_id   = built-in:<declaration name>
path        = None
hidden      = declaration.hidden
```

The runtime retains this in immutable `ExtensionSourceMetadata`. The host-owned
source ID, rather than the display name, owns tools, commands, handlers, and
provider layers.

`extension_names` remains the ordinary visible listing and omits hidden
built-ins. `extension_metadata` is the detailed diagnostic view and includes
both visible and hidden sources. Hidden therefore means “not advertised as an
installed/discovered extension,” never “not loaded.”

## Failure isolation

Built-in setup runs through the same `_setup_extension()` boundary as imported
extensions. The runtime creates one source-scoped API, records the source, and
then calls setup synchronously. If setup raises:

1. the failed source is removed from active extension metadata;
2. its tools, commands, guidelines, renderers, and handlers are removed;
3. all of its dynamic provider layers and refresh work are unregistered;
4. a `built-in setup failed` extension diagnostic is retained;
5. the next declaration and normal startup continue.

This keeps built-in failures visible without turning them into host startup
failures.

## Generation and cleanup lifecycle

Every staged `ExtensionRuntime` snapshots the declaration tuple and creates a
fresh `ExtensionGeneration` plus Phase 1 provider registry. Repeated `load()`
calls on one runtime do not rerun built-in setup; this matters because project
extensions load in a second post-trust call.

Reload, resume, new-session, and cwd replacement already stage fresh runtimes.
Each therefore reruns built-in setup with a fresh API and provider generation.
When the old runtime retires, it:

- retires and detaches every dynamic provider layer;
- requests cancellation of owned provider refresh/discovery work;
- invalidates every captured API/context/UI facade;
- unsubscribes the harness listener;
- removes extension handlers, tools, commands, guidelines, and renderers;
- drops bound-session and turn-request callbacks.

Replacement code clears old host UI before the successor mounts UI on the shared
bridge. Retirement deliberately does not clear that bridge again: doing so after
the successor's `session_start` would erase fresh widgets. Final session close has
no successor, so it clears extension components before retiring the runtime.

Async close then drains cooperative provider work or reports Phase 1's bounded
containment result. Tests start a fake built-in provider refresh, retire the
runtime, and assert cancellation, stale API rejection, empty source metadata,
and removal of the tool, command, and provider.

## Security boundary

“Trusted” means the code is reviewed and shipped inside Tau. It does not mean a
sandbox. Like every extension, a built-in executes Python in the Tau process.
Hidden status is display metadata only. Project trust remains an ambient
project-input guard and does not sandbox built-ins, providers, tools, network,
filesystem, credentials, or subprocesses.

## Pi alignment and intentional Tau choices

This follows Pi's hidden bundled-extension pattern and source invalidation on
reload. Tau deliberately keeps its own cwd/trust-bound fresh-runtime staging,
source/generation-aware Phase 1 provider registry, and detailed metadata view.
There is no process-global built-in runtime and no capability-name branch in the
loader.

## How to verify

Focused deterministic coverage:

```bash
uv run pytest tests/test_extensions.py tests/test_extension_providers.py \
  tests/test_project_trust.py
```

The tests cover declaration validation, real runtime registration, hidden/source
metadata, load order, `--no-extensions`, setup-once behavior, setup rollback,
no trust prompt, fresh reload/new/resume generations, stale API invalidation,
and provider-task retirement.

Full repository gates remain:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
hugo --source website --minify
uv build
```
