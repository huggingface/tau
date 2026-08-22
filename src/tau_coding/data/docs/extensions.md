# Tau extensions

Tau extensions are Python modules that can register custom tools and slash commands, observe lifecycle events, intercept tool calls and results, show UI dialogs, and customize message rendering.

## Start here

For complete API documentation, read the repository's published guide when working in a Tau checkout:

- `website/content/guides/extensions.md`
- `dev-notes/architecture/phase-21-extensions.md`

Installed examples are under `examples/extensions/` next to these docs. Read the relevant example completely before implementing an extension.

## Locations

- `~/.tau/extensions/`: discovered by default.
- `<project>/.tau/extensions/`: requires project approval and `--project-extensions`.
- `tau -e PATH`: explicitly load a file or directory.

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

An extension defines `setup(tau)`. Built-in, user, and explicit extensions may
handle `project_trust` before protected loading; first decisive result wins.
Project extensions cannot approve themselves. They execute arbitrary Python and
remain disabled without both approval and the explicit code opt-in. Trust is not
a process/filesystem/network/tool/model sandbox.

## Per-run system prompts

Register `before_agent_start` to replace the system prompt for one agent run.
Handlers receive `BeforeAgentStartEvent.system_prompt` and a typed
`system_prompt_inputs` snapshot, then may return
`BeforeAgentStartHookResult(system_prompt=...)`. Handlers run in registration
order and each sees the prior replacement. The final prompt remains active for
tool-loop requests, then the next run starts again from the session's base
prompt. It is never added to the transcript.

During this hook, both `event.system_prompt` and `context.system_prompt` expose
the current chained value.

Skill metadata includes `disable_model_invocation`, matching whether a skill is
eligible for model invocation. Prompt inputs can contain project instructions
and paths. Their container types hide values from `repr` and Tau diagnostics,
but extensions should still treat
explicitly accessed fields as sensitive. See
`examples/extensions/prompt_customizer.py` for a complete extension.

## Development checklist

1. Read this document and the closest installed example under `examples/extensions/` completely before implementing.
2. In a Tau checkout, also read `website/content/guides/extensions.md` and the relevant public extension API implementation.
3. Confirm the requested capability exists in the extension API before inventing a workaround.
4. Define `setup(tau)` and use documented registration APIs; do not reach into private session or Textual internals.
5. Keep extension behavior out of `tau_agent`; extensions belong to `tau_coding`. Use `tau_agent` types for portable messages and tools, and keep Textual behind Tau's UI adapter APIs.
6. Put user extensions in `~/.tau/extensions/`. Project extensions require explicit trust through `--project-extensions`; never enable one from an untrusted repository. Use `tau -e PATH` for isolated testing.
7. Test through the real extension runtime so discovery, imports, and `setup` registration are exercised. For Tau core changes, add deterministic tests with fake providers/tools and cover reload and lifecycle behavior when applicable.
8. Run focused tests followed by the repository's full pytest, Ruff, formatting, and mypy checks.
9. Update `website/content/guides/extensions.md` and add a development note for user-facing architectural changes.
