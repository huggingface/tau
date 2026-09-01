# Usage events in HTML session exports

The HTML session export's former **Cache** tab is now **Usage**, matching its broader
request, token, cost, tool, and compaction data.

The prompt-input chart now places notable persisted session events at the next model
request:

- context compaction
- model change
- thinking-level change
- branch summary

Events after the final response are attached to that last request. Markers use the Tau
theme palette, include accessible SVG labels/tooltips, switch with the page theme, and
remain in downloaded PNG charts.

## Verification

```bash
uv run pytest tests/test_session_usage.py tests/test_session_export.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
