# Session and project archiving

The `/resume` picker can archive its highlighted session or the highlighted
project with **Delete**. Archiving moves session metadata from active indexes to
`~/.tau/sessions/archive.jsonl`. It never removes JSONL transcripts, project
directories, or other user files. The picker's **Archived** tab reads that index;
selecting a session or project there and pressing **Enter** restores its metadata
to the active indexes.

`SessionManager.archive_session()`, `archive_project()`, `unarchive_session()`,
and `unarchive_project()` own the persisted behavior. The Textual picker calls
those methods off the UI thread and only moves rows after persistence succeeds.
Archiving one session leaves its project's other active sessions visible;
restoring a project restores all archived sessions belonging to that project.

Tests cover active/archive index transitions, repeated/idempotent calls,
preservation of transcripts and directories, and both picker columns in the
Active and Archived tabs.
