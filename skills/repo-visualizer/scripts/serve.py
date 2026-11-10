#!/usr/bin/env python3
"""serve.py - a local server for the on-demand endpoint viewer (standard library only).

    python3 serve.py --root PROJECT [--port 0] [--timeout 300] [--open]

The user types an endpoint in the viewer. The server runs, one job at a time:
locate -> analyze -> verify -> (re-run once if the problems are major) -> result,
by calling endpoint_pipeline.py. Claude Code runs headless in the project folder with
read-only tools only (Read, Grep, Glob; see claude_headless.py).

Security
  * Binds to 127.0.0.1 only, on a random port unless --port is given.
  * A random session token is printed once (in the URL fragment, which the browser never
    sends to a server). Every API call must carry it in the X-Session-Token header.
  * The Host header must be 127.0.0.1 or localhost with this port (blocks DNS rebinding); the
    Origin and Sec-Fetch-Site headers, when present, must be this same origin. No CORS headers
    are ever sent. POST bodies must be JSON and at most 4 KB.
  * The viewer page is served with a strict Content-Security-Policy and no external resources.
  * Subprocesses (git, claude) are started with argument lists, never through a shell.
  * Nothing is written into the project. Results are cached outside it (see below).

API (all but GET / need the token)
  POST /api/analyze  {"endpoint": "POST /api/login", "refresh": false, "pick": {"file": "...", "line": 12}}
                      -> 202 {"job": {...}}   (200 with the finished job when the result is cached)
  GET  /api/jobs/<id> -> {"id", "stage", "status", "queuePosition", "result"?, ...}
  GET  /api/health    -> {"ok": true, "queued": n, "running": bool}

Stages: queued, locating, analyzing, verifying, retrying, done. When stage is "done", `status` is
done, done_with_problems, not_located (result.locate has the candidates or the closest matches) or error.

Cache: results are cached by project + endpoint + the code's fingerprint (git commit plus any
uncommitted changes; without git, file sizes and modification times), in a folder outside the
project. Asking again is instant unless the code changed. "refresh": true re-analyzes.

Limits: each headless Claude run times out after --timeout seconds (default 300). A job can use up
to four runs (analysis and verification, twice). One job runs at a time; up to 20 wait in the queue.
"""
import argparse
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claude_headless  # noqa: E402
import endpoint_pipeline  # noqa: E402
import locate  # noqa: E402

VIEWER = os.path.join(os.path.dirname(HERE), "assets", "endpoint-viewer.html")
MAX_BODY = 4096
MAX_QUEUE = 20
KEEP_JOBS = 60
KEEP_CACHE = 200
CACHE_VERSION = "1"
JOB_ID_RE = re.compile(r"^/api/jobs/([0-9a-f]{16})$")
FINISHED_OK = ("done", "done_with_problems")


# --------------------------------------------------------------------------- fingerprint and cache
def _git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, timeout=30,
                           env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, b""


def code_fingerprint(root):
    """A string that changes when the code changes: git HEAD plus uncommitted changes, else file stats."""
    rc, head = _git(root, "rev-parse", "HEAD")
    if rc == 0 and head.strip():
        rc2, status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if rc2 == 0:
            if not status.strip():
                return "git:" + head.decode("ascii", "replace").strip()
            h = hashlib.sha256(status)
            h.update(_git(root, "diff", "HEAD")[1])
            for entry in status.split(b"\0"):
                if entry.startswith(b"?? "):
                    p = os.path.join(root, entry[3:].decode("utf-8", "replace"))
                    try:
                        st = os.stat(p)
                        h.update(("%s:%d:%d" % (entry[3:].decode("utf-8", "replace"), st.st_size, st.st_mtime_ns)).encode())
                    except OSError:
                        pass
            return "git:%s:dirty:%s" % (head.decode("ascii", "replace").strip(), h.hexdigest()[:16])
    h = hashlib.sha256()
    n = 0
    exts = locate.SRC_EXT | locate.CFG_EXT
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in locate.SKIP_DIRS and not d.startswith("."))
        for f in sorted(fn):
            if os.path.splitext(f)[1].lower() in exts:
                try:
                    st = os.stat(os.path.join(dp, f))
                except OSError:
                    continue
                h.update(("%s:%d:%d\n" % (os.path.relpath(os.path.join(dp, f), root), st.st_size, st.st_mtime_ns)).encode("utf-8", "replace"))
                n += 1
                if n > 20000:
                    break
    return "files:%d:%s" % (n, h.hexdigest()[:24])


def clean_query(raw):
    """Validate what the user typed. Returns (clean text, cache key text, None) or (None, None, reason).

    The clean text is rebuilt from the parsed method and path or name, so characters that are not part of
    a path or a name never reach a job, a prompt or the cache."""
    q, why = locate.parse_query(raw)
    if q is None:
        return None, None, why
    target = q.get("path") or q.get("name")
    clean = ("%s %s" % (q["method"], target)) if q["method"] else target
    return clean, "%s %s" % (q["method"] or "*", target.lower()), None


def default_cache_dir(root):
    base = os.environ.get("REPO_VISUALIZER_CACHE")
    if not base:
        base = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache"), "repo-visualizer")
    return os.path.join(base, hashlib.sha256(os.path.realpath(root).encode("utf-8", "replace")).hexdigest()[:16])


class Cache:
    def __init__(self, directory):
        self.dir = directory

    def _path(self, key):
        return os.path.join(self.dir, key + ".json")

    @staticmethod
    def key(root, norm_query, pick, fingerprint):
        raw = "\0".join([CACHE_VERSION, os.path.realpath(root), norm_query, json.dumps(pick, sort_keys=True), fingerprint])
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()

    def get(self, key):
        try:
            with open(self._path(key), encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict) and d.get("version") == CACHE_VERSION and isinstance(d.get("result"), dict):
                return d
        except (OSError, ValueError):
            pass
        return None

    def put(self, key, norm_query, fingerprint, result):
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self._path(key) + ".%d.tmp" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": CACHE_VERSION, "query": norm_query, "fingerprint": fingerprint,
                           "created": time.time(), "result": result}, fh, ensure_ascii=False)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self._path(key))
            files = sorted((os.path.getmtime(os.path.join(self.dir, f)), f) for f in os.listdir(self.dir) if f.endswith(".json"))
            for _, f in files[:-KEEP_CACHE]:
                os.remove(os.path.join(self.dir, f))
        except OSError:
            pass          # the cache is an optimisation; a failure to write it is not an error


# --------------------------------------------------------------------------- jobs
class Job:
    def __init__(self, endpoint, norm, pick, refresh):
        self.id = secrets.token_hex(8)
        self.endpoint = endpoint
        self.norm = norm
        self.pick = pick
        self.refresh = refresh
        self.created = time.time()
        self.stage = "queued"
        self.detail = None
        self.attempt = 0
        self.status = None
        self.result = None
        self.cached = False


class Manager:
    """One worker thread, one job at a time; the rest wait in a queue."""

    def __init__(self, root, cache, timeout=300, runner=None, model=None, verifier_model=None, claude_bin=None, max_budget=None):
        self.root = os.path.abspath(root)
        self.cache = cache
        self.timeout = timeout
        self.runner = runner
        self.model, self.verifier_model, self.claude_bin, self.max_budget = model, verifier_model, claude_bin, max_budget
        self.jobs = OrderedDict()
        self.pending = []
        self.running = None
        self.lock = threading.Lock()
        self.wake = threading.Condition(self.lock)
        self.stopped = False
        self.worker = threading.Thread(target=self._loop, daemon=True, name="repo-visualizer-worker")
        self.worker.start()

    # ---- submit and view
    def submit(self, endpoint, refresh=False, pick=None):
        """Returns (job, error_code, error_message)."""
        clean, norm, why = clean_query(endpoint)
        if clean is None:
            return None, 400, why
        job = Job(clean, norm, pick, refresh)
        if not refresh:
            fp = code_fingerprint(self.root)
            hit = self.cache.get(Cache.key(self.root, norm, pick, fp))
            if hit:
                job.cached = True
                job.stage, job.status, job.result = "done", hit["result"].get("status", "done"), hit["result"]
                with self.lock:
                    self._remember(job)
                return job, None, None
        with self.lock:
            if len(self.pending) >= MAX_QUEUE:
                return None, 429, "the queue is full; try again in a moment"
            self._remember(job)
            self.pending.append(job.id)
            self.wake.notify()
        return job, None, None

    def _remember(self, job):
        self.jobs[job.id] = job
        done = [j for j in self.jobs.values() if j.stage == "done"]
        for old in done[:-KEEP_JOBS]:
            self.jobs.pop(old.id, None)

    def view(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            pos = (self.pending.index(job.id) + 1) if job.id in self.pending else None
            v = {"id": job.id, "endpoint": job.endpoint, "stage": job.stage, "detail": job.detail, "attempt": job.attempt,
                 "queuePosition": pos, "status": job.status, "cached": job.cached, "refresh": job.refresh,
                 "createdAt": job.created, "stages": ["locating", "analyzing", "verifying", "retrying"]}
            if job.stage == "done":
                v["result"] = job.result
            return v

    def health(self):
        with self.lock:
            return {"ok": True, "queued": len(self.pending), "running": self.running is not None}

    def stop(self):
        with self.lock:
            self.stopped = True
            self.wake.notify_all()

    # ---- worker
    def _loop(self):
        while True:
            with self.lock:
                while not self.pending and not self.stopped:
                    self.wake.wait()
                if self.stopped:
                    return
                job = self.jobs.get(self.pending.pop(0))
                if job is None:
                    continue
                self.running = job
                job.stage = "locating"
            try:
                self._execute(job)
            except Exception as e:          # never let one job kill the worker
                self._finish(job, "error", {"status": "error", "message": "internal error (%s)" % e.__class__.__name__})
            finally:
                with self.lock:
                    self.running = None

    def _progress(self, job):
        def cb(stage, detail=None):
            with self.lock:
                if stage == "located":
                    job.detail = detail
                elif stage in ("locating", "analyzing", "verifying", "retrying"):
                    job.stage = stage
                    if stage in ("analyzing", "verifying"):
                        job.attempt = detail or job.attempt
                    job.detail = detail if stage == "retrying" else job.detail
        return cb

    def _finish(self, job, status, result):
        with self.lock:
            job.status, job.result, job.stage = status, result, "done"

    def _execute(self, job):
        fp_start = code_fingerprint(self.root)
        pick = job.pick or {}
        args = argparse.Namespace(root=self.root, query=job.endpoint, out=None, pick_file=pick.get("file"), pick_line=pick.get("line"),
                            model=self.model, verifier_model=self.verifier_model, claude_bin=self.claude_bin, timeout=self.timeout,
                            max_budget_usd=self.max_budget, no_rerun=False, no_claude=False, analysis_in=None)
        code, result = endpoint_pipeline.pipeline(args, runner=self.runner, progress=self._progress(job), keep=True)
        status = result.get("status", "error")
        if status == "not_located":
            self._finish(job, "not_located", result)
            return
        if status == "error":
            self._finish(job, "error", result)
            return
        # cache only when the code did not change while the analysis ran
        if status in FINISHED_OK and code_fingerprint(self.root) == fp_start:
            self.cache.put(Cache.key(self.root, job.norm, job.pick, fp_start), job.norm, fp_start, result)
        self._finish(job, status, result)


# --------------------------------------------------------------------------- http
class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        pass                    # a dropped or timed-out connection is routine; do not print tracebacks

def make_handler(manager, token, port_getter):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "repo-visualizer"
        sys_version = ""
        timeout = 20            # an idle or half-open connection is dropped instead of holding a thread

        def log_message(self, fmt, *args):      # no access log: nothing sensitive goes to the console
            pass

        # ---- helpers
        def _send(self, code, body, ctype, extra=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def _err(self, code, msg):
            self._json(code, {"error": msg})

        def _allowed_hosts(self):
            p = port_getter()
            return ("127.0.0.1:%d" % p, "localhost:%d" % p)

        def _same_origin(self):
            o = self.headers.get("Origin")
            if o is not None and o not in tuple("http://" + h for h in self._allowed_hosts()):
                return False
            sf = self.headers.get("Sec-Fetch-Site")
            return sf is None or sf in ("same-origin", "none")

        def _guard(self, api):
            """Host, origin and token checks. Returns True when the request may continue."""
            if (self.headers.get("Host") or "").lower() not in self._allowed_hosts():
                self._err(403, "bad Host header")
                return False
            if api:
                if not self._same_origin():
                    self._err(403, "cross-origin requests are not allowed")
                    return False
                tok = self.headers.get("X-Session-Token") or ""
                if not tok or not hmac.compare_digest(tok.encode("utf-8", "replace"), token.encode("utf-8")):
                    self._err(401, "a valid session token is required")
                    return False
            return True

        def _drain(self, n, cap=1_000_000):
            """Read and discard an unwanted request body (bounded) so closing the connection does not reset it."""
            left = min(max(n, 0), cap)
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)

        def _path(self):
            return self.path.split("?", 1)[0].split("#", 1)[0]

        # ---- verbs
        def do_GET(self):
            path = self._path()
            if path in ("/", "/index.html"):
                if not self._guard(False):
                    return
                try:
                    with open(VIEWER, encoding="utf-8") as fh:
                        page = fh.read()
                except OSError:
                    return self._err(500, "the viewer page is missing")
                nonce = secrets.token_urlsafe(16)
                csp = ("default-src 'none'; script-src 'nonce-%s'; style-src 'nonce-%s'; connect-src 'self'; img-src data:; "
                       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'" % (nonce, nonce))
                return self._send(200, page.replace("{{NONCE}}", nonce), "text/html; charset=utf-8", {"Content-Security-Policy": csp})
            if path == "/api/health":
                if self._guard(True):
                    self._json(200, manager.health())
                return
            m = JOB_ID_RE.match(path)
            if m:
                if not self._guard(True):
                    return
                v = manager.view(m.group(1))
                return self._json(200, v) if v else self._err(404, "unknown job")
            if path.startswith("/api/"):
                if self._guard(True):
                    self._err(404, "unknown endpoint")
                return
            if self._guard(False):
                self._err(404, "not found")

        def do_POST(self):
            path = self._path()
            if not self._guard(path.startswith("/api/")):
                return
            if path != "/api/analyze":
                return self._err(404, "unknown endpoint")
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                try:
                    self._drain(int(self.headers.get("Content-Length") or 0))
                except ValueError:
                    pass
                return self._err(415, "send JSON (Content-Type: application/json)")
            try:
                n = int(self.headers.get("Content-Length") or "")
            except ValueError:
                return self._err(411, "Content-Length is required")
            if n < 0 or n > MAX_BODY:
                self._drain(n)
                return self._err(413, "the request body is too large (limit %d bytes)" % MAX_BODY)
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._err(400, "the body is not valid JSON")
            if not isinstance(body, dict) or not isinstance(body.get("endpoint"), str):
                return self._err(400, 'expected {"endpoint": "<text>"}')
            refresh = body.get("refresh", False)
            pick = body.get("pick")
            if not isinstance(refresh, bool):
                return self._err(400, "refresh must be true or false")
            if pick is not None:
                if not (isinstance(pick, dict) and isinstance(pick.get("file"), str) and 0 < len(pick["file"]) <= 400
                        and (pick.get("line") is None or (isinstance(pick["line"], int) and not isinstance(pick["line"], bool)))):
                    return self._err(400, 'pick must be {"file": "<path>", "line": <number>}')
                pick = {"file": pick["file"], "line": pick.get("line")}
            job, code, msg = manager.submit(body["endpoint"], refresh, pick)
            if job is None:
                return self._err(code, msg)
            self._json(200 if job.stage == "done" else 202, {"job": manager.view(job.id)})

        def _no(self):
            if self._guard(self._path().startswith("/api/")):
                self._err(405, "method not allowed")

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no

    return Handler


def make_server(root, port=0, timeout=300, cache_dir=None, runner=None, model=None, verifier_model=None, claude_bin=None, max_budget=None):
    """Create the server (not started). Returns (httpd, manager, token)."""
    root = os.path.abspath(root)
    token = secrets.token_urlsafe(32)
    cache = Cache(cache_dir or default_cache_dir(root))
    manager = Manager(root, cache, timeout=timeout, runner=runner, model=model, verifier_model=verifier_model,
                      claude_bin=claude_bin, max_budget=max_budget)
    holder = {}
    handler = make_handler(manager, token, lambda: holder["httpd"].server_address[1])
    httpd = Server(("127.0.0.1", port), handler)
    holder["httpd"] = httpd
    return httpd, manager, token


def main(argv=None):
    ap = argparse.ArgumentParser(description="Serve the on-demand endpoint viewer on 127.0.0.1.")
    ap.add_argument("--root", default=".", help="project folder (default: current folder)")
    ap.add_argument("--port", type=int, default=0, help="port (default: random)")
    ap.add_argument("--timeout", type=int, default=300, help="seconds each headless Claude run may take (default 300)")
    ap.add_argument("--cache-dir", help="where results are cached (default: a folder outside the project)")
    ap.add_argument("--model", help="model for the analysis (and verifier) runs")
    ap.add_argument("--verifier-model", help="model for the verifier run")
    ap.add_argument("--claude-bin", help="path to the claude program")
    ap.add_argument("--max-budget-usd", type=float, help="spending cap for each headless run")
    ap.add_argument("--open", action="store_true", help="open the viewer in the default browser")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.root):
        print(json.dumps({"error": "project folder not found"}))
        return 2
    httpd, manager, token = make_server(args.root, args.port, args.timeout, args.cache_dir, None, args.model, args.verifier_model,
                                        args.claude_bin, args.max_budget_usd)
    port = httpd.server_address[1]
    url = "http://127.0.0.1:%d/#token=%s" % (port, token)
    if not claude_headless.find_claude(args.claude_bin):
        print("warning: the claude program was not found; analysis will fail until it is on PATH or --claude-bin / CLAUDE_BIN is set", file=sys.stderr)
    print(json.dumps({"url": url, "port": port, "token": token, "root": manager.root, "cacheDir": manager.cache.dir}), flush=True)
    print("Open %s (Ctrl+C to stop)" % url, file=sys.stderr, flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
