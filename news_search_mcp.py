"""Octen News Search as an MCP tool for Codex.

play.py starts Codex with this file registered as the `octen` MCP server, so the
agent gets one tool: news_search. During the game every call goes through the
Scoop Wars server, which forwards it to Octen with your own OCTEN_API_KEY and
logs it. A card only scores if your key made a News Search call during the clue.

Change anything here: parameters, how results are trimmed, extra tools
(other Octen APIs are allowed; other search tools and web browsing are not).
API reference: https://docs.octen.ai/capabilities/news-search
"""

import json
import os
import time
from pathlib import Path

import httpx
try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

KIT_DIR = Path(__file__).resolve().parent
OCTEN_URL = "https://api.octen.ai/news-search"


def load_env() -> dict:
    env = {}
    path = KIT_DIR / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("OCTEN_", "SCOOP_"))})
    return env


ENV = load_env()
TEAM = json.loads((KIT_DIR / ".scoop.json").read_text()) if (KIT_DIR / ".scoop.json").exists() else {}

mcp = MCPServer("octen")


def _post(body: dict) -> dict:
    headers = {"x-api-key": ENV.get("OCTEN_API_KEY", "")}
    server = ENV.get("SCOOP_SERVER") or TEAM.get("server")
    if server and TEAM.get("token"):
        url = server.rstrip("/") + "/octen/news-search"  # logged, counts for scoring
        headers["x-scoop-token"] = TEAM["token"]
    else:
        url = OCTEN_URL  # not joined yet: direct call, works for testing but won't count in the game
    r = httpx.post(url, json=body, headers=headers, timeout=30)
    try:
        data = r.json()
    except ValueError:
        data = {"code": r.status_code, "msg": r.text[:200]}
    _log(body.get("query", ""), r.status_code, data)
    return data


def _log(query: str, status: int, data: dict) -> None:
    path = os.environ.get("SCOOP_RUN_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps({"t": time.time(), "query": query, "status": status, "code": data.get("code")}) + "\n")


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
    data = _post(body)
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
