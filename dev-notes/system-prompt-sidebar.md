# System-prompt files in the TUI sidebar

## What changed

The TUI sidebar now shows a **system prompt** section whenever the active session
loaded `SYSTEM.md` or `APPEND_SYSTEM.md` from a user or project `.tau` directory.
It lists only selected files that contribute to the effective prompt; shadowed or
CLI-overridden files remain available through `/session` diagnostics.

## Why

System-prompt files affect every provider request but were previously invisible
in the persistent session summary. Listing their paths makes prompt customization
easier to notice and audit without mixing Tau-native prompt inputs into the
sidebar's project-context file list.

## Design

`CodingSession.system_prompt_files` exposes the selected replacement and append
source paths in assembly order. The TUI consumes that provider-neutral session
metadata, formats project and home paths consistently with context files, and
includes the paths in its redraw fingerprint so `/reload` updates the section.
The section is omitted when no discovered prompt files are active to avoid adding
noise to the default sidebar.

## Validation

Create `.tau/APPEND_SYSTEM.md`, launch Tau in that directory after trusting the
project, and confirm the sidebar shows:

```text
system prompt
  • .tau/APPEND_SYSTEM.md
```

Remove the file, run `/reload`, and confirm the section disappears.
