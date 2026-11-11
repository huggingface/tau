# Prevent headless TUI startup and help-discovery CPU loops

Issue: https://github.com/huggingface/tau/issues/794

## What happened

An automated `tau skills --help` invocation inherited `/dev/null` stdin and piped
its output to `head`. Tau's CLI shares a variadic positional argument between
prompts and its utility commands. Options after the first positional word are
normally literal prompt text, so this invocation became a TUI prompt rather than
a help request. `skills` is not a CLI subcommand; `sessions --help` exhibited the
same behavior even though `sessions` is a documented utility command.

The resulting process used approximately one CPU core for over four hours. A
macOS sample showed the `textual-input` thread repeatedly calling `select` and
`os.read` while the main thread waited. In Textual 8.2.8, reading EOF only breaks
the inner event-processing loop. An EOF descriptor remains readable, so the
outer selector loop immediately repeats. This is an input-EOF loop, not evidence
of model inference or broken-pipe retries.

## What changed

- The CLI's `run_openai_tui` adapter validates that stdin and stdout are usable
  terminals before release notices, session preparation, or Textual startup.
  Missing, closed, non-TTY, or unavailable streams produce an actionable error
  naming `--print`, `--mode text|json|transcript`, and `--mode rpc`.
- A small `TyperGroup` adapter recognizes the exact raw two-token invocation
  `WORD --help` and routes it to ordinary general CLI help. Checking raw tokens
  preserves explicit `--` semantics. It also preserves quoted single-argument
  prompts, longer prompts, and print-mode prompts containing `--help`.
- There is no new skills CLI command, command-specific help system, broad parser
  refactor, or change to utility/print/RPC terminal requirements.

The terminal check is deliberately at the real TUI runner boundary, not inside
`tau_agent` or Textual itself. This preserves Pi's separation: the reusable brain
has no terminal policy; the coding frontend decides when its UI can run.

This prevents accidental non-interactive startup. It does not patch Textual's
input driver or claim to handle a terminal disconnect after the UI starts.
Upstream EOF handling remains a separate follow-up; no dependency upgrade is
assumed to fix it.

## Verification

`tests/test_cli_terminal.py` covers utility/unsupported command help, literal
prompt preservation, print/RPC bypass, both terminal streams, missing/closed
streams, and rejection before session/UI preparation. Existing runner tests that
intentionally mock the UI bypass terminal validation explicitly.

Subprocess regressions exercise actual CLI startup with `/dev/null` and an
exhausted input pipe. Each subprocess has a timeout and guaranteed kill/wait
cleanup through `subprocess.run`, plus isolated homes and offline/update-check
disabling. A regression must not leave another CPU-burning process behind.

```bash
uv run pytest tests/test_cli.py tests/test_cli_terminal.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
cd website
npm ci
npm run check
npm run format:check
npm run build
npm test
```

Manual checks from an installed feature environment:

```bash
tau skills --help </dev/null 2>&1 | head -20  # general help, exits
tau sessions --help </dev/null              # general help, exits
tau </dev/null                             # actionable error, exits 2
printf '' | tau                            # actionable error, exits 2
tau                                        # normal TUI in a real terminal
```

For literal prompt behavior in a real terminal, compare `tau "skills --help"`
and `tau -- skills --help`; both keep the initial prompt text. Use a fake provider
or a configured account as appropriate, since these are genuine prompt requests.
