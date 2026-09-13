# `/resume` background loading

## What changed

The TUI now mounts an empty `/resume` shell first, then reads the current and
other project indexes in a background thread. The open picker shows staged
loading messages and remains usable; first the current project's sessions and
then the complete project list appear without replacing the modal.

Both picker columns use Textual's virtualized `OptionList`. Previously each
session and project became a mounted `ListItem`, even though the modal displays
at most 16 rows. Large histories therefore spent far more time constructing
widgets than parsing indexes. `OptionList` retains every searchable choice but
renders only visible lines.

Plain `/resume` command completion no longer reads session indexes while the
command name itself is being typed. Session-id completion remains available
when argument text follows `/resume `.

## Why

Aggregating every `~/.tau/sessions/*/index.jsonl` file synchronously blocked the
Textual event loop before the modal could appear. Eagerly mounting hundreds of
row widgets added a larger delay. Moving cross-project filesystem and Pydantic
work off the event loop, and virtualizing both columns, makes the common
current-project path available immediately while preserving cross-project
discovery and search.

## Safety and behavior

- A global background failure leaves any loaded current-project sessions usable
  and shows a warning.
- Results are ignored if the picker was dismissed or replaced.
- Refresh preserves the selected project, search query, and selected session
  when those records still exist.
- The session manager and portable agent harness remain independent of Textual.

## Verification

A Textual pilot test blocks the global index read, verifies that the modal and
local sessions are already interactive, then releases the read and verifies
that the other project appears. Existing picker navigation, search, and resume
tests cover the refreshed list.
