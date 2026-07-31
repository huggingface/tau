# Searchable tools reference

Issue #453 adds `/tools` to the built-in command registry. In the Textual frontend, the command opens a searchable tool browser built from `CodingSession.tools`, so it always reflects the active harness after startup or `/reload`.

The modal renders a compact table: tool name, origin, and description character count on one line. Built-ins appear first, sorted alphabetically. Extension groups follow in extension load/registration order, with each group's tools in registration order. Origins are `Built in` or the extension name; `CodingSession` exposes the extension runtime's ordered registration metadata so overrides are also attributed correctly. Its focused search field filters names, labels, descriptions, and extension names case-insensitively as the user types. Up and Down move through results; Enter or a mouse click opens the selected tool's full description. Escape returns from details or closes the browser without changing the prompt. Dedicated messages cover sessions with no tools and searches with no matches.

This stays within Tau's frontend boundary: portable `AgentTool` data remains in `tau_agent`, the command request is represented by `CommandResult`, and Textual rendering remains in `tau_coding.tui`.

Validate manually by starting `tau`, entering `/tools`, searching for a known tool such as `bash`, navigating with arrow keys, and closing with Escape. `tools` is a reserved prompt-template name, matching the existing `/prompts` collision behavior, so a user `tools.md` is ignored with a diagnostic and cannot shadow `/tools`. Automated coverage is in `tests/test_commands.py`, `tests/test_coding_session.py`, and `tests/test_tui_app.py`.
