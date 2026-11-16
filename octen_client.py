"""Octen News Search client shared by the MCP tool and any agent you write yourself.

Use news_search() for every search. It sends the call through the Scoop Wars game
server when you've joined (that's how your call gets counted: a card scores 0 if
your key made no News Search call during the clue) and logs it so `play.py eval`
can count your searches.

If you replace Codex with your own agent, call this module and record the searches
so `play.py eval` can see them:

    import octen_client
    with octen_client.capture() as searches:
        data = octen_client.news_search({"query": "...", "count": 10, "time_range": "day"})
    # searches == ["..."]

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
OCTEN_URL = "https://api.octen.ai/news-search"


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


@contextlib.contextmanager
def capture():
    """Collect the queries of every news_search() call made in this block (per thread)."""
    searches: list[str] = []
    token = _captured.set(searches)
    try:
        yield searches
    finally:
        _captured.reset(token)


def news_search(body: dict) -> dict:
    """POST a News Search request body; returns Octen's JSON response.

    On failure returns {"code": <non-zero>, "msg": "..."} instead of raising.
    """
    env, team = _load_env(), _team()
    headers = {"x-api-key": env.get("OCTEN_API_KEY", "")}
    server = env.get("SCOOP_SERVER") or team.get("server")
    if server and team.get("token"):
        url = server.rstrip("/") + "/octen/news-search"  # logged by the game server: counts for scoring
        headers["x-scoop-token"] = team["token"]
    else:
        url = OCTEN_URL  # not joined: works for testing, but won't count in the game
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
    _log(body.get("query", ""), status, data, time.time() - started)
    return data


def _log(query: str, status: int, data: dict, secs: float) -> None:
    captured = _captured.get()
    if captured is not None and status == 200 and data.get("code", 0) == 0:
        captured.append(query)
    path = os.environ.get("SCOOP_RUN_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps({"t": time.time(), "query": query, "status": status, "code": data.get("code"), "secs": round(secs, 2)}) + "\n")
