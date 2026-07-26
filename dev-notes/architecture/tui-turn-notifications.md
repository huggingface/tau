# TUI turn notifications

## What changed

Tau's Textual frontend can now request terminal attention after an agent run fully
settles while its terminal surface is unfocused. The `turn_notification` setting
in `~/.tau/tui.json` accepts:

- `"desktop"` (default): write OSC 9 for detected Ghostty, iTerm2, or MinTTY
  sessions, and OSC 99 for detected Kitty sessions;
- `"bell"`: write the standard BEL control character;
- `"off"`: write nothing.

Terminal emulators decide how these sequences appear. For example, a bell may
mark an inactive tab, request application attention, or play a sound according
to terminal settings. Desktop notifications may also use the operating system's
configured notification sound. Tau detects supported desktop protocols from
terminal environment variables: Kitty takes precedence and receives OSC 99;
Ghostty, iTerm2, and MinTTY receive OSC 9. Unknown terminals receive no desktop
sequence rather than an incompatible escape sequence.

## Why it belongs in the TUI

Completion remains a provider-neutral session event. Focus reporting, terminal
control sequences, and user notification preferences are frontend policy, so the
implementation stays under `tau_coding.tui`; neither `tau_agent` nor the coding
session knows about Textual or terminal capabilities.

Textual's `AppBlur` and `AppFocus` events maintain the active-surface state. Tau
notifies on `AgentSettledEvent`, rather than `agent_end` or `turn_end`, because a
retry, automatic compaction, queued steering message, or follow-up may still run
after those lower-level boundaries. This produces one notification when Tau
actually becomes idle.

Writes use `sys.__stdout__`, matching terminal-title updates, and are skipped for
non-TTY streams, `TERM=dumb`, and CI. Payload control bytes are stripped before
building OSC 9 or OSC 99, and write failures disable later attempts instead of
crashing the TUI.

## How to test

Automated coverage verifies configuration parsing, control-sequence generation,
write failure handling, and focused versus unfocused completion behavior.

For manual validation in a terminal with two tabs:

1. Start Tau and submit a prompt that runs for several seconds.
2. Switch to another tab before it completes.
3. Confirm a desktop notification appears on Ghostty/iTerm2/MinTTY (OSC 9) or
   Kitty (OSC 99) when Tau settles.
4. Set `"turn_notification": "bell"` in `~/.tau/tui.json`, repeat, and confirm the
   terminal's configured bell attention appears.
5. Keep Tau focused for a completed prompt and confirm no notification appears.
6. Set the value to `"off"` and confirm inactive completion stays silent.
