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
