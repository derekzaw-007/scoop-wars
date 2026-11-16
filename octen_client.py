"""Octen search client shared by the MCP tool and any agent you write yourself.

Use news_search() for every News Search, and search(api, body) for the other Octen
search APIs (SEARCH_APIS). Both send the call through the Scoop Wars game server when
you've joined, and log it so `play.py eval` can count your searches. A card scores 0
if your key made no News Search call during the clue. All search calls share one
budget: SEARCH_BUDGET per clue, whichever API they use.

If you replace Codex with your own agent, call this module and record the searches
so `play.py eval` can see them:

    import octen_client
    with octen_client.capture() as searches:
        data = octen_client.news_search({"query": "...", "count": 10, "time_range": "day"})
    # searches == ["..."]

    data = octen_client.search("search", {"query": "...", "count": 5})  # Web Search, same budget

API reference: https://docs.octen.ai/capabilities/news-search
"""

import contextlib
import contextvars
import json
import os
import time
from pathlib import Path

import httpx

KIT_DIR = Path(__file__).resolve().parent
OCTEN_URL = "https://api.octen.ai"
# Search calls allowed per clue, all search APIs combined. The game server enforces
# this during the Scoop clock; it's applied here too so `play.py eval` plays by the same rule.
SEARCH_BUDGET = int(os.environ.get("SCOOP_SEARCH_BUDGET", "2"))
# The Octen search APIs allowed in the game (endpoint -> name). Broad Search, Answer,
# Search Agent, Deep Research and Auto Search run many searches per call: not allowed.
SEARCH_APIS = {
    "news-search": "News Search",
    "search": "Web Search",
    "pulse-search": "Pulse Search",
    "image-search": "Image Search",
    "video-search": "Video Search",
    "business-search": "Business Search",
    "place-search": "Place Search",
}


def _load_env() -> dict:
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


def _team() -> dict:
    path = KIT_DIR / ".scoop.json"
    return json.loads(path.read_text()) if path.exists() else {}


_client = httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0))
_captured: contextvars.ContextVar[list | None] = contextvars.ContextVar("octen_captured", default=None)
_attempts: contextvars.ContextVar[list | None] = contextvars.ContextVar("octen_attempts", default=None)


@contextlib.contextmanager
def capture():
    """Collect the queries of every successful search call made in this block (per thread).

    Calls to APIs other than News Search are recorded as "<API name>: <query>".
    """
    searches: list[str] = []
    token, token2 = _captured.set(searches), _attempts.set([])
    try:
        yield searches
    finally:
        _captured.reset(token)
        _attempts.reset(token2)


def label(api: str, query: str) -> str:
    """How a search is recorded in Card.searches: News Search queries as-is, others prefixed."""
    return query if api == "news-search" else f"{SEARCH_APIS.get(api, api)}: {query}"


def is_news(entry: str) -> bool:
    """True if a Card.searches entry is a News Search call (the one a card needs to score)."""
    return not any(entry.startswith(f"{name}: ") for api, name in SEARCH_APIS.items() if api != "news-search")


def _calls_this_clue() -> int | None:
    """Calls already made for the current clue, or None outside a clue run."""
    attempts = _attempts.get()
    if attempts is not None:
        return len(attempts)
    path = os.environ.get("SCOOP_RUN_LOG")  # set by play.py for each Codex run
    if path:
        if not os.path.exists(path):
            return 0
        with open(path) as f:
            return sum(1 for line in f if line.strip() and json.loads(line).get("status") != 429)
    return None


def news_search(body: dict) -> dict:
    """POST a News Search request body; returns Octen's JSON response.

    On failure returns {"code": <non-zero>, "msg": "..."} instead of raising.
    """
    return search("news-search", body)


def search(api: str, body: dict) -> dict:
    """POST a request body to one of the Octen search APIs in SEARCH_APIS, e.g. "search"
    for Web Search. Returns Octen's JSON response, or {"code": <non-zero>, "msg": "..."}.
    Every call counts against the same per-clue budget as News Search.
    """
    if api not in SEARCH_APIS:
        return {"code": 400, "msg": f"{api} is not allowed in Scoop Wars. Allowed: {', '.join(SEARCH_APIS)}"}
    used = _calls_this_clue()
    if SEARCH_BUDGET and used is not None and used >= SEARCH_BUDGET:
        data = {"code": 429, "msg": f"Search budget used: {SEARCH_BUDGET} search calls per clue. Answer with what you have."}
        _log(api, body.get("query", ""), 429, data, 0.0)
        return data
    attempts = _attempts.get()
    if attempts is not None:
        attempts.append(body.get("query", ""))
    env, team = _load_env(), _team()
    headers = {"x-api-key": env.get("OCTEN_API_KEY", "")}
    server = env.get("SCOOP_SERVER") or team.get("server")
    if server and team.get("token"):
        url = f"{server.rstrip('/')}/octen/{api}"  # logged by the game server: counts for scoring
        headers["x-scoop-token"] = team["token"]
    else:
        url = f"{OCTEN_URL}/{api}"  # not joined: works for testing, but won't count in the game
    started = time.time()
    try:
        r = _client.post(url, json=body, headers=headers)
        try:
            data = r.json()
        except ValueError:
            data = {"code": r.status_code, "msg": r.text[:200]}
        if r.status_code != 200 and data.get("code", 0) == 0:
            data["code"] = r.status_code
        status = r.status_code
    except httpx.HTTPError as e:
        data, status = {"code": 599, "msg": f"network error: {e}"}, 599
    _log(api, body.get("query", ""), status, data, time.time() - started)
    return data


def _log(api: str, query: str, status: int, data: dict, secs: float) -> None:
    captured = _captured.get()
    if captured is not None and status == 200 and data.get("code", 0) == 0:
        captured.append(label(api, query))
    path = os.environ.get("SCOOP_RUN_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps({"t": time.time(), "api": api, "query": query, "status": status, "code": data.get("code"), "secs": round(secs, 2)}) + "\n")
