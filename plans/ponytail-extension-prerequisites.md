# Ponytail extension prerequisites implementation plan

**Status:** Approved on 2026-09-17 by the project owner in the Tau Ponytail planning session
**Goal:** Add the two generic Tau extension seams required by the Ponytail port: run-scoped system-prompt transformation and awaitable slash-command handlers.
**Behavioral source:** [`dev-notes/design/ponytail-extension-prerequisites.md`](../dev-notes/design/ponytail-extension-prerequisites.md), revision 1, approved by the project owner on 2026-09-17 in the Tau Ponytail planning session; SHA-256 `14d2cfe151e87575aea29459986ae9dc26ae43a242cc8e8e8ad748c5a406597c`.
**Decision record:** [`dev-notes/adr/0004-expose-run-scoped-prompt-and-awaitable-command-extension-seams.md`](../dev-notes/adr/0004-expose-run-scoped-prompt-and-awaitable-command-extension-seams.md), accepted on 2026-09-17.
**Baseline:** `origin/main` at `9fe6a719ac0bda3696e9fc8413643cfe9786c73c`.
**Plan type:** Two sequential, independently reviewable vertical slices under one owner. They share extension/session files, so parallel writers would add merge risk without shortening the critical path.

## Capture checkpoint

- **Vocabulary:** no new project-domain vocabulary requires a glossary. The API names follow Tau and Pi's existing extension terminology.
- **Decisions:** the consequential public-API and async-boundary choices are captured in accepted ADR 0004.
- **Behavior:** scope, non-goals, failure semantics, compatibility, and acceptance evidence are owned by approved design revision 1.
- **Uncertainty:** no material behavioral question remains. The old local `feat/pi-extension-ports` branch is historical evidence only and is excluded from implementation.
- **Security:** n/a. This change adds no trust boundary, credential handling, dependency, provider payload access, or new external input.

## Constraints

- Keep `tau_agent` provider-neutral; the new lifecycle contract belongs to `tau_coding`.
- Use the existing extension ordering, `_resolve`, diagnostic, and generation-isolation patterns.
- Do not add provider wrappers, durable prompt messages, private extension access, synchronous persistence, or a second command-dispatch API.
- Existing synchronous command handlers must continue to work.
- Each slice must leave the repository coherent and green before the next begins.

## Requirement map

| Approved-design requirement | Owning task |
| --- | --- |
| Chained sync/async prompt handlers | Task 1 |
| Prompt-hook failures diagnose and fail open | Task 1 |
| One transformed prompt covers a complete agent run and does not leak into the next | Task 1 |
| Failure/cancellation restores the base prompt | Task 1 |
| Queued input does not start a second transform chain | Task 1 |
| Existing synchronous extension commands remain valid | Task 2 |
| Async commands may await `append_entry()` before returning | Task 2 |
| CLI and TUI await command completion | Task 2 |

## Task 1 — Add the run-scoped `before_agent_start` prompt hook

**Prerequisites:** clean worktree at the recorded baseline or an explicitly refreshed baseline.

**Owned files:**

- `src/tau_coding/extensions/api.py`
- `src/tau_coding/extensions/__init__.py`
- `src/tau_coding/extensions/runtime.py`
- `src/tau_coding/session.py`
- `tests/test_extensions.py`
- `tests/test_coding_session.py`
- `website/content/guides/extensions.md`

**Consumes:** existing lifecycle registration, handler ordering, `_resolve`, runtime diagnostics, `CodingSession.prompt()`, and mutable `AgentHarnessConfig.system`.

**Produces:** public `BeforeAgentStartEvent`, public `BeforeAgentStartHookResult`, registration of `"before_agent_start"`, and one runtime method that returns the chained system prompt.

**Validation unit:** normal-risk public behavior change. `new-test` covers the reachable risks that one extension can corrupt later ordering, prevent a run, leak a transformed prompt across runs, or run again for queued input.

- [ ] Add frozen, slotted event/result dataclasses with the exact approved fields: expanded `prompt`, current `system_prompt`, and optional replacement `system_prompt`.
- [ ] Export the types and accept `"before_agent_start"` through the public event-registration contract.
- [ ] Add ordered runtime dispatch using the existing sync/async resolver. Give every handler the latest chained prompt; diagnose and skip exceptions, wrong result types, and non-string replacements; continue to later handlers.
- [ ] In `CodingSession.prompt()`, leave the existing running-session queue branches ahead of the new hook. For a new idle run, invoke the hook after input handling, expansion, model-limit refresh, and pre-prompt compaction but before `prompt_message()` starts the harness.
- [ ] Apply the transformed value to the harness for the whole run, including tool continuations and automatic retries. Restore the captured base prompt in an unconditional cleanup path after success, provider failure, or cancellation.
- [ ] Document the hook's timing, full-replacement chaining, ephemeral scope, and fail-open behavior with a minimal example.
- [ ] Red: add focused runtime tests for sync/async chaining and malformed/raising handlers, plus coding-session tests proving provider-visible replacement, no next-run leakage, cleanup on failure/cancellation, and no second invocation for queued input.
- [ ] Green: implement only the contract required to satisfy those tests.
- [ ] Verify:

  ```bash
  uv run pytest tests/test_extensions.py -k before_agent_start
  uv run pytest tests/test_coding_session.py -k before_agent_start
  uv run ruff check src/tau_coding/extensions src/tau_coding/session.py tests/test_extensions.py tests/test_coding_session.py
  uv run mypy
  ```

**Completion criterion:** the focused tests demonstrate every Task 1 row in the requirement map, public docs match the shipped types, and the base prompt is restored on every exit path.

## Task 2 — Await synchronous or asynchronous slash-command handlers

**Prerequisites:** Task 1 accepted on the shared branch. No behavioral dependency exists, but the files overlap and this order keeps reviews and conflict resolution linear.

**Owned files:**

- `src/tau_coding/commands.py`
- `src/tau_coding/extensions/api.py`
- `src/tau_coding/extensions/runtime.py`
- `src/tau_coding/session.py`
- `src/tau_coding/cli.py`
- `src/tau_coding/tui/app.py`
- `tests/test_commands.py`
- `tests/test_extensions.py`
- `tests/test_coding_session.py`
- `tests/test_cli.py`
- `tests/test_tui_app.py`
- `website/content/guides/extensions.md`
- `website/content/internals/custom-frontend.md`

**Consumes:** current command parsing/result types, extension failure containment, `ExtensionAPI.append_entry()`, and the CLI/TUI async submit paths.

**Produces:** awaitable-aware command handler types, async `CommandRegistry.execute()`, async `CodingSession.handle_command()`, and awaited host call sites.

**Validation unit:** normal-risk public async migration. `new-test` covers the distinct persistence-ordering and post-await exception paths; existing command tests are mechanically migrated and continue to guard built-in behavior.

- [ ] Broaden core and extension command handler aliases to accept direct or awaitable results. Keep built-in handler implementations synchronous.
- [ ] Make `CommandRegistry.execute()` async and resolve either form once. Make `CodingSession.handle_command()` async and await registry dispatch while preserving prompt-template bypass behavior.
- [ ] Make the extension command adapter async. Await the registered handler inside the existing exception boundary so failures raised after suspension produce the same diagnostic and handled error result as synchronous failures.
- [ ] Update the two CLI call sites and the TUI submit path to await `handle_command()`. Update test doubles and every direct test caller; do not add a synchronous compatibility wrapper or sibling async method.
- [ ] Add one extension-runtime regression in which an async command awaits `append_entry()` and assert the durable custom entry exists before `handle_command()` returns.
- [ ] Add one async extension-command failure regression and retain an explicit synchronous-handler regression.
- [ ] Update extension and custom-frontend documentation to show `await session.handle_command(text)` and an async command that persists state before returning.
- [ ] Red: add the async persistence/failure tests before changing dispatch.
- [ ] Green: migrate the command path and existing tests with no command-result behavior changes.
- [ ] Run a stale-reference search; every production invocation of `handle_command()` must be awaited, while documentation must not show the old synchronous call.
- [ ] Verify:

  ```bash
  uv run pytest tests/test_commands.py tests/test_extensions.py tests/test_coding_session.py tests/test_cli.py tests/test_tui_app.py -k command
  rg -n 'handle_command\(' src tests website/content
  uv run ruff check src/tau_coding tests
  uv run mypy
  ```

**Completion criterion:** sync and async handlers share one awaited path; async persistence is complete before command success; async failures are contained; all host and documented call sites use the new API.

## Integration and release gate

After both tasks and one candidate review of the combined delta:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
(cd website && hugo --minify)
```

One parent-authorized fix pass may address review or gate findings, followed by one delta-only recheck and the affected gates. Any remaining material blocker returns to the owner rather than opening another review cycle.

## Residual risks and manual checks

- The `CodingSession.handle_command()` async change is source-breaking for external custom frontends. The published migration example and release notes must call this out when the change is prepared for release.
- No manual provider smoke is required: deterministic fake-provider tests observe the exact system prompt and command completion ordering.
- Upstream issue/PR creation, branch publication, release assignment, and the downstream Tau Ponytail implementation are outside this plan and retain separate authorization gates.

## Handoff provenance

- Planning contract: version 1.
- Installed skill provenance: unknown.
- Capture outcome: behavior approved, vocabulary complete, ADR 0004 accepted, and no material uncertainty remains.
