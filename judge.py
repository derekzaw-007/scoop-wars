"""The Scoop Wars judge: does a card's link report the clue's story?

The game server uses this same code for links that aren't in the answer key,
and `play.py eval` uses it to score your practice runs, so eval scores match
how the game is scored.

A link is right when the article reports the same event as the answer key's
story (not an earlier or different similar event), was published in the last
24 hours, and the card's summary describes that event.

The judge is `codex exec` with no tools. It sees the clue, the answer key's
story, the card, and the page title and publish time fetched from the link.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import httpx

CODEX = os.environ.get("CODEX_BIN") or shutil.which("codex") or "codex"
JUDGE_MODEL = os.environ.get("SCOOP_JUDGE_MODEL", "")
JUDGE_REASONING = os.environ.get("SCOOP_JUDGE_REASONING", "low")

MATCH, NO_MATCH, UNSURE = "match", "no_match", "unsure"

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": [MATCH, NO_MATCH, UNSURE]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

PROMPT = """You are the judge for Scoop Wars, a news-finding game. A team's agent read a clue and answered with a link to a news article and a one-sentence summary. Decide whether the answer is right.

The answer is right only if ALL of these hold:
1. The article reports the same specific event as the answer key's story. A different event of the same kind does not count (for example yesterday's rate decision, another country's similar announcement, or an older stage of the story).
2. The article was published in the 24 hours before the clue was released, as far as you can tell from the publish time and the URL. Treat a URL date older than that as wrong.
3. The summary describes that event. Minor wording or language differences are fine.
{language_rule}
Articles in any language count. If the evidence is too thin to tell (for example the page could not be fetched and the URL and summary are ambiguous), answer "unsure". Do not guess "match" without evidence.

Clue released at: {released}
Clue: {clue}

Answer key story: {story}
Story summary: {story_summary}
Story started: {story_started}
Articles in the answer key:
{key_articles}

Team's link: {url}
Team's summary: {summary}
Fetched from the link: title: {page_title} | published: {page_published}

Answer with a verdict and a one-sentence reason."""


# --- page metadata ---------------------------------------------------------------------
_META = [
    r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|og:published_time|datePublished|pubdate|publishdate|publish-date|date|dc\.date|parsely-pub-date)["\'][^>]*content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name|itemprop)=["\'](?:article:published_time|og:published_time|datePublished|pubdate|publishdate|publish-date|date)["\']',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'<time[^>]+datetime=["\']([^"\']+)',
]


def fetch_page_meta(url: str, timeout: float = 6.0) -> dict:
    """Best-effort title and publish time of an article. Never raises."""
    try:
        r = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"user-agent": "Mozilla/5.0 (compatible; ScoopWarsJudge/1.0)", "accept-language": "en"},
        )
        html = r.text[:400_000]
    except Exception:
        return {}
    out = {}
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I) or re.search(
        r"<title[^>]*>(.*?)</title>", html, re.I | re.S
    )
    if m:
        title = unescape(" ".join(m.group(1).split()))[:300]
        if not re.search(r"just a moment|access denied|attention required|forbidden|captcha|are you a robot|^40[0-9]", title, re.I):
            out["title"] = title
    for pattern in _META:
        m = re.search(pattern, html, re.I)
        if m:
            out["published"] = m.group(1).strip()[:40]
            break
    return out


def _norm(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url.strip() if "://" in url else "https://" + url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    return host + parts.path.rstrip("/")


def _octen_key() -> str:
    key = os.environ.get("OCTEN_API_KEY", "")
    if not key:
        here = Path(__file__).resolve().parent
        for env in (here / ".env", here.parent.parent / ".env"):
            if env.exists():
                for line in env.read_text().splitlines():
                    if line.strip().startswith("OCTEN_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    return key


def lookup_in_octen(url: str, summary: str, timeout: float = 15.0) -> dict:
    """Find the article in Octen's news index to get its title and publish time. Never raises.

    Used when the page itself can't be fetched (paywalls, bot blocks).
    """
    key = _octen_key()
    if not key or not (summary or url):
        return {}
    target = _norm(url)
    for query in filter(None, [summary[:300], url]):
        try:
            r = httpx.post(
                "https://api.octen.ai/news-search",
                json={"query": query, "count": 20, "time_range": "week", "highlight": {"enable": False}, "subjects": {"enable": False}},
                headers={"x-api-key": key},
                timeout=timeout,
            )
            for a in (r.json().get("data") or {}).get("results") or []:
                if _norm(a.get("url") or "") == target:
                    return {"title": a.get("title"), "published": a.get("time_published"), "source": "octen index"}
        except Exception:
            continue
    return {}


# --- judging ------------------------------------------------------------------------------
def _iso(ts: float | None) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


LANGUAGE_NAMES = {  # every language News Search supports
    "ar": "Arabic", "de": "German", "en": "English", "es": "Spanish", "fr": "French", "hi": "Hindi", "id": "Indonesian",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese", "ru": "Russian",
    "th": "Thai", "tr": "Turkish", "vi": "Vietnamese", "zh": "Chinese",
}


def build_prompt(clue: str, key: dict, url: str, summary: str, released_at: float | None, page: dict, require_language: list[str] | None = None) -> str:
    language_rule = ""
    if require_language:
        names = " or ".join(LANGUAGE_NAMES.get(code, code) for code in require_language)
        language_rule = (
            f"4. For this clue the article itself must be written in {names}. Coverage of the same event in any other "
            f"language (including English) is wrong. Judge the language from the title and URL; if you can't tell, answer \"unsure\".\n"
        )
    arts = (key or {}).get("articles") or []
    listed = "\n".join(f"- {a.get('title') or ''} | {a.get('url')} | published {a.get('time_published') or '?'}" for a in arts[:8])
    return PROMPT.format(
        language_rule=language_rule,
        released=_iso(released_at),
        clue=clue,
        story=(key or {}).get("story") or "unknown",
        story_summary=(key or {}).get("summary") or "none",
        story_started=(key or {}).get("started") or "unknown",
        key_articles=listed or "- none",
        url=url,
        summary=summary or "(none)",
        page_title=page.get("title") or "could not fetch",
        page_published=page.get("published") or "unknown",
    )


def judge(clue: str, key: dict, url: str, summary: str, released_at: float | None = None, require_language: list[str] | None = None, timeout: float = 90) -> dict:
    """Return {"verdict": match|no_match|unsure, "reason": str, "secs": float}. Never raises."""
    started = time.time()
    if not url:
        return {"verdict": NO_MATCH, "reason": "No link.", "secs": 0.0}
    page = fetch_page_meta(url)
    if not page.get("published"):
        found = lookup_in_octen(url, summary)
        page = {**page, **found}  # the index's title and time beat a blocked page's
    prompt = build_prompt(clue, key, url, summary, released_at or time.time(), page, require_language)
    workdir = tempfile.mkdtemp(prefix="scoop-judge-")
    schema_path = os.path.join(workdir, "schema.json")
    out_path = os.path.join(workdir, "verdict.json")
    Path(schema_path).write_text(json.dumps(SCHEMA))
    cmd = [
        CODEX, "exec",
        "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
        "--sandbox", "read-only", "--color", "never",
        "--cd", workdir,
        "--output-schema", schema_path,
        "--output-last-message", out_path,
        "-c", f"model_reasoning_effort={json.dumps(JUDGE_REASONING)}",
        "-c", 'web_search="disabled"',
        "-c", "tools.web_search=false",
        "-c", "features.browser_use=false",
        "-c", "features.computer_use=false",
        "-c", "features.apps=false",
    ]
    if JUDGE_MODEL:
        cmd += ["--model", JUDGE_MODEL]
    cmd.append("-")
    result = {"verdict": UNSURE, "reason": "Judge failed to run."}
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
        if os.path.exists(out_path):
            data = json.loads(Path(out_path).read_text())
            if data.get("verdict") in (MATCH, NO_MATCH, UNSURE):
                result = {"verdict": data["verdict"], "reason": (data.get("reason") or "").strip()[:300]}
        elif proc.returncode != 0:
            result["reason"] = f"Judge error: {(proc.stderr or '').strip()[-200:]}"
    except FileNotFoundError:
        result["reason"] = "codex not installed, so the judge can't run."
    except subprocess.TimeoutExpired:
        result["reason"] = "Judge timed out."
    except (json.JSONDecodeError, OSError) as e:
        result["reason"] = f"Judge output unreadable: {e}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    result["secs"] = round(time.time() - started, 1)
    return result
