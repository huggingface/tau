"""Append structured, always-on instructions to Tau's system prompt."""

from tau_coding.extensions import ExtensionAPI


def setup(tau: ExtensionAPI) -> None:
    """Add a labeled procedure while this extension generation is active."""
    tau.add_prompt_section(
        "Review procedure",
        """Read the complete diff before editing.

Run the relevant checks before reporting success:

```bash
uv run pytest
uv run ruff check .
```
""",
    )
