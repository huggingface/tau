# Explicit system-prompt CLI controls

Issue: https://github.com/huggingface/tau/issues/530

## What changed

Tau now exposes Pi-compatible startup controls:

```bash
tau --system-prompt "Custom base" \
  --append-system-prompt ./shared-rules.md \
  --append-system-prompt "Local rule" \
  -p "review this"
```

`--system-prompt TEXT_OR_PATH` replaces the generated base. The repeatable
`--append-system-prompt TEXT_OR_PATH` values retain command-line order and are
joined with `\n\n` (exactly one blank line). As with Tau's other options, these
recognized flags must precede positional prompt text.

## Resolution and errors

Each option value is resolved independently. Tau expands `~`; if the resulting
path exists, Tau reads it as UTF-8. If it does not exist, the original value is
literal prompt text. Existing directories, unreadable files, and invalid UTF-8
files fail startup with a concise diagnostic containing both the option and
path. This matches Pi's file-when-existing, literal-otherwise policy without
adding automatic prompt-file discovery.

## Architecture and resume behavior

Resolution belongs to `tau_coding.cli`. The resolved values flow through print
mode or the Textual adapter into `CodingSessionConfig.custom_system_prompt` and
`append_system_prompt`. They deliberately do not use `CodingSessionConfig.system`,
which is an exact low-level override. Therefore a custom base still receives
append text, project context, eligible skills when `read` is available, date,
and cwd from the existing prompt builder.

The same values are supplied when `--session` resumes a TUI session, so its next
provider request uses the startup override. Prompt controls are not written into
session JSONL; callers must supply them again on a later resume.

Out of scope: automatic `SYSTEM.md` discovery, extension runtime overrides, and
export behavior.

## Verification

```bash
uv run pytest tests/test_cli.py tests/test_tui_app.py tests/test_system_prompt.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
