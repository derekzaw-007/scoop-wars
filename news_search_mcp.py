"""Octen News Search as an MCP tool for Codex.

play.py starts Codex with this file registered as the `octen` MCP server, so the
agent gets one tool: news_search. Calls go through octen_client.news_search(),
which routes them via the game server so they count for scoring.

Change anything here: parameters, how results are trimmed, extra tools
(other Octen APIs are allowed; other search tools and web browsing are not).
API reference: https://docs.octen.ai/capabilities/news-search
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Codex starts this from a temp dir

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

from octen_client import news_search as octen_news_search  # noqa: E402

mcp = MCPServer("octen")


def _article(a: dict) -> dict:
    return {
        "title": a.get("title"),
        "url": a.get("url"),
        "source": a.get("authors"),
        "published": a.get("time_published"),
        "highlight": (a.get("highlight") or "")[:400],
    }


@mcp.tool()
def news_search(query: str, language: list[str] | None = None, time_range: str = "day", count: int = 10) -> str:
    """Search recent news articles with Octen News Search.

    Args:
        query: what to search for.
        language: optional ISO 639-1 codes to restrict results, e.g. ["en"].
        time_range: "day", "week", "month" or "year". Defaults to the last day.
        count: number of articles, 1 to 20.
    """
    body = {
        "query": query,
        "count": max(1, min(int(count), 20)),
        "time_range": time_range,
        "highlight": {"enable": True, "max_tokens": 200},
        "subjects": {"enable": True, "count": 2, "max_sub_news": 5},
    }
    if language:
        body["language"] = language
    data = octen_news_search(body)
    if data.get("code", 0) != 0:
        return json.dumps({"error": data.get("msg") or data.get("error") or "News Search failed", "code": data.get("code")})
    d = data.get("data") or {}
    out = {
        "results": [_article(a) for a in d.get("results") or []],
        "stories": [
            {
                "name": s.get("name"),
                "summary": s.get("summary"),
                "started": s.get("time_start"),
                "articles": [_article(a) for a in [s.get("top_news") or {}] + list(s.get("sub_news") or []) if a.get("url")],
            }
            for s in d.get("subjects") or []
        ],
    }
    return json.dumps(out, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
