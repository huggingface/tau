# Tau CLI and commands

Tau supports print mode, Pi-compatible JSONL RPC mode, and a Textual interactive TUI. The CLI entry point is `tau_coding.cli:app`.

For current user-facing behavior in a Tau checkout, read:

- `website/content/reference/cli.md`
- `website/content/reference/slash-commands.md`
- `src/tau_coding/commands.py`

Keep command parsing and application-specific resource loading in `tau_coding`, not the reusable `tau_agent` harness. When changing behavior, test both command results and the relevant print/TUI integration, then update published reference documentation.

## Project trust startup controls

`--approve`/`-a` and `--no-approve`/`-na` are mutually exclusive run-only
overrides. They are parsed before protected resource loading and never persist.
Headless modes never prompt; user-global `defaultProjectTrust` controls unresolved
projects. Keep diagnostics on stderr for structured stdout modes. See
`security.md` and `website/content/guides/project-trust.md`.

## System prompt startup controls

`--system-prompt TEXT_OR_PATH` replaces the default base prompt.
`--append-system-prompt TEXT_OR_PATH` is repeatable; values retain command-line
order and are separated by exactly one blank line. Put recognized flags before
the positional prompt.

Each value is read as UTF-8 when it names an existing path (with `~` expanded),
or used verbatim when the path does not exist. An existing directory, unreadable
file, or invalid UTF-8 file stops startup with a diagnostic naming the option
and path.

Both flags apply to print and interactive TUI startup, including the next request
of a session resumed with `--session`. They are not persisted, so pass them on
each later resume that needs them. A custom base still goes through
`build_system_prompt`: configured append text, project context, eligible skills,
the current date, and cwd remain included. Do not route this CLI option through
the lower-level exact `CodingSessionConfig.system` override.

## System prompt files

Tau discovers replacement `SYSTEM.md` and append-only `APPEND_SYSTEM.md` files
from `<cwd>/.tau/` and `~/.tau/`. Explicit CLI prompt input wins over a project
file, which wins over a user file. Project and user append files are alternatives,
not cumulative layers. `.agents/SYSTEM.md` and `.agents/APPEND_SYSTEM.md` are not
supported because these are Tau-specific configuration resources.

The selected replacement still receives append text, project context, eligible
skills, date, and cwd. Run `/reload` after changing files; the next-turn prompt is
rebuilt without adding prompt contents to session history. Selected, shadowed,
and CLI-overridden sources appear in resource diagnostics. Unreadable or invalid
UTF-8 selected files stop startup/reload with an actionable error.

Project files load only after the canonical cwd trust decision. Users should
still inspect trusted `.tau/SYSTEM.md` and `.tau/APPEND_SYSTEM.md`: project trust
is an input-loading guard, not a sandbox or prompt-safety guarantee.
