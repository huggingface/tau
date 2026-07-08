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

The `-p` / `--prompt` flag is what switches Tau into print mode. It still uses
the full coding-session environment — the same tools, project context, and
session storage as the TUI — so its turns are saved under `~/.tau/sessions/` too.

## Output formats

Choose how results are written with `-o` / `--output`:

```bash
tau -p "list the public functions in src/app.py" -o text        # default, human-readable
tau -p "list the public functions in src/app.py" -o json        # JSON, for parsing
tau -p "list the public functions in src/app.py" -o transcript  # structured transcript
```

- **text** — plain text with ANSI styling, for reading.
- **json** — machine-readable, for piping into other tools.
- **transcript** — a structured record of the turn.

## Choosing provider, model, and directory

The same selection flags work in print mode:

```bash
tau -p "explain this module" -m gpt-5.5
tau --provider local -p "explain this module"
tau -p "audit for secrets" --cwd ./services/api
```

## Exit status

Print mode exits non-zero if the run fails, so you can use it in scripts:

```bash
if tau -p "do the tests pass? answer yes or no" -o text | grep -qi yes; then
  echo "looks good"
fi
```

{{% tip %}}
For interactive work, start the [TUI]({{< relref "./tui.md" >}}) instead — you get streaming,
steering, pickers, and session branching.
{{% /tip %}}
