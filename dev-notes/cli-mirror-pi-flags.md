# Mirroring Pi's non-interactive CLI flags

Issue: https://github.com/huggingface/tau/issues/439

## What changed

- `tau --resume <session-id>` is renamed to `tau --session <session-id>`,
  matching Pi's `--session <path|id>` naming. This is a **breaking change**
  for scripted use of Tau; the version was bumped `0.2.4` -> `0.3.0`.
- `--resume` still exists as a hidden option so that using it produces a
  clear migration error (`--resume was renamed to --session. Use
  \`tau --session <id>\` instead.`) rather than Typer's generic "no such
  option" message. It never resumes a session.
- The `--session`/`--new-session` conflict check and validation message were
  updated to reference `--session`.
- Docs (`website/content/reference/cli.md`,
  `website/content/guides/sessions.md`) and CLI help text now say
  `--session`.
- The in-TUI `/resume` slash command (`src/tau_coding/commands.py`,
  `src/tau_coding/tui/autocomplete.py`) is unaffected: it is a distinct,
  interactive picker/id command, not the process-startup CLI flag.

## Audit of Tau vs Pi non-interactive flags

Per the [Pi CLI reference](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md#cli-reference),
the full audit and disposition for this change:

| Tau (current) | Pi | Disposition |
|---|---|---|
| `--resume <id>` | `--session <path\|id>` | **Fixed here.** Renamed to `--session <id>`. Path support (Pi also accepts a JSONL file path) is left as a follow-up; Tau's `--session` accepts an indexed session id only for now. Pi's own `-r/--resume` (no value, opens a picker) is not adopted; Tau's picker already lives in the TUI's `/resume` slash command. |
| `--prompt`, `-p <text>` | `-p`, `--print` (boolean, prompt is positional) | **Deferred.** Aligning `-p` to a boolean print-mode flag with a positional prompt is a bigger behavior change than a rename (Tau's print mode is currently keyed on `--prompt` being set). Left for a follow-up issue/PR so it can be scoped and tested on its own. |
| `--output`, `-o <text\|json\|transcript>` | `--mode json\|rpc` | **Kept as-is, justified.** Tau's `text\|json\|transcript` output modes do not map 1:1 onto Pi's `json\|rpc` modes (Tau has no RPC mode; `transcript` has no Pi equivalent). Renaming the flag without renaming the values would be more confusing than the current divergence. |
| `--extension`, `-x <path>` | `-e`, `--extension <source>` | **Kept as-is, justified.** `-x` does not collide with any Pi short flag Tau exposes, and repointing Tau's established `-x` shortcut for parity with Pi's `-e` would itself be a breaking, low-value rename. |
| `tau export <id> [out]` (subcommand) | `--export <in> [out]` (flag) | **Kept as-is, justified.** Tau's `export` sits alongside its other subcommands (`sessions`, `providers`, `setup`, `update`); folding it into a flag on the root command would be a larger structural change than this issue's scope. |
| `--version` | `-v`, `--version` | **Deferred.** Adding the `-v` short flag is low-risk and could land as a small follow-up; not required for this rename. |

## Flag names reserved for future Pi parity

Per the issue, Tau should not add new flags that squat on names Pi uses for
different things. Known Pi flags Tau does not yet have: `-c/--continue`,
`--fork`, `--no-session`, `--name/-n`, `--session-dir`. If Tau adds
equivalent features later, prefer these names with matching semantics.

## Exit resume hint (issue #438)

Issue #438 (print a resume hint on exit) was still unimplemented at the time
of this change, so there is no existing hint string to update. Whoever
implements #438 should print:

```text
To resume this session: tau --session <session-id>
```

using the flag name from this change.

## Testing

```bash
uv run pytest tests/test_cli.py tests/test_tui_app.py
```
