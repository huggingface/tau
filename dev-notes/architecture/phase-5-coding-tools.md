---
title: "Phase 5: Built-in Coding Tools"
---

Phase 5 adds Tau's first built-in coding tools in `tau_coding`.

The tools live in:

```text
src/tau_coding/tools.py
```

## What was added

Tau now provides factory functions for these initial tools:

- `create_read_tool()`
- `create_write_tool()`
- `create_edit_tool()`
- `create_bash_tool()`
- `create_coding_tools()` for the default tool set

Each factory has a Pi-like `ToolDefinition` counterpart with a description, prompt snippet, prompt guidelines, JSON schema, and executor. The public factory returns a provider-neutral `AgentTool` from `tau_agent.tools`.

## Why the tools live in `tau_coding`

`tau_agent` owns the portable agent brain: messages, events, tools as an abstraction, the loop, and the harness.

Filesystem and shell access are coding-agent environment features, so the concrete implementations live in `tau_coding`.

```text
tau_agent:
  knows how to execute an AgentTool

tau_coding:
  provides read/write/edit/bash tools for local coding work
```

This keeps the core loop reusable and independent of local machine behavior.

## Tool behavior

### `read`

Reads a UTF-8 text file.

Arguments:

- `path`: file path
- `offset`: optional 1-indexed starting line; `0` is accepted as an alias for the start of the file
- `limit`: optional number of lines to return

Large output is truncated with Pi-style truncation metadata and continuation hints. Supported image files (`jpg`, `png`, `gif`, `webp`) are detected and returned as base64 metadata for later UI/provider integration.

### `write`

Writes a complete UTF-8 text file and creates parent directories when needed. Writes are serialized per path with a file mutation queue so concurrent mutations to the same file do not interleave.

Arguments:

- `path`: file path
- `content`: complete file content

### `edit`

Applies exact text replacements to a UTF-8 file.

Arguments:

- `path`: file path
- `edits`: array of `{ oldText, newText }` objects

Important Pi-inspired behavior:

- every `oldText` must match exactly once
- edits must not overlap
- validation happens before writing
- if any edit fails, the file is left unchanged
- UTF-8 BOMs are preserved
- existing line endings are preserved
- results include diff, unified patch, and first-changed-line metadata
- legacy `oldText`/`newText` arguments and JSON-string `edits` are normalized

### `bash`

Executes a shell command in the configured working directory.

Arguments:

- `command`: command string
- `timeout`: optional timeout in seconds, with no default timeout

The result includes combined stdout/stderr output, exit code, timeout state, duration, truncation metadata, and a full-output temp file path when output is truncated.

## How to use the tools

```python
from tau_coding import create_coding_tools

tools = create_coding_tools(cwd="/path/to/project")
```

Pass those tools into `AgentHarnessConfig` or directly into `run_agent_loop()`.

## Tests

The phase is covered by:

```text
tests/test_coding_tools.py
```

The tests verify:

- default tool registration
- file reading with line slicing
- parent directory creation for writes
- multi-edit exact replacement
- rollback on failed edits
- unique-match validation
- bash stdout capture
- bash timeout reporting

## Next phase

The next phase can wire these tools into a non-interactive print-mode CLI so a user can run Tau against a real project from the terminal.
