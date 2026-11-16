"""Scoop Wars starter kit: the house agent.

  uv run play.py doctor              check Codex, your keys and the game server
  uv run play.py join "Team Name"    register your team (once)
  uv run play.py ask "some clue"     run the agent on one clue
  uv run play.py practice            run the practice clues and see where the agent fails
  uv run play.py play                join the Scoop clock; then hands off the keyboard

The agent is Codex (`codex exec`) with one tool, Octen News Search (news_search_mcp.py),
and the instructions in prompt.md. Change any of it.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

KIT_DIR = Path(__file__).resolve().parent
TEAM_FILE = KIT_DIR / ".scoop.json"
PROMPT_FILE = KIT_DIR / "prompt.md"
SCHEMA_FILE = KIT_DIR / "card.schema.json"
MCP_FILE = KIT_DIR / "news_search_mcp.py"


# --- config ----------------------------------------------------------------------
def load_env() -> None:
    path = KIT_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
CODEX = os.environ.get("CODEX_BIN") or shutil.which("codex") or "codex"
CODEX_MODEL = os.environ.get("CODEX_MODEL", "")  # empty = Codex default
CODEX_REASONING = os.environ.get("CODEX_REASONING", "low")  # minimal | low | medium | high


def team() -> dict:
    return json.loads(TEAM_FILE.read_text()) if TEAM_FILE.exists() else {}


def server_url() -> str:
    url = os.environ.get("SCOOP_SERVER") or team().get("server") or ""
    return url.rstrip("/")


# --- game server client ------------------------------------------------------------
class ServerError(Exception):
    pass


def api(method: str, path: str, body: dict | None = None, timeout: float = 10, admin: bool = False) -> dict | list:
    base = server_url()
    if not base:
        raise ServerError("SCOOP_SERVER is not set. Put the URL from the big screen in .env")
    headers = {"content-type": "application/json"}
    if team().get("token"):
        headers["x-scoop-token"] = team()["token"]
    if admin and os.environ.get("SCOOP_ADMIN_TOKEN"):
        headers["x-admin-token"] = os.environ["SCOOP_ADMIN_TOKEN"]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except Exception:
            msg = e.reason
        raise ServerError(f"{e.code}: {msg}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ServerError(f"can't reach {base}: {e}") from None


# --- the agent ----------------------------------------------------------------------
@dataclass
class Card:
    url: str = ""
    summary: str = ""
    seconds: float = 0
    searches: list[str] = field(default_factory=list)
    error: str = ""


def codex_command(workdir: str, out_file: str, run_log: str) -> list[str]:
    toml = json.dumps  # JSON strings are valid TOML basic strings
    cmd = [
        CODEX, "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",  # your personal Codex config can't add other tools
        "--sandbox", "read-only",
        "--color", "never",
        "--cd", workdir,
        "--output-schema", str(SCHEMA_FILE),
        "--output-last-message", out_file,
        "-c", f"mcp_servers.octen.command={toml(sys.executable)}",
        "-c", f"mcp_servers.octen.args=[{toml(str(MCP_FILE))}]",
        "-c", f"mcp_servers.octen.env={{SCOOP_RUN_LOG={toml(run_log)}}}",
        "-c", "mcp_servers.octen.startup_timeout_sec=20",
        "-c", "mcp_servers.octen.tool_timeout_sec=30",
        "-c", 'mcp_servers.octen.default_tools_approval_mode="approve"',  # no human to click approve
        "-c", f"model_reasoning_effort={toml(CODEX_REASONING)}",
        # Only Octen counts as search: keep Codex's own web tools off.
        "-c", 'web_search="disabled"',
        "-c", "tools.web_search=false",
        "-c", "features.browser_use=false",
        "-c", "features.computer_use=false",
        "-c", "features.apps=false",
    ]
    if CODEX_MODEL:
        cmd += ["--model", CODEX_MODEL]
    cmd.append("-")  # prompt from stdin
    return cmd


def run_agent(clue: str, timeout: float = 40, verbose: bool = False) -> Card:
    """Run Codex on one clue and return its card. Never raises."""
    started = time.time()
    workdir = tempfile.mkdtemp(prefix="scoop-")  # empty dir: no AGENTS.md, nothing to read
    out_file = os.path.join(workdir, "card.json")
    run_log = os.path.join(workdir, "searches.jsonl")
    prompt = PROMPT_FILE.read_text().strip() + f"\n\nClue: {clue}\n"
    card = Card()
    try:
        proc = subprocess.Popen(
            codex_command(workdir, out_file, run_log),
            stdin=subprocess.PIPE,
            stdout=None if verbose else subprocess.DEVNULL,
            stderr=None if verbose else subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        card.error = "codex not found. Install it: npm install -g @openai/codex"
        return card
    try:
        _, err = proc.communicate(prompt, timeout=timeout)
        if proc.returncode != 0:
            card.error = f"codex exited {proc.returncode}: {(err or '').strip()[-300:]}"
    except subprocess.TimeoutExpired:
        kill(proc)
        card.error = f"timed out after {timeout:.0f}s"
    card.seconds = time.time() - started
    if os.path.exists(run_log):
        with open(run_log) as f:
            card.searches = [json.loads(line)["query"] for line in f if line.strip()]
    if os.path.exists(out_file):
        try:
            data = json.loads(Path(out_file).read_text())
            card.url = (data.get("url") or "").strip()
            card.summary = (data.get("summary") or "").strip()
        except (json.JSONDecodeError, AttributeError):
            card.error = card.error or "agent's final answer was not a JSON card"
    shutil.rmtree(workdir, ignore_errors=True)
    return card


def kill(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)  # codex and its MCP server
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def print_card(card: Card) -> None:
    print(f"  url:      {card.url or '(none)'}")
    print(f"  summary:  {card.summary or '(none)'}")
    print(f"  searches: {len(card.searches)}  {'; '.join(card.searches)[:150]}")
    print(f"  time:     {card.seconds:.1f}s" + (f"   error: {card.error}" if card.error else ""))


# --- answer checking (practice) ------------------------------------------------------
def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    path = parts.path.rstrip("/")
    if path.endswith("/amp"):
        path = path[:-4]
    skip = {"fbclid", "gclid", "ref", "ref_src", "cmpid", "smid", "ocid", "src", "guccounter"}
    q = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in skip)
    return host + path + ("?" + urlencode(q) if q else "")


def check_local(clue: dict, url: str) -> dict:
    arts = (clue.get("key") or {}).get("articles") or []
    times = {normalize_url(a["url"]): a.get("time_published") or "" for a in arts}
    earliest = min((t for t in times.values() if t), default=None)
    norm = normalize_url(url)
    return {
        "correct": norm in times,
        "first_report": norm in times and len(times) >= 3 and earliest is not None and times[norm] == earliest,
        "story": (clue.get("key") or {}).get("story"),
    }


# --- commands ---------------------------------------------------------------------
def cmd_doctor(args) -> None:
    ok = True

    def line(good: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else '!!'}] {label}" + (f"  {detail}" if detail else ""))

    print("Scoop Wars doctor")
    line(sys.version_info >= (3, 10), "Python", sys.version.split()[0])
    codex = shutil.which(CODEX) or (CODEX if os.path.exists(CODEX) else None)
    line(bool(codex), "Codex CLI", codex or "not found: npm install -g @openai/codex")
    if codex:
        r = subprocess.run([CODEX, "login", "status"], capture_output=True, text=True)
        status = (r.stdout + r.stderr).strip().splitlines()
        line(r.returncode == 0, "Codex login", status[-1] if status else "run: codex login")
    try:
        import mcp  # noqa: F401
        import httpx  # noqa: F401

        line(True, "Python packages")
    except ImportError as e:
        line(False, "Python packages", f"{e}. Run with: uv run play.py doctor")
    key = os.environ.get("OCTEN_API_KEY", "")
    line(bool(key), "OCTEN_API_KEY", "set in .env" if key else "missing: add it to .env")
    base = server_url()
    line(bool(base), "SCOOP_SERVER", base or "missing: add the URL from the big screen to .env")
    if base:
        try:
            api("GET", "/healthz", timeout=5)
            line(True, "Game server reachable")
        except ServerError as e:
            line(False, "Game server reachable", str(e))
    t = team()
    line(bool(t.get("token")), "Team joined", t.get("name") or 'run: uv run play.py join "Team Name"')
    if key:
        sys.path.insert(0, str(KIT_DIR))
        try:
            import news_search_mcp

            res = json.loads(news_search_mcp.news_search("Singapore", count=1))
            line("error" not in res, "News Search call", res.get("error", "works" + (" (through the game server)" if t.get("token") and base else " (direct)")))
        except Exception as e:  # noqa: BLE001
            line(False, "News Search call", str(e))
    print("\nAll good. Next: uv run play.py practice" if ok else "\nFix the [!!] lines, then run doctor again.")


def cmd_join(args) -> None:
    base = server_url()
    if not base:
        sys.exit("Set SCOOP_SERVER in .env first (the URL on the big screen).")
    if team().get("token") and not args.force:
        sys.exit(f"Already joined as '{team()['name']}'. Use --force to join as a new team.")
    try:
        res = api("POST", "/api/join", {"name": args.name, "house": args.house}, admin=args.house)
    except ServerError as e:
        sys.exit(f"Join failed: {e}")
    TEAM_FILE.write_text(json.dumps({"name": res["name"], "token": res["token"], "server": base}, indent=2))
    print(f"Joined as '{res['name']}'. Next: uv run play.py practice")


def cmd_ask(args) -> None:
    print(f"Clue: {args.clue}\n")
    card = run_agent(args.clue, timeout=args.timeout, verbose=args.verbose)
    print_card(card)


def cmd_practice(args) -> None:
    if args.file:
        doc = json.loads(Path(args.file).read_text())
        clues = doc.get("practice", []) + (doc.get("game", []) if args.all else [])
        for i, c in enumerate(clues):
            c.setdefault("id", c.get("id") or f"g{c.get('n', i + 1)}")
    else:
        try:
            clues = api("GET", "/api/practice")
        except ServerError as e:
            sys.exit(f"Can't load practice clues: {e}")
    if args.only:
        clues = [c for c in clues if c["id"] in args.only.split(",")]
    if not clues:
        sys.exit("No practice clues yet.")
    right = 0
    for c in clues:
        print(f"\n[{c['id']}] {c['text']}")
        card = run_agent(c["text"], timeout=args.timeout, verbose=args.verbose)
        print_card(card)
        if not card.url:
            print("  result:   no answer (0)")
            continue
        try:
            res = check_local(c, card.url) if args.file else api("POST", "/api/practice/check", {"id": c["id"], "url": card.url})
        except ServerError as e:
            print(f"  result:   couldn't check ({e})")
            continue
        right += res["correct"]
        verdict = "RIGHT" + (" + first report" if res.get("first_report") else "") if res["correct"] else "not in the answer key"
        print(f"  result:   {verdict}" + (f"   (story: {res['story']})" if res.get("story") else ""))
        if not card.searches:
            print("  warning:  no News Search call. In the game this card scores 0.")
    print(f"\n{right}/{len(clues)} right")


def cmd_play(args) -> None:
    if not team().get("token"):
        sys.exit('Join first: uv run play.py join "Team Name"')
    print(f"Playing as '{team()['name']}' on {server_url()}. Hands off the keyboard once the clock starts.\n")
    attempted: set[int] = set()
    worker: threading.Thread | None = None
    last_msg = ""

    def answer(n: int, text: str, budget: float) -> None:
        print(f"\n=== Clue {n}: {text}")
        card = run_agent(text, timeout=budget)
        print_card(card)
        try:
            api("POST", "/api/cards", {"n": n, "url": card.url, "summary": card.summary})
            print(f"  submitted clue {n}")
        except ServerError as e:
            print(f"  NOT submitted: {e}. Paste the card into the backup form.")

    while True:
        try:
            st = api("GET", "/api/state", timeout=5)
        except ServerError as e:
            msg = f"[server] {e}. Retrying..."
            if msg != last_msg:
                print(msg)
                last_msg = msg
            time.sleep(2)
            continue
        busy = worker is not None and worker.is_alive()
        if st["phase"] == "open" and st["clue"] and not st["answered"] and st["clue"]["n"] not in attempted and not busy:
            n = st["clue"]["n"]
            attempted.add(n)
            budget = max(5.0, (st["closes_in"] or 45) - 4)
            worker = threading.Thread(target=answer, args=(n, st["clue"]["text"], budget), daemon=True)
            worker.start()
        elif st["phase"] == "done" and not busy:
            print("\nScoop clock finished. Watch the big screen.")
            return
        elif st["phase"] == "lobby":
            msg = "Connected. Waiting for the Scoop clock to start" + (f" (in {st['next_in']:.0f}s)" if st.get("next_in") else "") + "..."
            if msg.split(" (")[0] != last_msg.split(" (")[0]:
                print(msg)
            last_msg = msg
        time.sleep(1)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check your setup").set_defaults(fn=cmd_doctor)
    j = sub.add_parser("join", help="register your team")
    j.add_argument("name")
    j.add_argument("--force", action="store_true", help="join again as a new team")
    j.add_argument("--house", action="store_true", help=argparse.SUPPRESS)
    j.set_defaults(fn=cmd_join)
    a = sub.add_parser("ask", help="run the agent on one clue")
    a.add_argument("clue")
    a.add_argument("--timeout", type=float, default=40)
    a.add_argument("-v", "--verbose", action="store_true", help="show Codex's output")
    a.set_defaults(fn=cmd_ask)
    pr = sub.add_parser("practice", help="run the practice clues")
    pr.add_argument("--only", help="comma-separated clue ids")
    pr.add_argument("--file", help=argparse.SUPPRESS)  # organizers: a clues.json with answer keys
    pr.add_argument("--all", action="store_true", help=argparse.SUPPRESS)  # with --file: game clues too
    pr.add_argument("--timeout", type=float, default=40)
    pr.add_argument("-v", "--verbose", action="store_true")
    pr.set_defaults(fn=cmd_practice)
    sub.add_parser("play", help="play the Scoop clock").set_defaults(fn=cmd_play)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
