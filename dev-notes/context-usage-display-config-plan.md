# Context usage display config plan

## Goal

Add a user-configurable context usage display for Tau's Textual TUI so users can choose whether context usage is shown as token counts, percentage, both, or hidden.

Current compact TUI chrome shows token counts like:

```text
12k/200k context
```

It does not show percentage usage. This plan keeps the feature inside `tau_coding` because context accounting and TUI presentation are coding-app behavior, not reusable agent-harness behavior.

## Current code locations

- `src/tau_coding/context_window.py`
  - Rough token estimation and compaction thresholds.
  - Important items: `ContextUsageEstimate`, `estimate_context_usage`, `estimate_context_tokens`, `auto_compaction_threshold_for_context_window`.

- `src/tau_coding/session.py`
  - `CodingSession` exposes active context information.
  - Important properties: `context_usage`, `context_token_estimate`, `context_window_tokens`, `auto_compact_token_threshold`.

- `src/tau_coding/tui/widgets.py`
  - Renders compact session info and context usage text.
  - Important function: `_context_usage(session)`.

- `src/tau_coding/tui/config.py`
  - Durable TUI settings stored in `~/.tau/tui.json`.
  - Important items: `TuiSettings`, `tui_settings_from_json`, `save_tui_settings`, `load_tui_settings`.

## Proposed setting

Add a durable TUI setting named `context_usage_display`.

Suggested type:

```python
type ContextUsageDisplay = Literal["tokens", "percent", "both", "off"]
```

Suggested default:

```python
context_usage_display: ContextUsageDisplay = "tokens"
```

The default preserves current behavior for existing users.

## Display modes

| Value | Compact TUI output |
| --- | --- |
| `tokens` | `12k/200k context` |
| `percent` | `6% context` |
| `both` | `12k/216k context · 6%` |
| `off` | hides context usage |

Open design point: current token rendering sometimes uses `auto_compact_token_threshold` as the denominator. For percentage, prefer using the full model `context_window_tokens`, because users usually interpret percentage as total context window usage.

## Implementation steps

1. Add percentage helper in `src/tau_coding/context_window.py`.

   Example:

   ```python
   def context_usage_percent(used_tokens: int, context_window_tokens: int) -> int:
       if context_window_tokens <= 0:
           raise ValueError("context_window_tokens must be positive")
       return min(999, round((used_tokens / context_window_tokens) * 100))
   ```

2. Expose percentage from `CodingSession` in `src/tau_coding/session.py`.

   Example:

   ```python
   @property
   def context_usage_percent(self) -> int | None:
       return context_usage_percent(
           self.context_token_estimate,
           self.context_window_tokens,
       )
   ```

3. Add config support in `src/tau_coding/tui/config.py`.

   - Add `ContextUsageDisplay` type.
   - Add `context_usage_display` field to `TuiSettings`.
   - Include it in `TuiSettings.to_json()`.
   - Allow and validate it in `tui_settings_from_json(...)`.

4. Update TUI rendering in `src/tau_coding/tui/widgets.py`.

   - Extend `SessionSummarySource` with `context_usage_percent`.
   - Pass the setting into `render_compact_session_info(...)`.
   - Update `_context_usage(...)` to render based on `context_usage_display`.

5. Update `src/tau_coding/tui/app.py`.

   - When refreshing chrome, pass `self.tui_settings.context_usage_display` into compact session info rendering.
   - Keep sidebar behavior unchanged unless we intentionally decide to show context usage there too.

6. Optional: update `/session` in `src/tau_coding/commands.py`.

   Since `/session` is not TUI chrome, it can always include the percentage as additional information, or it can stay token-only for this first change.

## Tests to add or update

- `tests/test_context_window.py`
  - Percentage helper computes deterministic values.
  - Invalid or zero context window raises `ValueError`.

- `tests/test_tui_config.py`
  - Default is `tokens`.
  - `context_usage_display` round-trips through JSON.
  - Invalid values raise `TuiConfigError`.

- `tests/test_tui_app.py`
  - Existing compact session info still renders token mode by default.
  - Percent mode renders `6% context` for the fake session.
  - Both mode renders tokens and percent.
  - Off mode hides context usage.

## Compatibility

This should be backwards-compatible because:

- the default remains token display;
- missing `context_usage_display` in existing `~/.tau/tui.json` files falls back to `tokens`;
- the reusable `tau_agent` package is untouched.

## Suggested branch

```bash
git checkout -b context-usage-display-config
```

Suggested focused checks:

```bash
uv run pytest tests/test_context_window.py tests/test_tui_config.py tests/test_tui_app.py
uv run ruff check .
```

## Task breakdown

### Task 1 — Add context percentage accounting

**Goal:** Provide a deterministic percentage helper in the context accounting layer.

Files:

- `src/tau_coding/context_window.py`
- `tests/test_context_window.py`

Acceptance criteria:

- `context_usage_percent(12_034, 216_384)` returns `6`.
- `context_usage_percent(..., 0)` raises `ValueError`.
- The helper is exported from `tau_coding.__init__` if the surrounding context helpers are exported there.
- Focused test command passes:

  ```bash
  uv run pytest tests/test_context_window.py
  ```

### Task 2 — Expose percentage from `CodingSession`

**Goal:** Make context usage percentage available to TUI renderers through the session object.

Files:

- `src/tau_coding/session.py`
- `src/tau_coding/commands.py` protocol only if needed later
- `src/tau_coding/tui/widgets.py` protocol only if needed later
- relevant fake sessions in tests

Acceptance criteria:

- `CodingSession.context_usage_percent` returns the helper result using `context_token_estimate` and `context_window_tokens`.
- The property is cache-friendly and reuses existing context usage invalidation indirectly through `context_token_estimate`.

### Task 3 — Add durable TUI config setting

**Goal:** Let users choose their preferred context display in `~/.tau/tui.json`.

Files:

- `src/tau_coding/tui/config.py`
- `tests/test_tui_config.py`

Acceptance criteria:

- `TuiSettings().context_usage_display == "tokens"`.
- `TuiSettings(context_usage_display="both").to_json()` includes `"context_usage_display": "both"`.
- `tui_settings_from_json({"context_usage_display": "percent"})` parses successfully.
- Invalid values like `"bar"`, `123`, or `""` raise `TuiConfigError` mentioning `context_usage_display`.
- Existing config files without the setting continue to parse.

Focused test command:

```bash
uv run pytest tests/test_tui_config.py
```

### Task 4 — Render configurable compact context usage

**Goal:** Update compact TUI chrome rendering while preserving the existing default.

Files:

- `src/tau_coding/tui/widgets.py`
- `tests/test_tui_app.py`

Acceptance criteria:

- Default/token mode still renders `12k/200k context` for the current fake session.
- Percent mode renders `6% context`.
- Both mode renders token count plus percentage, e.g. `12k/200k context · 6%`.
- Off mode omits context usage but still renders provider/model/thinking details.
- Percentage is always available for a valid coding session because unknown context windows fall back to Tau's default.

Focused test command:

```bash
uv run pytest tests/test_tui_app.py -k "compact_session_info or context_usage"
```

### Task 5 — Wire TUI settings into app refresh

**Goal:** Ensure the configured display mode is used in the live Textual app.

Files:

- `src/tau_coding/tui/app.py`
- `tests/test_tui_app.py`

Acceptance criteria:

- `_refresh_chrome(...)` passes `self.tui_settings.context_usage_display` to compact session info rendering.
- A TUI app test with `TuiSettings(context_usage_display="percent")` shows percent mode in `#compact-session-info` after refresh.

### Task 6 — Optional `/session` command enhancement

**Goal:** Decide whether slash-command status should include percentage independently of TUI chrome config.

Files, if implemented:

- `src/tau_coding/commands.py`
- `tests/test_commands.py`

Recommended acceptance criteria:

- `/session` includes a stable line like `Context usage: 123 / 584 tokens (21%)`.
- This command is not controlled by `context_usage_display`, because the setting is specifically TUI chrome presentation.

### Task 7 — Documentation and final checks

**Goal:** Document the user-facing setting and run focused quality checks.

Files:

- `website/src/content/docs/` appropriate TUI/config guide, if one exists
- this dev note if implementation choices change

Checks:

```bash
uv run pytest tests/test_context_window.py tests/test_tui_config.py tests/test_tui_app.py tests/test_commands.py
uv run ruff check .
uv run ruff format --check .
```
