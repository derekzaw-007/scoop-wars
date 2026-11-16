"""Local eval dashboard: `uv run play.py ui`, then open http://localhost:8765

Run Eval starts `play.py eval` in a fresh process (so it always uses your latest
code), and the page follows it live from runs/current.json. Past runs come from
runs/eval-*.json. Standard library only.
"""

import json
import mimetypes
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

KIT_DIR = Path(__file__).resolve().parent
UI_DIR = KIT_DIR / "ui"
RUNS_DIR = KIT_DIR / "runs"

_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()
_log_path = RUNS_DIR / "ui-eval.log"


def _team() -> dict:
    path = KIT_DIR / ".scoop.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _runs() -> list[dict]:
    out = []
    for f in sorted(RUNS_DIR.glob("eval-*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        summary = data.get("summary")
        if not summary:  # runs saved before summaries existed
            rows = data.get("rows", [])
            summary = {"n": len(rows), "right": sum(r.get("verdict") == "right" for r in rows), "points": sum(r.get("points") or 0 for r in rows)}
        out.append({"name": f.name, "at": data.get("at"), "secs": data.get("secs"), **summary})
    return out


def _running() -> bool:
    return _proc is not None and _proc.poll() is None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the terminal quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._send(200, (UI_DIR / "eval.html").read_bytes(), "text/html; charset=utf-8")
        if url.path.startswith("/ui/"):
            f = (UI_DIR / url.path[4:]).resolve()
            if UI_DIR.resolve() in f.parents and f.is_file():
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            return self._send(404, b"not found", "text/plain")
        if url.path == "/api/status":
            current = RUNS_DIR / "current.json"
            cur = json.loads(current.read_text()) if current.exists() else None
            log_tail = _log_path.read_text()[-600:] if _log_path.exists() and not _running() and cur is None else ""
            return self._json({"running": _running(), "current": cur, "team": _team().get("name"), "log": log_tail})
        if url.path == "/api/runs":
            return self._json(_runs())
        if url.path == "/api/run":
            name = (q.get("name") or [""])[0]
            f = (RUNS_DIR / name).resolve()
            if f.parent == RUNS_DIR.resolve() and f.name.startswith("eval-") and f.exists():
                return self._send(200, f.read_bytes())
            return self._json({"error": "no such run"}, 404)
        return self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        global _proc
        if urlparse(self.path).path != "/api/start":
            return self._send(404, b"not found", "text/plain")
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        with _proc_lock:
            if _running():
                return self._json({"error": "An eval is already running."}, 409)
            parallel = max(1, min(int(body.get("parallel") or 4), 8))
            cmd = [sys.executable, str(KIT_DIR / "play.py"), "eval", "--parallel", str(parallel)]
            if body.get("only"):
                cmd += ["--only", str(body["only"])]
            RUNS_DIR.mkdir(exist_ok=True)
            (RUNS_DIR / "current.json").unlink(missing_ok=True)
            log = open(_log_path, "w")
            _proc = subprocess.Popen(cmd, cwd=KIT_DIR, stdout=log, stderr=subprocess.STDOUT)
        return self._json({"ok": True})


def serve(port: int = 8765, open_browser: bool = True) -> None:
    RUNS_DIR.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"Eval dashboard: {url}  (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
