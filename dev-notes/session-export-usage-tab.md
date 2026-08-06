# Session export usage tab

## What changed

HTML session exports now contain two self-contained tabs:

- **Transcript**: the existing session tree, entry stream, filters, and JSONL download.
- **Cache**: request-level token usage, cache behavior, output/reasoning totals, estimated cost, tool-call counts, and compactions for the active branch.

The Cache tab includes interactive SVG charts (hover tooltips, legend series toggles). Each chart can be downloaded as a 2x PNG with a white background. No network resources are required.

## Tabs

Tab switching is real `<button>`-driven `role="tablist"` behavior: click or use Left/Right arrows to move between panels; panels toggle with the `hidden` attribute and the transcript filter bar hides while the Cache tab is active. The active tab is reflected in the URL hash (`#cache`) so links can open the analytics directly.

## Tau theming

The export template now uses Tau's own TUI themes instead of ad-hoc colors:

- Light mode: `tau-light` (`src/tau_coding/tui/themes/tau-light.json`) — white canvas, `#0f766e` teal accent.
- Dark mode: `tau-dark` (`tau-dark.json`) — black canvas, `#a7f3f0` cyan accent.

Charts ship both palette variants per series (`data-dark`/`data-light` attributes) and recolor live when the theme toggle or system preference changes, via a `tau-themechange` event and a `.theme-dark` class on `<html>`. PNG downloads always render on white with print-friendly darker variants of the Tau accents.

The layout borrows the analysis-script terminal aesthetic: JetBrains Mono everywhere, `$`/`#` prompt markers, dashed rules, blinking cursor on the title, and flat squared panels.

## Design

Usage collection and rendering live in `src/tau_coding/session_usage.py`. This keeps analytics separate from the existing transcript renderer while allowing `render_session_html()` to compose both views into one standalone file.

The collector reads typed `SessionEntry` and `AssistantMessage` models rather than reparsing JSONL. Cost estimates use Tau's built-in provider catalog and its input-token pricing tiers. If no catalog rate exists, cost remains unavailable instead of guessing.

Only entries on the active session path feed the dashboard. If an export has no resolvable active path, it falls back to all visible entries.

The local `scripts/analyze_session.py` prototype remains outside this tracked implementation and unchanged as a behavior reference.

## Verification

```bash
uv run ruff check src/tau_coding tests
uv run ruff format --check src/tau_coding tests
uv run pytest
```
