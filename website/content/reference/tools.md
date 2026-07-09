---
title: Built-in tools
description: The read, write, edit, and bash tools the agent uses to work in your project.
---

Tools are the actions the agent can take in your working directory. The model
decides when to call them; Tau executes them and streams the results back. Tau
ships four built-in coding tools: `read`, `write`, `edit`, and `bash`.

All paths are resolved against the session's working directory (`--cwd`, or the
directory you launched Tau from).

{{% note %}}
This page documents tool *behavior* — what the model can do on your machine. To
build a frontend or register your own tools, see
[Building a custom frontend]({{< relref "../internals/custom-frontend.md" >}}).
{{% /note %}}

## `read`

Reads a file from disk.

```json
{ "path": "README.md", "offset": 1, "limit": 40 }
```

| Argument | Required | Type | Description |
| --- | --- | --- | --- |
| `path` | yes | string | File to read (relative to `cwd`). |
| `offset` | no | integer | 1-indexed start line (`0` = start of file). |
| `limit` | no | integer | Maximum number of lines to return. |

For text files, `read` returns UTF-8 content, applies `offset`/`limit`, and
truncates to at most 2,000 lines or 50 KB (whichever comes first), appending a
hint like `[42 more lines in file. Use offset=101 to continue.]`. Supported
images (JPEG, PNG, GIF, WebP) are returned as base64 with metadata.

Fails when `path` is missing/invalid, the file doesn't exist, the path is a
directory, `offset` is past the end, or the file is neither UTF-8 text nor a
supported image.

## `write`

Creates or overwrites a complete UTF-8 text file.

```json
{ "path": "src/example.py", "content": "print('hello')\n" }
```

| Argument | Required | Type | Description |
| --- | --- | --- | --- |
| `path` | yes | string | File to write (relative to `cwd`). |
| `content` | yes | string | Complete file contents. |

Creates missing parent directories and overwrites any existing file. Writes to
the same path are serialized within a process, so concurrent `write`/`edit` calls
on one file don't interleave.

## `edit`

Applies exact text replacements to one file.

```json
{
  "path": "src/example.py",
  "edits": [
    { "oldText": "print('hello')", "newText": "print('hello, Tau')" }
  ]
}
```

| Argument | Required | Type | Description |
| --- | --- | --- | --- |
| `path` | yes | string | File to edit (relative to `cwd`). |
| `edits` | yes | array | One or more `{oldText, newText}` replacements. |

Each `oldText` must be non-empty, match exactly (whitespace included), appear
**exactly once**, and not overlap another edit. All edits validate before
anything is written — if any fails, the file is left unchanged. Line endings are
normalized for matching and the original dominant ending is restored. Successful
results include a diff, a unified patch, and the first changed line number.

## `bash`

Runs a shell command in the working directory.

```json
{ "command": "pytest -q", "timeout": 30 }
```

| Argument | Required | Type | Description |
| --- | --- | --- | --- |
| `command` | yes | string | Shell command to run. |
| `timeout` | no | number | Max runtime in seconds (> 0). No default. |

Combines stdout and stderr, succeeds on exit code `0`, and returns the **tail**
of large output (truncated to 2,000 lines / 50 KB; the full output is written to
a temp `.log` file whose path is included in the result). On POSIX, a timeout
kills the whole process group.

## Choosing the right tool

- **`read`** — inspect files (instead of `cat`/`sed`).
- **`write`** — new files or complete rewrites.
- **`edit`** — precise changes to an existing file.
- **`bash`** — tests, linters, searches, project inspection.
