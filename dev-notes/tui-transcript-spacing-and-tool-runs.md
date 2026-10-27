# Transcript spacing and narration-free tool runs

The TUI drew one block per tool call with generous margins, so an
exploration-heavy turn read as mostly whitespace, and providers that serialize
calls one at a time produced a tall stack of one-line blocks.

This note records the two changes that fixed it: a tighter, uniform block rhythm
and run continuation across narration-free responses.

## What changed

### One blank line between blocks

`TranscriptMessageWidget` and `StreamingTranscriptMessageWidget` carried a
two-row bottom margin, which Textual collapses with the next block's one-row top
margin into two blank rows. The margin is now one row, so blocks are separated by
exactly one blank line.

Markdown bodies also gave their final paragraph `margin-bottom: 1` even when
nothing followed it, so borderless rows (assistant, thinking, and the collapsed
`Thinking…` placeholder) reserved a row that plain-body rows never did. That
margin is now zeroed on the final paragraph only:

```css
TranscriptMessageWidget > .transcript-markdown-body > MarkdownParagraph:last-child {
    margin: 0;
}
```

`:last-child` rather than `:last-of-type`: a block that ends with a fence, table,
or heading must keep the space before it. Paragraphs 1..n-1 keep their margin, so
the paragraph separation restored in #742 is unchanged.

### Whitespace-only blocks are invisible

Providers routinely emit a bare `"\n\n"` text block beside a tool call, and some
emit whitespace-only thinking with it. Projecting one created a zero-height item
that still contributed block margins, doubling the gap between consecutive tool
rows. One predicate decides visibility for both content types:

- `has_visible_assistant_text(text)` — the string primitive,
- `assistant_block_is_visible(block)` — used by `TuiState.add_assistant_message`
  (projection), the streaming `MessageEnd` handler (canonical-block filter), and
  `_is_tool_only_message` (run classification), so the three cannot disagree.

`TuiAgentAdapter._flush` drops a whitespace-only assistant buffer for the same
reason.

### Tool runs continue across narration-free responses

A model that calls one tool per response produced one item per call, so a
sequence of shell, read, or edit steps was drawn as a stack of blocks. Only calls
from a single assistant message batched, plus a narrow edit/write-only
continuation rule that required an assistant message containing nothing but the
calls (whitespace-only `"\n\n"` blocks defeated it on real provider traffic).

`TuiState.add_tool_call` now extends the previous tool item when:

- the current message is tool-only (`_is_tool_only_message`: tool calls plus only
  invisible text/thinking),
- the previous item came from a tool-only message too (`ChatItem.allows_tool_continuation`),
- the previous item is settled and successful (an error keeps its own block),
- the incoming call is the same tool as the previous item's last row,
- neither side has a custom call renderer or a `render_result` card, and
- the run is below `TOOL_RUN_ROW_LIMIT` (8) rows.

`_tool_run_tail` reports an item's visible row count and its last row's identity,
counting `grouped_tool_calls` members as well as `tool_batch_items` rows: a file
group is one collapsed row but must still consume the run's budget.

Anything the transcript would render between the rows ends the run: visible
prose, thinking, another tool, a failure, or a new user prompt. Merging across
those would reorder the block, which the adapter deliberately avoids ("persisted
block boundaries win").

`ChatItem.allows_tool_continuation` is set from the message in both
`add_assistant_message` (restored history) and the event adapter (live stream),
so both paths group identically. This replaced the separate mutation-only
continuation machinery, which had none of these guards.

### Read groups show their results

Reads were originally excluded from continuation because a grouped read run
rendered as a path list whose member results were never shown — collapsing two
reads would have hidden the contents the user opened the transcript for. Reads
are now resultful groups: expanding a run prints each member's previewed result
(eight lines, with the usual `Preview only` note), like bash and edit rows.
`RESULTFUL_FILE_GROUP_NAMES` therefore means "a group whose members' results
render". The collapsed path list also keeps each read's line range, so the bullet
reads `state.py:30-159` rather than a bare path.

### Tool-only messages do not remount the window

A tool-only message streams a blank text buffer that never becomes a canonical
item. The `MessageEnd` handler used to push that case through
`finish_structured_assistant_message`, which sets `_window_end` to the newest item
and can redraw the mounted window forward — pulling a user who had scrolled back
to earlier output. `TranscriptView.discard_provisional_assistant_message` removes
only the message's provisional widgets, leaving the window and scroll position
alone.

## Why it works this way

Grouping stays display-only and sequence-derived. Nothing reaches the provider
payload, tool execution, or session JSONL, and restored history rebuilds the same
groups because eligibility comes from the canonical assistant block sequence
rather than transient display adjacency.

Keeping the run rule in `TuiState` (not the adapter) is what makes that parity
possible: the adapter only records which calls came from a tool-only message, the
same way it records batch ids.

## Tests

- `tests/test_tui_app.py`: paragraph margin and block-gap regressions, whitespace
  block projection (restored and streamed), run continuation (bash/edit/read),
  boundaries (prose, thinking, failure), the row cap for batches and file groups,
  `Ctrl+O` read previews, and window/scroll stability for a paged-back transcript.
- `tests/test_tui_adapter.py`: restored grouping for reads/edits/writes, tool-only
  continuations, text boundaries, and path lists with line ranges.

```bash
uv run pytest tests/test_tui_app.py tests/test_tui_adapter.py
```
