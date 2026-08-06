# Session export usage tab

## What changed

HTML session exports now contain two self-contained tabs:

- **Transcript**: the existing session tree, entry stream, filters, and JSONL download.
- **Usage**: request-level token usage, cache behavior, output/reasoning totals, estimated cost, tool-call counts, and compactions for the active branch.

The Usage tab includes interactive SVG charts. Each chart can be downloaded as a 2x PNG with a white background. No network resources are required.

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
