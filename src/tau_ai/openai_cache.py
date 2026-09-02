"""Shared OpenAI prompt-cache affinity helpers."""

from __future__ import annotations

from urllib.parse import urlsplit

OPENAI_API_HOST = "api.openai.com"
OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH = 64


def openai_prompt_cache_key(session_id: str | None) -> str | None:
    """Return a non-empty session-derived key within OpenAI's 64-char limit."""
    if not session_id:
        return None
    return session_id[:OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH]


def is_direct_openai_url(base_url: str) -> bool:
    """Return whether a URL targets OpenAI's first-party API host."""
    return urlsplit(base_url).hostname == OPENAI_API_HOST
