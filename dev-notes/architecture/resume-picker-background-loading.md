# `/resume` background loading

## What changed

The TUI now opens `/resume` from the current project's small index, then reads
all other project indexes in a background thread. The open picker shows a
loading message and remains usable; once loading finishes, its project list is
updated without replacing the modal.

Plain `/resume` command completion no longer reads session indexes while the
command name itself is being typed. Session-id completion remains available
when argument text follows `/resume `.

## Why

Aggregating every `~/.tau/sessions/*/index.jsonl` file synchronously blocked the
Textual event loop before the modal could appear. Moving that filesystem and
Pydantic parsing work off the event loop makes the common current-project path
available immediately while preserving cross-project discovery.

## Safety and behavior

- Background failures leave current-project sessions usable and show a warning.
- Results are ignored if the picker was dismissed or replaced.
- Refresh preserves the selected project, search query, and selected session
  when those records still exist.
- The session manager and portable agent harness remain independent of Textual.

## Verification

A Textual pilot test blocks the global index read, verifies that the modal and
local sessions are already interactive, then releases the read and verifies
that the other project appears. Existing picker navigation, search, and resume
tests cover the refreshed list.
