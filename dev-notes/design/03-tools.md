---
title: "03 — Tools"
---

Tools let the assistant inspect and modify the user's environment through structured calls.

This page is the user-facing source of truth for built-in tool behavior. The
Python factory functions also have docstrings for contributors, but generated
API reference docs are intentionally deferred; see
[ADR 0002](./adr/0002-keep-tool-docs-hand-written.md).

Tau separates the idea of a tool from any specific frontend:

- `tau_agent` defines provider-neutral tool types and executes requested tool calls.
- `tau_coding` provides concrete local coding tools for files and shell commands.
- CLIs, TUIs, and other frontends decide which tools to expose to a session.

## Core model

An `AgentTool` has:

- a `name`, such as `read` or `bash`
- a human-readable `description`
- an `input_schema` describing accepted JSON arguments
- an async `executor`
- optional prompt metadata used by clients that assemble tool guidance

A tool returns an `AgentToolResult` with:

- `ok`: whether the tool completed successfully
- `content`: text that can be sent back to the model
- optional `data`: structured metadata for UIs, logs, or future integrations
- optional `error`: a machine-readable or user-readable error message

The agent loop executes tool calls and converts results into `ToolResultMessage` entries in the transcript.

## Built-in coding tools

`tau_coding` provides four built-in local coding tools:

- `read`
- `write`
- `edit`
- `bash`

Use `create_coding_tools()` to register all of them:

```python
from tau_coding import create_coding_tools

tools = create_coding_tools(cwd="/path/to/project")
```

Or create individual tools:

```python
from tau_coding import (
    create_bash_tool,
    create_edit_tool,
    create_read_tool,
    create_write_tool,
)
```

Relative paths passed to the tools are resolved against `cwd`. If `cwd` is omitted, Tau uses the process current working directory at the time the factory is called.

## `read`

Reads a file from disk.

Factory functions:

- `create_read_tool_definition()`
- `create_read_tool()`

### Arguments

```json
{
  "path": "README.md",
  "offset": 1,
  "limit": 40
}
```

| Argument | Required | Type | Description |
| --- | --- | --- | --- |
| `path` | yes | string | File path to read. Relative paths are resolved against `cwd`. |
| `offset` | no | integer | 1-indexed line number to start reading from. `0` is accepted as an alias for the start of the file. |
| `limit` | no | integer | Maximum number of lines to return. |

`offset` must be a non-negative integer when supplied. `limit` must be a positive integer.

### Text behavior

For text files, `read`:

1. opens the file as UTF-8 text
2. applies `offset` and `limit` if provided
3. truncates large output to at most 2,000 lines or 50 KB, whichever limit is reached first
4. appends a continuation hint when more lines remain

Example continuation hint:

```text
[42 more lines in file. Use offset=101 to continue.]
```

### Image behavior

Supported image files are detected by MIME type:

- JPEG
- PNG
- GIF
- WebP

For supported images, `read` returns a short text message and stores image metadata in `data`, including:

- resolved path
- MIME type
- byte size
- base64-encoded image bytes

### Errors

`read` fails when:

- `path` is missing or is not a string
- `offset` is negative, or `limit` is not a positive integer
- the file does not exist
- the path is a directory
- `offset` is beyond the end of the file
- the file cannot be decoded as UTF-8 text and is not a supported image

## `write`

Creates or overwrites a complete UTF-8 text file.

Factory functions:

- `create_write_tool_definition()`
- `create_write_tool()`

### Arguments

```json
{
  "path": "src/example.py",
  "content": "print('hello')\n"
}
```

| Argument | Required | Type | Description |
| --- | --- | --- | --- |
| `path` | yes | string | File path to write. Relative paths are resolved against `cwd`. |
| `content` | yes | string | Complete file contents to write. |

### Behavior

`write`:

1. resolves the target path
2. creates missing parent directories
3. writes `content` using UTF-8 encoding
4. overwrites any existing file at that path

Writes are serialized per resolved path inside the current process. That means concurrent `write` or `edit` operations targeting the same file do not interleave.

### Result metadata

Successful results include:

- resolved path
- number of characters written

### Errors

`write` fails when:

- `path` is missing or is not a string
- `content` is missing or is not a string
- the filesystem rejects the write operation

## `edit`

Applies exact text replacements to one UTF-8 file.

Factory functions:

- `create_edit_tool_definition()`
- `create_edit_tool()`

### Arguments

```json
{
  "path": "src/example.py",
  "edits": [
    {
      "oldText": "print('hello')",
      "newText": "print('hello, Tau')"
    }
  ]
}
```

| Argument | Required | Type | Description |
| --- | --- | --- | --- |
| `path` | yes | string | File path to edit. Relative paths are resolved against `cwd`. |
| `edits` | yes | array | One or more replacement objects. |
| `edits[].oldText` | yes | string | Exact text to replace. Must be non-empty and unique in the original file. |
| `edits[].newText` | yes | string | Replacement text. |

### Matching rules

Every `oldText` must:

- be non-empty
- match exactly, including whitespace and newlines
- appear exactly once in the original file
- not overlap another edit's matched range

All `oldText` entries are matched against the original file content, not against the result of earlier edits.

### Rollback behavior

`edit` validates every replacement before writing. If any edit fails validation, the file is left unchanged.

### Line endings and BOMs

For matching, Tau normalizes file content and edit text to LF line endings. After applying edits, Tau restores the file's original dominant line ending. UTF-8 byte-order marks are preserved.

### Result metadata

Successful results include:

- resolved path
- number of edits applied
- an `ndiff`-style diff
- a unified patch
- first changed line number

### Errors

`edit` fails when:

- `path` is missing or is not a string
- the file does not exist
- the path is a directory
- `edits` is missing, empty, or malformed
- any `oldText` is empty
- any `oldText` is not found
- any `oldText` appears more than once
- edit ranges overlap
- all replacements would leave the file unchanged

## `bash`

Executes a shell command in the configured working directory.

Factory functions:

- `create_bash_tool_definition()`
- `create_bash_tool()`

### Arguments

```json
{
  "command": "pytest -q",
  "timeout": 30
}
```

| Argument | Required | Type | Description |
| --- | --- | --- | --- |
| `command` | yes | string | Shell command to execute. |
| `timeout` | no | number | Maximum runtime in seconds. Must be greater than zero when supplied. |

There is no default timeout. Callers should provide one when they need bounded execution.

### Behavior

`bash`:

1. runs the command with `cwd` as the subprocess working directory
2. combines stdout and stderr into a single output stream
3. decodes output as UTF-8, replacing invalid bytes
4. returns success when the command exits with code `0`
5. returns failure when the command exits non-zero or times out

On POSIX systems, commands are started in a new session. If a timeout occurs, Tau kills the whole process group so child processes from pipelines or compound commands are stopped too. On non-POSIX systems, Tau kills the direct subprocess.

### Output truncation

`bash` returns the tail of large output. Output is truncated to at most 2,000 lines or 50 KB, whichever limit is reached first.

When truncation happens, Tau writes the full command output to a temporary `.log` file and includes that path in the result metadata.

### Result metadata

Results include:

- command string
- exit code
- whether the command timed out
- duration in seconds
- truncation metadata
- full-output temp file path when output was truncated

### Errors

`bash` fails when:

- `command` is missing or is not a string
- `timeout` is not a number greater than zero
- the command exits with a non-zero status
- the command times out
- the subprocess cannot be started

## Choosing the right tool

- Use `read` to inspect file contents instead of shelling out to `cat` or `sed`.
- Use `write` for new files or complete rewrites.
- Use `edit` for precise changes to an existing file.
- Use `bash` for commands such as tests, linters, searches, and project inspection.
