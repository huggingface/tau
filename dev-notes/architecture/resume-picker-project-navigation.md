# `/resume` project navigation

## What changed

PR #669 made sessions from every working directory available in `/resume`. This
follow-up presents that history as a two-column navigator instead of appending
foreign sessions below the current project's sessions:

- **Projects** lists compact folder names, with the current working directory
  first, followed by other projects ordered by their most recent indexed
  session.
- **Recent sessions** shows only the selected project's sessions, newest first;
  its header preserves the selected directory's full path.
- Left moves navigation to projects; Up/Down changes the project and updates its
  session list immediately; Right returns to sessions; Enter resumes.
- Clicking a project opens its session list without resuming anything.
- Search remains focused for typing and filters names/models within the selected
  project.

The active column gets an accent border and title. The current project gets a
marker, every project row includes its session count, and the session list has
horizontal breathing room inside its border.

## Architecture

This remains entirely in `tau_coding.tui.app.SessionPickerScreen`. Session
storage, indexing, and the portable `tau_agent` harness are unchanged. The
picker derives canonical project paths from the records supplied by the session
manager, retaining the existing current-project-first and recency ordering.

## Verification

Automated Textual pilot tests cover keyboard column switching, project clicks,
project-local search, empty projects, repeated list refreshes, boundary
navigation, and session resume.

Manual check:

1. Open `/resume` or press Ctrl+R in a project with session history elsewhere.
2. Confirm the current project's recent sessions are visible initially.
3. Press Left and use Up/Down to preview other projects' sessions.
4. Press Right, choose a session, and press Enter to resume it.
5. Confirm typing filters only the selected project's sessions and Escape closes
   without resuming.
