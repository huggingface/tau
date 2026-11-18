"""Opt-in web search tool backed by Keenable.

Registers a `web_search` tool that returns titles, URLs and snippets so the
agent can cite its sources. Works without an account (keyless public endpoint,
rate limited per IP); set `KEENABLE_API_KEY` to use the keyed endpoint instead.
Nothing is sent anywhere unless you load this extension.

    tau -e examples/extensions/keenable_search.py

Only the query text leaves your machine. Docs: https://docs.keenable.ai
"""

import os

import httpx

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult
from tau_coding.extensions import ExtensionAPI

KEYLESS_URL = "https://api.keenable.ai/v1/search/public"
KEYED_URL = "https://api.keenable.ai/v1/search"
APP_TITLE = "Tau"
DEFAULT_RESULTS = 5
MAX_RESULTS = 10
TIMEOUT_SECONDS = 20.0

# Tests swap this for a client with an httpx.MockTransport.
_client_factory = httpx.AsyncClient


def _request(query: str, count: int) -> tuple[str, dict[str, str], dict[str, object]]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = os.environ.get("KEENABLE_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
        url = KEYED_URL
    else:
        # The keyless endpoint requires an app title.
        headers["X-Keenable-Title"] = APP_TITLE
        url = KEYLESS_URL
    return url, headers, {"query": query, "max_results": count}


def _format(results: list[dict[str, object]]) -> str:
    blocks = []
    for index, item in enumerate(results, start=1):
        title = str(item.get("title") or "").strip() or str(item["url"])
        snippet = str(item.get("snippet") or item.get("description") or "").strip()
        if len(snippet) > 500:
            snippet = snippet[:500].rstrip() + "…"
        blocks.append(f"[{index}] {title}\n{item['url']}\n{snippet}".rstrip())
    return "\n\n".join(blocks)


def _error(message: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=message)], details={"error": message})


async def _run_search(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
    del tool_call_id, on_update
    query = str(arguments.get("query", "")).strip()
    if not query:
        return _error("web_search needs a non-empty query.")
    try:
        count = int(arguments.get("max_results", DEFAULT_RESULTS))
    except (TypeError, ValueError):
        count = DEFAULT_RESULTS
    count = max(1, min(count, MAX_RESULTS))

    url, headers, body = _request(query, count)
    try:
        # follow_redirects=False so an API key is never resent to another host.
        async with _client_factory(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return _error("Keenable search timed out.")
    except httpx.HTTPError as exc:
        return _error(f"Keenable search failed: {type(exc).__name__}.")
    if signal is not None and signal.is_cancelled():
        return _error("Keenable search was cancelled.")
    if response.status_code == 429:
        return _error("Keenable rate limit reached. Try again later or set KEENABLE_API_KEY.")
    if response.status_code != 200:
        return _error(f"Keenable search failed: HTTP {response.status_code}.")

    try:
        payload = response.json()
    except ValueError:
        return _error("Keenable search returned invalid JSON.")
    raw = payload.get("results") if isinstance(payload, dict) else None
    results = [item for item in raw or [] if isinstance(item, dict) and item.get("url")][:count]
    if not results:
        return AgentToolResult(
            content=[TextContent(text=f"No web results for: {query}")],
            details={"results": []},
        )
    return AgentToolResult(
        content=[TextContent(text=_format(results))],
        details={"results": [str(item["url"]) for item in results]},
    )


def setup(tau: ExtensionAPI) -> None:
    """Register the Keenable web_search tool."""
    tau.register_tool(
        AgentTool(
            name="web_search",
            label="web search",
            description=(
                "Search the public web with Keenable. Returns numbered results with "
                "title, URL and snippet. Cite the URLs you rely on."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for."},
                    "max_results": {
                        "type": "integer",
                        "description": f"How many results to return (1-{MAX_RESULTS}).",
                        "minimum": 1,
                        "maximum": MAX_RESULTS,
                    },
                },
                "required": ["query"],
            },
            execute_fn=_run_search,
            prompt_snippet="Search the public web (Keenable) and cite result URLs.",
        )
    )
