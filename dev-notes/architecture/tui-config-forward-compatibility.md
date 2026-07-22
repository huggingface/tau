# Forward-compatible TUI configuration

## What changed

Tau now ignores unrecognized top-level settings and unrecognized nested
keybinding actions in `~/.tau/tui.json`.

Recognized fields are still validated exactly as before. Invalid known values,
non-object keybinding data, empty known key strings, and duplicate assignments
among known actions remain configuration errors.

## Why

`tui.json` is user-level state and can be shared by multiple Tau installations.
A newer Tau release may add a setting and write it to that file. Previously,
starting an older Tau release then failed before opening the TUI with an error
such as:

```text
Unknown TUI settings field: turn_notification
```

Ignoring fields that a particular version cannot use makes upgrades and
downgrades safe. This follows the same compatibility principle used by session
index metadata: consume the fields this version understands without letting
future metadata block startup.

Unknown fields are not part of the parsed `TuiSettings` model and therefore are
not emitted if that older version later rewrites the settings file. This change
protects startup compatibility; it does not make older releases understand newer
features.

## How to test

1. Add a made-up top-level field to `~/.tau/tui.json`, for example
   `"future_setting": true`.
2. Add a made-up action inside `keybindings`, for example
   `"future_action": "ctrl+g"`.
3. Start Tau and confirm the TUI opens while recognized settings still apply.
4. Give a recognized setting an invalid value and confirm Tau still reports the
   configuration error.

Automated tests cover ignored unknown settings and actions alongside the existing
strict validation tests for known fields.
