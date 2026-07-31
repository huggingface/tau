---
title: Print mode & scripting
description: Run Tau non-interactively for a single prompt — ideal for scripts, pipes, and CI.
---

Print mode runs a single prompt without the interactive UI and writes the result
to the terminal. It's the right choice for scripts, pipelines, and one-off
questions.

## Basic use

```bash
tau -p "summarize the changes in the last commit"
```

The `-p` / `--print` flag switches Tau into print mode; the prompt itself is a
plain positional argument, the same as an initial prompt for the TUI. Print
mode still uses the full coding-session environment — the same tools, project
context, and session storage as the TUI — so its turns are saved under
`~/.tau/sessions/` too.

Put flags **before** the prompt. Tau accepts multi-word prompts without
quoting, so anything after the last recognized flag — including tokens that
look like other flags — is treated as prompt text:

```bash
tau -p --mode json "list the public functions in src/app.py"
```

## Output formats

Choose how results are written with `--mode`. Passing `--mode` on its own also
switches Tau into non-interactive mode, so `-p` is optional once `--mode` is set:

```bash
tau -p --mode text "list the public functions in src/app.py"        # default, human-readable
tau --mode json "list the public functions in src/app.py"           # JSON, for parsing
tau -p --mode transcript "list the public functions in src/app.py"  # structured transcript
```

- **text** — plain text with ANSI styling, for reading.
- **json** — machine-readable, for piping into other tools.
- **transcript** — a structured record of the turn.

Piped stdin is merged into the prompt, so you can feed file contents in:

```bash
cat README.md | tau -p "Summarize this text"
```

A piped body can also be the entire prompt — `tau -p` with no positional text
and piped stdin is valid:

```bash
cat README.md | tau -p
```

## Choosing provider, model, and directory

The same selection flags work in print mode:

```bash
tau -m gpt-5.5 -p "explain this module"
tau --provider local -p "explain this module"
tau --cwd ./services/api -p "audit for secrets"
```

## Recording the session id

Automation can choose the exact id of a new print-mode session with
`--session-id`. This keeps stdout dedicated to the selected output format and
avoids scanning `~/.tau/sessions/`:

```bash
worker_session_id="$(python -c 'import uuid; print(uuid.uuid4().hex)')"
tau --print --new-session \
  --session-id "$worker_session_id" \
  --cwd /path/to/project \
  "review the current changes"
printf 'Tau session: %s\n' "$worker_session_id"
```

Ids may contain letters, numbers, `.`, `_`, and `-`, must start and end with a
letter or number, and may be at most 128 bytes. `default` and `index` are
reserved. Use a unique id for each worker. Tau atomically reserves the transcript
and exits with an error rather than opening or overwriting an existing session,
even if an unindexed transcript already uses that id or two workers start at the
same time. The option applies to text, JSON, and transcript modes without adding
metadata to their stdout output.

## Exit status

Print mode exits non-zero if the run fails, so you can use it in scripts:

```bash
if tau --mode text -p "do the tests pass? answer yes or no" | grep -qi yes; then
  echo "looks good"
fi
```

{{% tip %}}
For interactive work, start the [TUI]({{< relref "./tui.md" >}}) instead — you get streaming,
steering, pickers, and session branching.
{{% /tip %}}
