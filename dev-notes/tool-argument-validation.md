# Tool-call arguments checked against the schema

## What

Before a tool runs, `tau_agent` checks the call's arguments against the JSON
schema the tool declares in `AgentTool.parameters`. If they do not fit, the tool
is not run. The model gets one tool error that lists every problem, each with
its path:

```text
Invalid arguments for read: missing required "path"; "limit" must be integer,
got string; "mode" must be one of "text", "bytes", got "lines"; unknown
argument "colour"; missing required "ranges[1].start". Send the call again
with these fixed.
```

(The error is one line. It is wrapped here.)

- The checker is `src/tau_agent/tool_arguments.py`:
  `argument_problems(schema, arguments)` returns the list of problems, and
  `invalid_arguments_message` builds the error.
- `_execute_tool_call` in `src/tau_agent/loop.py` runs it after the tool is
  found and before `_run_tool`. A refused call ends like any failed call: a
  `ToolExecutionEndEvent` and a `ToolResultMessage`, both with `is_error`.
- Nothing is coerced: `"10"` for an integer is refused, not turned into `10`.

## Preparing arguments

A tool can set `AgentTool.prepare_arguments` to rewrite a call's arguments into
the shape its schema declares. The loop runs it first, checks what it returns,
and passes that to the tool. The model's original arguments stay on the
`ToolCall` and in the events. A preparer that raises refuses the call with its
message.

The edit tool uses this for two forms models send instead of `edits`: a single
top-level `oldText`/`newText` pair, and `edits` as a JSON string.

## What the checker covers

It covers the JSON Schema keywords Tau's tools use:

- `type`: one name or a list of names. The names are `string`, `integer`,
  `number`, `boolean`, `array`, `object` and `null`. `true` is not an integer or
  a number. `10.0` is an integer, as in JSON Schema.
- `required`: each missing key is named.
- `properties`: each declared key is checked against its own schema.
- `additionalProperties`: `false` refuses each undeclared key. A schema there
  checks each undeclared key against it.
- `enum`: the value must equal one of the options. `true` does not equal `1`.
- `items`: one schema, checked against each element. The list form is ignored.

Any other keyword (`anyOf`, `oneOf`, `$ref`, `pattern`, `minimum`, `minItems`,
`format` and the rest) is ignored, so a schema that uses one is only checked for
the keywords above. An unknown `type` name is ignored too.

An optional argument sent as `null` counts as not sent: models often fill unused
optional arguments with `null`, and tools read a missing argument and `null` the
same way. A required argument sent as `null` is checked like any other value.

A wrong type is reported once, and the value under it is not checked further. A
value outside an `enum` is reported the same way.

Paths are written as `limit`, `ranges[1].start` or `options.mode`.

## What is not checked

- A tool with no schema. An empty `parameters` mapping has no keywords, so
  nothing is checked. `{"type": "object"}` only checks that the arguments are an
  object, which they always are.
- A call whose arguments did not parse as JSON. A provider passes those on as
  `{"_raw_arguments": text}`, and the tool answers them itself
  (`tau_coding/tools.py` reports the cut-off). Checked against the schema, every
  required key would look missing, so the loop skips them.
- Arguments that an extension's `tool_call` hook returns. Extension hooks run
  inside the tool's executor (`tau_coding/extensions/runtime.py`), after the
  loop's check.

## Order

1. `before_tool_call` may block the call.
2. An unknown tool name is refused.
3. The tool's `prepare_arguments`, if any, rewrites the arguments.
4. The arguments are checked against the schema.
5. The tool runs. Extension `tool_call` hooks run here.

## Required means the tool cannot do without it

A call that lacks a required key is refused and the tool never runs. A key the
tool can do without, or that it handles itself when it is missing, should not be
listed as required. Bash's `description` only labels the call in the UI, so it
is optional.

## Why

Each tool parses its own arguments and stops at the first problem, so a small
model that sends a bad call learns one problem per refused call and spends a
turn on each. A single error that names every problem lets it fix the call in
one retry. The check is in `tau_agent`, so it holds for every tool and every
frontend, and it has no CLI or UI parts.

The subset above is about 130 lines. `jsonschema` would cover every keyword, but
it would be a new dependency of the core package.

## Tests

`tests/test_tool_arguments.py` uses a fake provider and a fake `read` tool:

- a missing required key is refused, and the tool does not run
- one error lists five problems: missing, wrong type, enum, unknown key, and a
  nested missing key
- a wrong type (a boolean for an integer, a string for an array) and a value
  outside an enum are each named
- valid arguments reach the tool unchanged (`10.0` stays a float)
- an optional argument sent as `null` passes; a required one is refused
- a preparer's result is what gets checked and what the tool receives, and a
  preparer that raises refuses the call
- a tool with an empty schema is not checked
- `_raw_arguments` is left to the tool
- an `additionalProperties` schema and a list of types are checked

`tests/test_coding_tools.py` checks that the edit tool's preparer turns both
legacy forms into arguments its schema accepts.

Run with
`uv run pytest tests/test_tool_arguments.py tests/test_coding_tools.py`.
