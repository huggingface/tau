# Herdr/Textual mouse compatibility

## Problem

Tau's Textual TUI received unusable mouse coordinates on Herdr 0.9.0. In a
161-by-53-cell pane, a click near the bottom-right arrived at Textual near
`(11, 1)`. Clicking, hovering, selecting text, following links, and scrolling
therefore targeted the top-left of the application.

A standalone Textual probe reproduced the coordinate collapse. Herdr forwarded
mouse events, and the pane size reported by `stty` remained correct, ruling out
Tau widget dispatch and PTY sizing.

## Cause

Textual's in-band resize negotiation enables SGR pixel mouse mode. Affected
Herdr versions report that mode as enabled but can forward cell coordinates
unless the pane has graphics demand. Textual correctly interprets the negotiated
input as pixels and converts it to cells a second time.

Opening and closing any affected Textual app can mask the problem for a later
run because its terminal-mode cleanup changes the negotiation state. This is why
Toad appeared to repair Tau despite having no custom outer-terminal mouse setup.

## Tau workaround

Before starting the TUI under `HERDR_ENV=1`, Tau defaults
`TEXTUAL_SMOOTH_SCROLL` to `0` and updates Textual's loaded setting. In Textual
8.2.8 this disables the in-band resize/pixel-mouse path, retaining SIGWINCH-based
resizing and cell-coordinate mouse events. Tau does not need sub-cell pointer
precision.

The workaround:

- applies only inside Herdr;
- preserves an explicit `TEXTUAL_SMOOTH_SCROLL` value;
- leaves print mode and the reusable `tau_agent` harness unchanged;
- remains compatible with a future Herdr fix because cell mouse input is still
  valid.

## Validation

Automated tests cover the Herdr default, explicit user override, and non-Herdr
behavior. Manual validation uses a fresh Herdr 0.9.0 pane: start Tau once, then
verify prompt focus, transcript scrolling, text selection, link hover/click, and
mouse movement without first priming the pane with another Textual application.
