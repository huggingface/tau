"""Portable tool-call identifiers for cross-provider history replay."""

from __future__ import annotations

import re
from hashlib import sha256

# Anthropic has the strictest documented identifier alphabet among Tau's
# providers. Keeping translated IDs within this common subset also avoids
# provider-specific punctuation and length constraints elsewhere.
_PORTABLE_TOOL_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def portable_tool_call_id(value: str) -> str:
    """Return a deterministic provider-safe correlation ID.

    Provider-native IDs that already fit the common format remain unchanged,
    preserving same-provider cache/replay behavior. Other IDs are hashed rather
    than character-replaced so distinct native IDs cannot collapse together.
    """
    if _PORTABLE_TOOL_CALL_ID.fullmatch(value):
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"tc_{digest[:40]}"
