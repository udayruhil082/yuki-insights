"""Yuki Insights desktop app.

Starts a small private web server on this computer only (127.0.0.1),
opens the app in your browser, and runs the collector when you press
"Refresh everything". Nothing is shared online.

Start it with the "Start Yuki Insights" file, or:  python radar/app.py
"""
import base64
import json
import os
import urllib.request
import socket
import subprocess
import sys
import re
import threading
import time
import webbrowser
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = DOCS / "data"
LOGGED = DATA / "logged_posts.json"
STATE = {"running": False, "started": None, "finished": None, "ok": None, "log": ""}
LOCK = threading.Lock()
AUTO_REFRESH_HOURS = 6
GH_FILE = ROOT / "config" / "github_local.json"   # private; never uploaded (see .gitignore)
FIAMA = ROOT / "docs" / "data" / "fiama_activity.json"
SYNC_FILES = ["fiama_activity.json", "items.json", "stats.json", "products.json", "seed.json", "digest.md", "competitor_radar.xlsx"]


def gh_config():
    try:
        c = json.loads(GH_FILE.read_text())
        return c if c.get("repo") and c.get("token") else None
    except Exception:
        return None


def gh(path, method="GET", body=None, raw=False):
    c = gh_config()
    req = urllib.request.Request(f"https://api.github.com/repos/{c['repo']}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {c['token']}", "User-Agent": "YukiInsights",
                                          "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if raw else (json.loads(data) if data else None)


def sync_from_github():
    """Download the latest data the daily GitHub run produced."""
    DATA.mkdir(parents=True, exist_ok=True)
    got = 0
    for name in SYNC_FILES:
        try:
            (DATA / name).write_bytes(gh(f"/contents/docs/data/{name}", raw=True))
            got += 1
        except Exception:
            pass
    if not got:
        raise RuntimeError("could not download data from GitHub")


def github_refresh():
    """Ask GitHub to run the collector now, wait for it, then download the results."""
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gh("/actions/workflows/daily.yml/dispatches", "POST", {"ref": "main"})
    for _ in range(80):
        time.sleep(10)
        runs = gh(f"/actions/workflows/daily.yml/runs?event=workflow_dispatch&per_page=1&created=>={since}")
        run = (runs.get("workflow_runs") or [None])[0]
        if run and run["status"] == "completed":
            if run["conclusion"] != "success":
                raise RuntimeError("the GitHub update failed; open Actions on GitHub to see why")
            break
    else:
        raise RuntimeError("GitHub took too long; try again in a few minutes")
    sync_from_github()


def refresh():
    with LOCK:
        if STATE["running"]:
            return False
        STATE.update(running=True, started=time.time(), finished=None, ok=None, log="")
    def work():
        if gh_config():
            try:
                github_refresh()
                STATE.update(running=False, finished=time.time(), ok=True, log="")
            except Exception as e:
                STATE.update(running=False, finished=time.time(), ok=False, log=str(e))
            return
        p = subprocess.run([sys.executable, str(ROOT / "radar" / "collect.py")],
                           cwd=ROOT, capture_output=True, text=True)
        STATE.update(running=False, finished=time.time(), ok=p.returncode == 0,
                     log=(p.stderr or "")[-4000:])
    threading.Thread(target=work, daemon=True).start()
    return True


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            return self._json({**STATE, "local": True, "github": (gh_config() or {}).get("repo")})
        return super().do_GET()

    def do_POST(self):
        # only accept requests from this app's own page
        origin = self.headers.get("Origin", "")
        if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
            return self._json({"error": "forbidden"}, 403)
        if self.path == "/api/refresh":
            return self._json({"started": refresh()})
        if self.path == "/api/github":
            n = int(self.headers.get("Content-Length", 0))
            post = json.loads(self.rfile.read(min(n, 5000)) or b"{}")
            repo = str(post.get("repo", "")).strip().removeprefix("https://github.com/").strip("/")
            token = str(post.get("token", "")).strip()
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not token:
                return self._json({"ok": False, "error": "Enter the project as name/project and paste the key."}, 400)
            GH_FILE.write_text(json.dumps({"repo": repo, "token": token}))
            try:
                sync_from_github()
            except Exception as e:
                GH_FILE.unlink(missing_ok=True)
                return self._json({"ok": False, "error": "GitHub didn't accept that. Check the project name and the key's permissions."}, 400)
            return self._json({"ok": True})
        if self.path == "/api/fiama":
            n = int(self.headers.get("Content-Length", 0))
            post = json.loads(self.rfile.read(min(n, 500000)) or b"{}")
            fields = ("id", "type", "date", "name", "product", "channels", "talent", "idea", "results", "link", "added")
            items = [{k: str(x.get(k, ""))[:2000] for k in fields} for x in post.get("items", []) if isinstance(x, dict)][:2000]
            text = json.dumps(items, ensure_ascii=False, indent=1)
            FIAMA.write_text(text)
            if (gh_config() or {}).get("share_fiama"):   # only if the GitHub project is private (see README)
                try:
                    try:
                        sha = gh("/contents/docs/data/fiama_activity.json").get("sha")
                    except Exception:
                        sha = None
                    body = {"message": "Update Fiama activity", "content": base64.b64encode(text.encode()).decode()}
                    if sha:
                        body["sha"] = sha
                    gh("/contents/docs/data/fiama_activity.json", "PUT", body)
                except Exception as e:
                    return self._json({"ok": True, "warning": "Saved on this laptop, but couldn't copy it to GitHub yet: " + str(e)[:120]})
            return self._json({"ok": True})
        if self.path == "/api/log":
            n = int(self.headers.get("Content-Length", 0))
            post = json.loads(self.rfile.read(min(n, 20000)) or b"{}")
            keep = {k: str(post.get(k, ""))[:1000] for k in
                    ("brand", "platform", "what", "link", "date", "creator", "metrics", "notes")}
            keep["logged_at"] = datetime.now(timezone.utc).isoformat()
            if gh_config():   # save it as a "post-log" issue in the private GitHub project
                labels = {"brand": "Brand", "platform": "Platform", "what": "What happened (one line)",
                          "link": "Link to the post", "date": "Date posted", "creator": "Creator handle (if any)",
                          "metrics": "Likes / comments / views (if visible)", "notes": "Notes / why it matters"}
                body = "\n\n".join(f"### {v}\n\n{keep[k] or '_No response_'}" for k, v in labels.items())
                try:
                    gh("/issues", "POST", {"title": "[Post] " + keep["what"][:80], "body": body, "labels": ["post-log"]})
                except Exception as e:
                    return self._json({"saved": False, "error": "Couldn't save to GitHub: " + str(e)[:120]}, 502)
                refresh()
                return self._json({"saved": True})
            items = json.loads(LOGGED.read_text()) if LOGGED.exists() else []
            items.append(keep)
            LOGGED.write_text(json.dumps(items, ensure_ascii=False, indent=1))
            refresh()
            return self._json({"saved": True})
        return self._json({"error": "not found"}, 404)


def free_port(preferred=8765):
    for port in range(preferred, preferred + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return 0


def main():
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(DOCS)))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    stats = DATA / "stats.json"
    if gh_config():
        try:
            sync_from_github()          # latest data from this morning's GitHub run
            print("  Downloaded the latest data from GitHub.")
        except Exception as e:
            print(f"  Couldn't reach GitHub ({e}). Showing the last saved data.")
    else:
        stale = not stats.exists() or (time.time() - stats.stat().st_mtime) > AUTO_REFRESH_HOURS * 3600
        if stale:
            refresh()
    print(f"\n  Yuki Insights is running at {url}\n  Keep this window open while you use it. Close it to stop.\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
