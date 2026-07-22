# Forward-compatible user configuration

## What changed

Tau now ignores fields it does not recognize in user-level runtime settings:

- top-level settings and nested keybinding actions in `~/.tau/tui.json`;
- fields in `~/.tau/settings.json`;
- top-level settings and nested provider-preference fields in
  `~/.tau/providers.json`.

Recognized fields are still validated exactly as before. Invalid known values,
malformed objects, empty known key strings, conflicting shell-prefix aliases,
and duplicate assignments among known TUI actions remain configuration errors.

## Why

These JSON files are user-level state and can be shared by multiple Tau
installations. A newer Tau release may add a setting and write it to one of
those files. Previously, starting an older release could then fail before
opening the TUI with an error such as:

```text
Unknown TUI settings field: turn_notification
```

Each parser now consumes the fields its version understands and ignores future
metadata. This follows the compatibility principle already used by session
index records (`extra="ignore"`), credential object fields, unknown top-level
provider settings, and orphaned provider preferences.

Unknown fields are not part of the parsed settings models and therefore are not
emitted if that older version later rewrites a settings file. This change
protects startup compatibility; it does not make older releases understand or
preserve newer features.

## Deliberately strict formats

This policy does not apply to every persisted file:

- `~/.tau/catalog.toml` remains schema-versioned and strict because it controls
  provider endpoints, authentication routing, model capabilities, and pricing;
- custom theme JSON remains strict so misspelled color and role names produce a
  useful diagnostic rather than a subtly broken theme;
- session transcript entries remain strict to protect conversation integrity.

Credential objects and session index metadata were already tolerant of extra
fields, so they need no parser change.

## How to test

1. Add a made-up field to each JSON settings file, such as
   `"future_setting": true`.
2. Add a made-up action inside `tui.json`'s `keybindings` and a made-up option
   inside one `providers.json` provider preference.
3. Start Tau and confirm the TUI opens while recognized settings still apply.
4. Give recognized settings invalid values and confirm Tau still reports those
   configuration errors.

Automated tests cover ignored fields in all three parsers alongside existing
strict validation tests for recognized fields.
