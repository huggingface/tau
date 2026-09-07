# TUI file drop (drag-and-drop paths into the prompt)

Tracks issue #170: users can drag files onto the terminal window and Tau inserts
the filesystem paths into the prompt.

## How it works

Terminals do not send OS drag-and-drop as a dedicated event. When a file is
dropped onto the terminal window, the emulator *types* the file's path into the
running program. Because Textual enables bracketed-paste mode, that typed path
arrives as a single `textual.events.Paste` message — so file-drop support is
implemented as a special case of paste handling.

The exact dropped text varies by terminal:

- most shell-escape paths (`/tmp/my\ file.png`) and space-separate multiple
  files;
- some quote paths that contain spaces (`"/tmp/my file.png"`);
- some VTE-based terminals emit `file://` URIs;
- a few emit the bare path, even with spaces.

## Pieces

- `src/tau_coding/tui/file_drop.py` — `normalize_dropped_paths(text)` decides
  whether pasted text is *only* one or more absolute paths that exist on disk
  (parsing escaped/quoted forms with `shlex`, and converting `file://` URIs).
  If so it returns clean, space-separated paths, double-quoting any path with
  whitespace. Anything else returns `None`. Requiring absolute, existing paths
  keeps false positives (ordinary pasted prose) effectively impossible.
- `PromptInput.on_paste` in `src/tau_coding/tui/app.py` — tries
  `normalize_dropped_paths` first; on a match it inserts the normalized text at
  the cursor with smart spacing (a separating space is added before/after only
  when the neighboring character is not whitespace). Otherwise paste handling
  falls through to the existing large-paste placeholder logic untouched. The
  rules live in `PromptInput.handle_pasted_text(text)` so other entry points can
  reuse them; `PromptInput.insert_pasted_text(text)` adds verbatim insertion for
  callers that bypass Textual's default paste handler.
- `TauTuiApp.on_paste` in `src/tau_coding/tui/app.py` — app-level fallback for
  drops that arrive while no widget holds focus (see below).

## Drops that arrive while the terminal is unfocused

Some drag sources never hand keyboard focus back to the terminal before the drop
lands — most visibly the **macOS Dock** (e.g. the Downloads stack). Those drops
silently disappeared while Finder drops worked, because of this chain in Textual:

1. Textual enables focus reporting (`CSI ? 1004 h`), so the terminal reports
   `FocusOut` when the drag source takes over.
2. `App._watch_app_focus` calls `screen.set_focus(None)` on blur, so the prompt
   is no longer the focused widget.
3. `App.on_event` forwards `events.Paste` to `self.focused`, or to the screen
   when nothing is focused; `Screen` has no paste handler, so the text is lost.
4. Textual restores `app_focus` on `Key`/`MouseDown` only, never on `Paste`, so
   the blur is still in effect when the dropped path arrives.

Finder drags activate the terminal (`FocusIn`) before/with the drop, which is why
they always worked: the prompt still had focus.

The fix keeps focus reporting enabled — Tau uses `AppBlur`/`AppFocus` for turn
notifications — and closes the dispatch hole instead: `TauTuiApp.on_paste`
receives the paste as it bubbles up from the screen and, **only when
`self.focused is None`**, routes the text to `#prompt` via
`insert_pasted_text`. The focus guard prevents double insertion, since
`TextArea._on_paste` does not stop propagation. `query_one("#prompt", ...)` is
scoped to the active screen, so modal screens keep their own paste handling.
Clipboard pastes always require terminal focus, so this path only ever rescues
drops.

Diagnosing this needed raw-input tracing inside the real app: wrapping
`XTermParser.feed` (raw stdin bytes) and `LinuxDriver.process_message` (emitted
messages), plus selectively suppressing individual mode-setting sequences
(`?1004h`, `?1003h`, kitty keyboard flags, `?2048`, `?7l`) to bisect which one
changed the behavior. Suppressing `?1004h` made Dock drops work, which pinned the
cause to focus-driven dispatch rather than to the byte stream or path parsing.

## Tests

`tests/test_tui_file_drop.py` covers the detection/normalization matrix
(escaped, quoted, bare, URI, multi-file, newline-separated, directories, and
non-drop text) and the prompt insertion behavior (empty prompt, existing text,
mid-text cursor, default paste passthrough). `TestUnfocusedDropRouting` runs a
real `TauTuiApp` under `run_test()`, posts `events.AppBlur()` followed by
`events.Paste(path)`, and asserts the path reaches the prompt; a companion test
posts a paste while the prompt *is* focused to assert the app-level fallback does
not insert the path twice.

## Manual validation

Run `tau` in a terminal, drag a file from Finder/your file manager onto the
window, and confirm the prompt shows the file's path (quoted if it contains
spaces), preserving anything already typed. On macOS, also drag an item out of
the Dock's Downloads stack — that source keeps the terminal unfocused and
exercises the `TauTuiApp.on_paste` fallback.
