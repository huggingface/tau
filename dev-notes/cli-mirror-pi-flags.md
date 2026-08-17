# Mirroring Pi's non-interactive CLI flags

Issue: https://github.com/huggingface/tau/issues/439

This PR started as a single-flag rename (`--resume` -> `--session`) and grew,
in place, into a full mirror of Pi's non-interactive CLI flag surface. The
version bump `0.2.4` -> `0.3.0` covers all of the breaking renames below, not
just the original one.

## What changed

| Tau (before) | Tau (now) | Notes |
|---|---|---|
| `--resume <id>` | `--session <id>` | Matches Pi's `--session <path\|id>`. Id only for now; JSONL path support is a possible follow-up. |
| `-p, --prompt <text>` | `-p, --print` (boolean) + positional prompt | Matches Pi's `-p, --print`. The prompt is the same positional argument the TUI already uses for an initial prompt. |
| `-o, --output <text\|json\|transcript>` | `--mode <text\|json\|transcript>` | Matches Pi's `--mode` flag name. Tau keeps its own value set (no RPC mode; Pi has no `transcript` mode) — see the audit table below. Passing `--mode` on its own also triggers non-interactive mode, mirroring `pi --mode json "prompt"` needing no separate `-p`. |
| `-x, --extension <path>` | `-e, --extension <path>` | Matches Pi's `-e, --extension`. |
| `tau export <id> [out]` (subcommand only) | `tau export <id> [out]` **and** `tau --export <id> [out]` | Added `--export` as a top-level flag alias, matching Pi's `--export <in> [out]`, while keeping the subcommand for backward compatibility (it isn't deprecated — it fits Tau's other subcommands). |
| `--version` | `--version`, `-v` | Added the `-v` short flag, matching Pi's `-v, --version`. |
| (none) | piped stdin merges into the print-mode prompt | Mirrors Pi's `cat file \| pi -p "..."` behavior: when stdin is not a TTY, its contents are prepended to the prompt. A piped body can also be the *entire* prompt (`tau -p` with no positional text). |

Every renamed/removed flag keeps a **hidden** option under its old name that
raises a clear migration error instead of Typer's generic "no such option":

- `--resume <id>` -> `--resume was renamed to --session. Use \`tau --session <id>\` instead.`
- `--prompt <text>` -> `--prompt was removed. Pass the prompt positionally and use --print, e.g. \`tau --print "<text>"\`.`
- `-o/--output <mode>` -> `--output was renamed to --mode. Use \`tau --mode <mode>\` instead.`
- `-x <path>` -> `-x was renamed to -e/--extension.`

None of these old flags do anything anymore; they only exist to produce a
friendly error.

### Flag ordering

Tau's CLI accepts an unquoted, multi-word prompt as trailing positional
arguments (`tau fix the bug in main.py`, no quotes needed). To support that,
the root command uses Click's `ignore_unknown_options` + a variadic
`prompt_args` argument. One consequence: once positional-argument capture
starts, everything after it — including tokens that look like flags — is
absorbed into the prompt. Put flags **before** the prompt:

```bash
# Works: flags precede the prompt
tau -p --mode json "summarize this"

# Breaks: --mode is swallowed into the prompt text
tau -p "summarize this" --mode json
```

This matches Pi's own documented examples, which always place flags before
the trailing message (`pi --model gpt-4o "Help me refactor"`).

## Audit of Tau vs Pi non-interactive flags

Per the [Pi CLI reference](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md#cli-reference),
the full audit and disposition:

| Tau (current) | Pi | Disposition |
|---|---|---|
| `--session <id>` | `--session <path\|id>` | **Fixed.** Renamed from `--resume`. Id only for now; path support left as a follow-up. Pi's own `-r/--resume` (no value, opens a picker) is not adopted; Tau's picker already lives in the TUI's `/resume` slash command. |
| `-p, --print` (boolean) | `-p, --print` (boolean) | **Fixed.** Prompt moved to a positional argument, matching Pi. |
| `--mode <text\|json\|transcript>` | `--mode json\|rpc` | **Fixed the flag name; kept Tau's own value set, justified.** Tau's `text`/`json`/`transcript` output modes do not map 1:1 onto Pi's `json`/`rpc` (Tau has no RPC mode; `transcript` has no Pi equivalent). Adding an RPC mode is a much larger effort (a process-integration protocol) and is out of scope here. |
| `-e, --extension <path>` | `-e, --extension <source>` | **Fixed.** |
| `tau export`/`tau --export` | `--export <in> [out]` | **Fixed by addition.** Added the flag form; kept the subcommand form since it fits Tau's other subcommands (`sessions`, `providers`, `setup`, `update`), none of which Pi has an equivalent for either. |
| `-v, --version` | `-v, --version` | **Fixed.** |

## Flag names reserved for future Pi parity

Tau should not add new flags that squat on names Pi uses for different
things. Known Pi flags Tau does not yet have: `-c/--continue`, `--fork`,
`--no-session`, `--name/-n`, `--session-dir`, `--mode rpc`. If Tau adds
equivalent features later, prefer these names with matching semantics.

## Exit resume hint (issue #438)

Issue #438 (print a resume hint on exit) landed in #441 while this PR was in
flight, printing `To resume this session: tau --resume <session-id>`. This PR
rebased onto that change and updated the printed hint (and the matching
internal `--resume and --new-session cannot be used together` message in
`src/tau_coding/tui/app.py`) to use `--session`:

```text
To resume this session: tau --session <session-id>
```

## Testing

```bash
uv run pytest tests/test_cli.py tests/test_tui_app.py
```

Manual smoke test:

```bash
tau -p "hello"                                 # print mode, text output
tau -p --mode json "hello"                     # print mode, JSON output
tau --mode json "hello"                        # --mode alone also triggers print mode
cat README.md | tau -p "summarize this"        # stdin merged into the prompt
cat README.md | tau -p                          # stdin alone as the prompt
tau -e ./my-extension.py "hello"               # load an extension
tau --export <session-id> out.html             # export via the flag form
tau --session <session-id>                     # resume a session
tau -v                                          # short version flag
tau --resume x / --prompt x / --output json / -x . # all error with a migration hint
```
