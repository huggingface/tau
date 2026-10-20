# Child-session seam: trust propagation and tool allow-lists

Hosts such as [tau-subagents](https://github.com/rian-dolphin/tau-subagents)
build child `CodingSession`s in the same process from inside a tool call, via
`CodingSession.load(CodingSessionConfig(...))`. From there the only Tau
surfaces available are `ExtensionContext` (reading the parent) and
`CodingSessionConfig` (constructing the child). This note covers two small
additions to that seam, following the precedent of `skills_enabled` and the
session-metadata properties in `extension-session-metadata.md`.

## What was added

### `ExtensionContext.project_trusted` / `project_trust_resolution`

`CodingSession.project_trust_resolution` already existed, but it was not part of
the `BoundSession` protocol, so extensions could not read it without reaching
into private state. It is now on the protocol and exposed on the context:

- `project_trust_resolution` returns the frozen `ProjectTrustResolution`
  (`trusted`, `source`, `saved_path`, diagnostics), or `None` for a session that
  never resolved trust.
- `project_trusted` is the derived bool hosts actually need.

Both assert the generation is active like every other context property. During
a `session_start("reload")` hook the value still reflects the outgoing
generation, matching the other session properties read at that point.

### `CodingSessionConfig.allowed_tool_names`

`CodingSessionConfig.tools` only replaces the built-in tool set;
`ExtensionRuntime.compose_tools()` then appends every extension-registered tool.
So `tools=[read, bash]` still produced `read, bash, <every extension tool>`, and
the only way to restrict the final list was to mutate
`session._harness.config.tools` after load — which `_reload` silently discarded
when it recomposed the tool list.

`allowed_tool_names: frozenset[str] | None` is applied by a `_filter_tools`
helper immediately after `compose_tools()` in both `CodingSession.load` and
`_reload`, before the system prompt is built (so filtered tools do not appear in
the prompt's tool section) and before the reload's `before_tool_names`
comparison (so a stable filtered list does not trigger a needless prompt
rebuild). `resume()` forwards the field when it rebuilds a config from scratch;
the `dataclasses.replace(self._config, ...)` sites carry it automatically.

Unknown names are ignored silently, mirroring Pi's `tools:` frontmatter, and an
extension tool that overrides a built-in of the same name is still subject to
the list. `CodingSession.extension_tool_sources` still reports every
*registered* extension tool, including ones the allow-list removed; callers
that need the active set intersect it with `session.tools`.

## Why

Since project-input trust (#541), a headless child session resolves trust with
`trust_default="ask"`, `trust_interactive=False`, `trust_prompt=None`, which
lands on `trusted=False`. Every subagent therefore lost the project's skills,
`AGENTS.md`, and project extensions even when the user had approved the repo for
the parent. The two bad alternatives were approving unconditionally (reopening
the hole #541 closed for declined repos) or leaving children untrusted. Reading
the parent's decision and forwarding it as `trust_override` is the correct
behaviour, and only the read side was missing.

## How to use

```python
child = await CodingSession.load(
    CodingSessionConfig(
        ...,  # provider, model, storage, cwd, resource_paths for the child
        trust_override="approve" if tau.context.project_trusted else "decline",
        allowed_tool_names=frozenset({"read", "bash"}),
        skills_enabled=False,
    )
)
```

## Tests

`tests/test_extensions.py`:

- `test_context_exposes_project_trust_decision` (fake `BoundSession`)
- `test_context_project_trusted_follows_session_trust_override` (real
  `CodingSession.load` with `trust_override`)
- `test_allowed_tool_names_caps_composed_tools_and_survives_reload`
- `test_allowed_tool_names_none_keeps_every_composed_tool`

References: huggingface/tau#729, #541, #643.
