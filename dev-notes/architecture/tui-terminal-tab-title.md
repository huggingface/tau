# TUI terminal tab titles

Issue: #260

## What changed

The Textual TUI now updates the terminal emulator's window/tab title with the
active Tau session name and running state:

- idle unnamed session: `τ`
- idle named session: `τ | <session name>`
- running session: an animated Braille spinner prefix plus the idle title

On TUI shutdown Tau writes a neutral `τ` title so the terminal is not left with
a stale running frame.

## Why it lives in `tau_coding`

Terminal title updates are a frontend concern. The implementation uses state the
TUI already owns:

- `TuiState.running`, populated from `AgentStartEvent`, `AgentEndEvent`, and
  non-recoverable `ErrorEvent` by the adapter;
- `CodingSession.session_title`, which is updated by `/name` and automatic
  session naming.

No terminal or Textual dependencies were added to `tau_agent` or `tau_ai`.

## Mechanism

`src/tau_coding/tui/terminal_title.py` emits OSC 0 sequences:

```text
ESC ] 0 ; <title> BEL
```

OSC 0 is broadly supported by common terminal emulators and sets the terminal
window/tab title. The `TerminalTitleController` writes only when the computed
title changes, so idle refreshes do not spam stdout. While running, the existing
activity timer drives both the in-app prompt animation and the tab-title spinner.

## Capability detection and safety

Title writing is enabled only when stdout is a TTY, `TERM` is not `dumb`, and CI
is not detected. Users can opt out with `TAU_TERMINAL_TITLE=0`; CI/no-TTY cases
can opt in explicitly with `TAU_TERMINAL_TITLE=1` only where supported by the
helper's rules. Title writes are best-effort: if the terminal stream raises while
Tau is writing an OSC sequence, Tau disables further title writes for that TUI
process instead of interrupting the session.

Session names are sanitized before they enter an OSC payload: C0/C1 control
characters, including BEL and ESC, are stripped and the title is capped to 120
characters.

## Testing and manual verification

Automated tests cover title construction, sanitization, capability detection,
deduplicated writes, and TUI running/name/idle transitions.

Manual verification:

1. Open `tau` in a real terminal tab.
2. Confirm an unnamed idle TUI shows `τ` in the tab title.
3. Run `/name build notes` and confirm the tab changes to `τ | build notes`.
4. Submit a prompt that runs long enough to observe the animated spinner prefix.
5. Cancel or let the run finish; confirm the spinner stops.
6. Quit the TUI and confirm the tab is reset to `τ`.
7. Repeat once with `TAU_TERMINAL_TITLE=0 tau` and confirm Tau does not manage
   the tab title.
