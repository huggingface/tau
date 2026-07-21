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
  falls through to the existing large-paste placeholder logic untouched.

## Tests

`tests/test_tui_file_drop.py` covers the detection/normalization matrix
(escaped, quoted, bare, URI, multi-file, newline-separated, directories, and
non-drop text) and the prompt insertion behavior (empty prompt, existing text,
mid-text cursor, default paste passthrough).

## Manual validation

Run `tau` in a terminal, drag a file from Finder/your file manager onto the
window, and confirm the prompt shows the file's path (quoted if it contains
spaces), preserving anything already typed.
