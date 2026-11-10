#!/usr/bin/env python3
"""verify.py - check an endpoint-analysis.json against the project it describes.

    python3 verify.py --input endpoint-analysis.json --root PROJECT [--json] [--out report.json]
                      [--require-verification] [--fix-drift]

What it checks (standard library only, read-only):

  1. Schema   the file has the shape described in references/endpoint-schema.md, and carries
              KEYS AND TYPES ONLY: no value / example / default keys, no values that look
              like an email, token, UUID, or secret.
  2. Evidence every cited file exists inside the project, the cited line exists, and the
              quoted snippet is found at that line (whitespace differences are ignored; a
              snippet that is 1-2 lines away is a warning with the correct line; anything
              else is an error).
  3. Keys     every request key and every response key appears in the code the item cites
              (DTO, model, serializer, handler, error handler).
  4. Status   every response status code appears (as a number or as a standard name such as
              BAD_REQUEST / badRequest / StatusBadRequest) near the code it cites.

It never opens .env files, never follows a path out of the project, and treats the analysis
as data. Exit code: 0 no errors, 1 errors found, 2 bad usage or unreadable input.
"""
import argparse
import http
import json
import os
import posixpath
import re
import sys

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
OUTCOMES = ("success", "client-error", "server-error", "degraded")
FIELD_TYPES = ("string", "integer", "number", "boolean", "object", "array", "null")
NODE_TYPES = ("client", "gateway", "middleware", "service", "cache", "database", "queue", "external-api", "storage")
DIRECTIONS = ("request", "response")
SHAPE_SOURCES = ("dto", "serializer", "global-error-handler", "inline", "framework-default")
V_STATUS = ("verified", "corrected", "unverified")
FORBIDDEN_KEYS = ("value", "values", "example", "examples", "sample", "default", "defaultvalue", "defaultValue")
CLIENT_NAME = "Client / User"
MAX_FILE = 2_000_000
MAX_SNIPPET_LINES = 3
DRIFT_OK = 2            # a snippet this many lines away is a warning with the right line; further is an error
STATUS_NEAR = 30        # lines around a cited location in which a status code must appear to count as near

VALUE_PATTERNS = (
    ("an email address", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9.\-]{2,}")),
    ("a JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}")),
    ("a UUID", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("a bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}")),
    ("a credential assignment", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[A-Za-z0-9/+_.\-]{8,}")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an AWS key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)
SNIPPET_SECRET = (
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token|authorization)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


# --------------------------------------------------------------------------- report
class Report:
    def __init__(self):
        self.problems = []
        self.checks = 0
        self.ok_targets = set()
        self.seen_targets = set()

    def check(self, target=None):
        self.checks += 1
        if target:
            self.seen_targets.add(target)

    def add(self, severity, check, path, message, **extra):
        p = {"severity": severity, "check": check, "path": path, "target": owner_target(path), "message": message}
        p.update(extra)
        self.problems.append(p)

    def error(self, check, path, message, **extra):
        self.add("error", check, path, message, **extra)

    def warn(self, check, path, message, **extra):
        self.add("warning", check, path, message, **extra)

    @property
    def errors(self):
        return [p for p in self.problems if p["severity"] == "error"]

    @property
    def warnings(self):
        return [p for p in self.problems if p["severity"] == "warning"]

    def as_dict(self):
        bad = {p["target"] for p in self.errors}
        warn = {p["target"] for p in self.warnings} - bad
        return {
            "summary": {"checks": self.checks, "errors": len(self.errors), "warnings": len(self.warnings),
                        "targetsWithErrors": sorted(bad), "targetsWithWarnings": sorted(warn)},
            "errors": self.errors, "warnings": self.warnings,
        }


def owner_target(path):
    """The verification target that a problem path belongs to (see endpoint-schema.md)."""
    if path.startswith("endpoint.handler"):
        return "endpoint.handler"
    m = re.match(r"^(scenarios\[\d+\]\.steps\[\d+\])", path)
    if m:
        return m.group(1)
    m = re.match(r"^(scenarios\[\d+\]\.request\.(?:body\.fields|pathParams|queryParams|headers)\[\d+\])", path)
    if m:
        return m.group(1)
    m = re.match(r"^(scenarios\[\d+\]\.response)", path)
    if m:
        return m.group(1)
    m = re.match(r"^(scenarios\[\d+\])", path)
    return m.group(1) if m else (path.split(".")[0] if path else "(file)")


def resolve(doc, path):
    """Follow a path like scenarios[0].steps[2] into the document; returns (found, value)."""
    cur = doc
    for m in re.finditer(r"([A-Za-z_]\w*)|\[(\d+)\]", path):
        try:
            cur = cur[m.group(1)] if m.group(1) is not None else cur[int(m.group(2))]
        except (KeyError, IndexError, TypeError):
            return False, None
    return True, cur


# --------------------------------------------------------------------------- files
class Files:
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.cache = {}

    def lines(self, rel):
        """Return (lines, error). Cached."""
        if rel in self.cache:
            return self.cache[rel]
        self.cache[rel] = self._read(rel)
        return self.cache[rel]

    def _read(self, rel):
        if not isinstance(rel, str) or not rel or "\x00" in rel:
            return None, "the file path is empty or invalid"
        r = rel.replace("\\", "/")
        if r.startswith("/") or re.match(r"^[A-Za-z]:", r):
            return None, "the file path must be relative to the project root"
        n = posixpath.normpath(r)
        if n == ".." or n.startswith("../"):
            return None, "the file path leaves the project"
        base = posixpath.basename(n).lower()
        if base.startswith(".env") and base not in (".env.example", ".env.sample", ".env_sample"):
            return None, "real .env files are never read or cited; cite .env.example if needed"
        full = os.path.realpath(os.path.join(self.root, n))
        try:
            if os.path.commonpath([full, self.root]) != self.root:
                return None, "the file path leaves the project"
        except ValueError:
            return None, "the file path leaves the project"
        if not os.path.isfile(full):
            return None, "the file does not exist"
        try:
            if os.path.getsize(full) > MAX_FILE:
                return None, "the file is larger than %d bytes and is not checked" % MAX_FILE
            with open(full, encoding="utf-8", errors="replace") as fh:
                return fh.read().splitlines(), None
        except OSError as e:
            return None, "the file cannot be read (%s)" % e.__class__.__name__


def squash(s):
    return re.sub(r"\s+", "", s)


def snippet_parts(snippet):
    return [squash(p) for p in re.split(r"\.\.\.|…", snippet) if squash(p)]


def snippet_at(lines, start, parts, nlines):
    """True when the snippet parts appear in order in the lines starting at index `start`."""
    if start < 0 or start >= len(lines):
        return False
    text = "".join(squash(l) for l in lines[start:start + nlines + 1])
    pos = 0
    for n, p in enumerate(parts):
        i = text.find(p, pos)
        if i < 0:
            return False
        if n == 0 and i >= len(squash(lines[start])):
            return False           # the snippet must begin on the cited line, not on a later one
        pos = i + len(p)
    return True


def find_snippet(lines, line, snippet):
    """Return ('exact'|'drift'|'far'|'missing', found_line)."""
    parts = snippet_parts(snippet)
    if not parts:
        return "missing", None
    nlines = snippet.count("\n") + 1
    base = line - 1
    if snippet_at(lines, base, parts, nlines):
        return "exact", line
    for d in (-1, 1, -2, 2):
        if snippet_at(lines, base + d, parts, nlines):
            return "drift", line + d
    for i in range(len(lines)):
        if snippet_at(lines, i, parts, nlines):
            return "far", i + 1
    return "missing", None


# --------------------------------------------------------------------------- schema
def is_evidence(d):
    return isinstance(d, dict) and "file" in d and "line" in d


def walk(obj, path=""):
    """Yield (path, value) for every node."""
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, (path + "." + str(k)) if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, "%s[%d]" % (path, i))


def check_schema(doc, rep):
    def need(cond, path, msg):
        rep.check()
        if not cond:
            rep.error("schema", path, msg)
        return cond

    def string(d, key, path, required=True):
        v = d.get(key) if isinstance(d, dict) else None
        if v is None and not required:
            return None
        need(isinstance(v, str) and v.strip(), "%s.%s" % (path, key) if path else key, "is required and must be a non-empty string")
        return v

    if not need(isinstance(doc, dict), "", "the file must hold one JSON object"):
        return
    ep = doc.get("endpoint")
    if need(isinstance(ep, dict), "endpoint", "is required and must be an object"):
        need(ep.get("method") in METHODS, "endpoint.method", "must be one of %s (capitals)" % ", ".join(METHODS))
        need(isinstance(ep.get("path"), str) and ep["path"].startswith("/"), "endpoint.path", "must be a string starting with /")
        string(ep, "service", "endpoint")
        string(ep, "summary", "endpoint", required=False)
        h = ep.get("handler")
        if need(isinstance(h, dict), "endpoint.handler", "is required: {file, line, symbol}"):
            string(h, "file", "endpoint.handler")
            need(isinstance(h.get("line"), int) and h["line"] >= 1, "endpoint.handler.line", "must be a line number (1 or more)")
            string(h, "symbol", "endpoint.handler")
    scs = doc.get("scenarios")
    if not need(isinstance(scs, list) and scs, "scenarios", "must be a non-empty list"):
        return
    seen_ids = set()
    for i, sc in enumerate(scs):
        sp = "scenarios[%d]" % i
        if not need(isinstance(sc, dict), sp, "must be an object"):
            continue
        sid = sc.get("id")
        need(isinstance(sid, str) and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", sid or ""), sp + ".id", "must be kebab-case (letters, digits, hyphens)")
        need(sid not in seen_ids, sp + ".id", "duplicate scenario id %r" % sid)
        seen_ids.add(sid)
        string(sc, "name", sp)
        string(sc, "trigger", sp)
        need(sc.get("outcome") in OUTCOMES, sp + ".outcome", "must be one of %s" % ", ".join(OUTCOMES))
        check_request(sc.get("request"), sp + ".request", rep, need)
        check_steps(sc.get("steps"), sp + ".steps", rep, need)
        check_response(sc, sp + ".response", rep, need)
    # values and keys that must never appear
    for path, v in walk(doc):
        last = path.split(".")[-1].split("[")[0]
        if last in FORBIDDEN_KEYS:
            rep.check()
            rep.error("no-values", path, "the key %r is not allowed: describe keys and types only, never values" % last)
    scan_values(doc, rep)


def check_field(f, path, rep, need, request, inherited_ev):
    if not need(isinstance(f, dict), path, "must be an object"):
        return
    need(isinstance(f.get("key"), str) and f.get("key"), path + ".key", "is required")
    need(f.get("type") in FIELD_TYPES, path + ".type", "must be one of %s" % ", ".join(FIELD_TYPES))
    need(isinstance(f.get("required"), bool), path + ".required", "must be true or false")
    val = f.get("validation", [])
    need(isinstance(val, list) and all(isinstance(x, str) for x in val), path + ".validation", "must be a list of strings")
    if request and val and not is_evidence(f.get("evidence")) and not inherited_ev:
        need(False, path + ".evidence", "is required for a field that has validation rules")
    ch = f.get("children")
    if ch is not None:
        if need(f.get("type") in ("object", "array"), path + ".children", "only objects and arrays have children"):
            need(isinstance(ch, list), path + ".children", "must be a list")
            for j, c in enumerate(ch if isinstance(ch, list) else []):
                check_field(c, "%s.children[%d]" % (path, j), rep, need, request, inherited_ev or is_evidence(f.get("evidence")))
    elif f.get("type") in ("object", "array"):
        rep.warn("schema", path, "an %s field should describe its structure in children" % f.get("type"))


def check_request(req, path, rep, need):
    if not need(isinstance(req, dict), path, "is required"):
        return
    for lst in ("headers", "pathParams", "queryParams"):
        v = req.get(lst)
        if need(isinstance(v, list), "%s.%s" % (path, lst), "must be a list (empty when none)"):
            for i, it in enumerate(v):
                ip = "%s.%s[%d]" % (path, lst, i)
                if need(isinstance(it, dict), ip, "must be an object"):
                    need(isinstance(it.get("name"), str) and it.get("name"), ip + ".name", "is required")
                    need("value" not in it, ip, "headers and parameters are listed by name only, never with a value")
                    if lst != "headers":
                        check_field(dict(it, key=it.get("name")), ip, rep, need, True, False)
    body = req.get("body")
    if body is not None and need(isinstance(body, dict), path + ".body", "must be null or an object"):
        need(isinstance(body.get("contentType"), str), path + ".body.contentType", "is required")
        flds = body.get("fields")
        if need(isinstance(flds, list), path + ".body.fields", "must be a list"):
            for i, f in enumerate(flds):
                fp = "%s.body.fields[%d]" % (path, i)
                check_field(f, fp, rep, need, True, False)
                if isinstance(f, dict):
                    need(is_evidence(f.get("evidence")), fp + ".evidence", "is required for every top-level request field")


def check_steps(steps, path, rep, need):
    if not need(isinstance(steps, list) and len(steps) >= 2, path, "must be a list of at least two steps"):
        return
    ids = set()
    for i, s in enumerate(steps):
        sp = "%s[%d]" % (path, i)
        if not need(isinstance(s, dict), sp, "must be an object"):
            continue
        need(isinstance(s.get("id"), str) and s.get("id"), sp + ".id", "is required")
        need(s.get("id") not in ids, sp + ".id", "duplicate step id %r" % s.get("id"))
        ids.add(s.get("id"))
        n = s.get("node")
        if need(isinstance(n, dict), sp + ".node", "is required: {name, type}"):
            need(isinstance(n.get("name"), str) and n.get("name"), sp + ".node.name", "is required")
            need(n.get("type") in NODE_TYPES, sp + ".node.type", "must be one of %s" % ", ".join(NODE_TYPES))
        need(s.get("direction") in DIRECTIONS, sp + ".direction", "must be request or response")
        need(isinstance(s.get("action"), str) and s.get("action"), sp + ".action", "is required")
        ev = s.get("evidence")
        if need(is_evidence(ev), sp + ".evidence", "is required: {file, line, snippet}"):
            need(isinstance(ev.get("snippet"), str) and ev.get("snippet").strip(), sp + ".evidence.snippet", "must quote the code")
            if isinstance(ev.get("snippet"), str):
                need(ev["snippet"].count("\n") + 1 <= MAX_SNIPPET_LINES, sp + ".evidence.snippet", "must be at most %d lines" % MAX_SNIPPET_LINES)
    first, last = steps[0], steps[-1]
    if isinstance(first, dict) and isinstance(last, dict):
        fn, ln = first.get("node") or {}, last.get("node") or {}
        need(first.get("direction") == "request" and fn.get("type") == "client", path + "[0]", "the journey starts with a request step at the Client / User node")
        need(last.get("direction") == "response" and ln.get("type") == "client", "%s[%d]" % (path, len(steps) - 1), "the journey ends with a response step back at the Client / User node")


def check_response(sc, path, rep, need):
    r = sc.get("response")
    if not need(isinstance(r, dict), path, "is required"):
        return
    st = r.get("status")
    need(isinstance(st, int) and not isinstance(st, bool) and 100 <= st <= 599, path + ".status", "must be an HTTP status code (100 to 599)")
    hd = r.get("headers", [])
    if need(isinstance(hd, list), path + ".headers", "must be a list"):
        for i, h in enumerate(hd):
            need(isinstance(h, dict) and h.get("name") and "value" not in h, "%s.headers[%d]" % (path, i), "list response headers by name only, never with a value")
    body = r.get("body")
    if body is not None and need(isinstance(body, dict) and isinstance(body.get("fields"), list), path + ".body", "must be null or {fields: [...]}"):
        for i, f in enumerate(body["fields"]):
            check_field(f, "%s.body.fields[%d]" % (path, i), rep, need, False, False)
    need(is_evidence(r.get("evidence")), path + ".evidence", "is required: where the response shape comes from")
    ev = r.get("evidence")
    if is_evidence(ev):
        need(isinstance(ev.get("snippet"), str) and ev["snippet"].strip(), path + ".evidence.snippet", "must quote the code")
    need(r.get("shapeSource") in SHAPE_SOURCES, path + ".shapeSource", "must be one of %s" % ", ".join(SHAPE_SOURCES))


def scan_values(doc, rep):
    """Strings must not carry data values or secrets; snippets must not contain literal secrets."""
    for path, v in walk(doc):
        if not isinstance(v, str):
            continue
        rep.check()
        is_snip = path.endswith(".snippet") or path.endswith(".evidence.snippet")
        is_file = path.endswith(".file")
        if is_snip:
            for rx in SNIPPET_SECRET:
                if rx.search(v):
                    rep.error("secret", path, "the snippet contains what looks like a literal secret; cut it at the secret with '...'")
                    break
            continue
        if is_file:
            continue
        for what, rx in VALUE_PATTERNS:
            if rx.search(v):
                rep.error("no-values", path, "the text contains %s; describe keys and types only, never values or secrets" % what)
                break


# --------------------------------------------------------------------------- evidence
PLACEHOLDER = "[removed: looked like a value or a secret]"


def scrub_text(s):
    """Replace anything that looks like a value or a secret in a free-text string."""
    if not isinstance(s, str):
        return s
    for _, rx in VALUE_PATTERNS:
        s = rx.sub(PLACEHOLDER, s)
    return s


def _parent_of(doc, path):
    toks = re.findall(r"([A-Za-z_]\w*)|\[(\d+)\]", path)
    if not toks:
        return None, None
    cur = doc
    try:
        for name, idx in toks[:-1]:
            cur = cur[name] if name else cur[int(idx)]
    except (KeyError, IndexError, TypeError):
        return None, None
    name, idx = toks[-1]
    return cur, (name if name else int(idx))


def redact_doc(doc, rep):
    """Remove what the no-values and secret checks flagged: forbidden keys are deleted and flagged
    strings are replaced. Returns the set of verification targets that were touched."""
    touched = set()
    for p in list(rep.errors):
        if p["check"] not in ("no-values", "secret"):
            continue
        parent, key = _parent_of(doc, p["path"])
        if parent is None:
            continue
        try:
            if isinstance(parent, dict) and key in parent:
                if key in FORBIDDEN_KEYS:
                    del parent[key]                     # a value / example / default key is removed outright
                elif isinstance(parent[key], str):
                    parent[key] = scrub_text(parent[key]) if p["check"] == "no-values" and scrub_text(parent[key]) != parent[key] else PLACEHOLDER
                else:
                    del parent[key]
                touched.add(p["target"])
            elif isinstance(parent, list) and isinstance(key, int) and key < len(parent) and isinstance(parent[key], str):
                parent[key] = PLACEHOLDER
                touched.add(p["target"])
        except (TypeError, KeyError):
            continue
    return touched


def evidence_items(doc):
    """Yield (path, evidence dict, has_snippet)."""
    for path, v in walk(doc):
        if is_evidence(v):
            yield path, v


def check_evidence(doc, files, rep, fix_drift=False):
    for path, ev in evidence_items(doc):
        tgt = owner_target(path)
        rep.check(tgt)
        rel = ev.get("file")
        line = ev.get("line")
        lines, err = files.lines(rel)
        if err:
            rep.error("evidence-file", path, "%s: %s" % (rel, err))
            continue
        if not (isinstance(line, int) and not isinstance(line, bool) and line >= 1):
            rep.error("evidence-line", path, "line must be a number (1 or more) in %s" % rel)
            continue
        if line > len(lines):
            rep.error("evidence-line", path, "line %d does not exist: %s has %d lines" % (line, rel, len(lines)))
            continue
        snippet = ev.get("snippet")
        if snippet is None:
            sym = ev.get("symbol")
            if sym:
                window = "\n".join(lines[max(0, line - 4):line + 3])
                short = re.split(r"[.#:]+", sym.strip())[-1]      # Class.method, Class#method, Class::method
                if sym not in window and short not in window:
                    rep.warn("evidence-symbol", path, "symbol %r was not found within 3 lines of %s:%d" % (sym, rel, line))
            continue
        if not isinstance(snippet, str) or not snippet.strip():
            continue
        status, found = find_snippet(lines, line, snippet)
        if status == "exact":
            continue
        if status == "drift":
            rep.warn("evidence-snippet", path, "the snippet is at line %d of %s, not line %d" % (found, rel, line), fixLine=found, file=rel)
        elif status == "far":
            rep.error("evidence-snippet", path, "the snippet is not at line %d of %s; the same code is at line %d" % (line, rel, found), fixLine=found, file=rel)
        else:
            rep.error("evidence-snippet", path, "the snippet does not appear in %s near line %d (line %d reads: %r)" % (rel, line, line, lines[line - 1].strip()[:100]), file=rel)


# --------------------------------------------------------------------------- keys
def norm_key(s):
    return re.sub(r"[_\-$]", "", s.lower())


class TokenIndex:
    def __init__(self, files):
        self.files = files
        self.cache = {}

    def tokens(self, rel):
        if rel not in self.cache:
            lines, err = self.files.lines(rel)
            toks = set()
            if not err:
                for m in re.finditer(r"[A-Za-z0-9_$\-]+", "\n".join(lines)):
                    toks.add(norm_key(m.group(0)))
            self.cache[rel] = toks
        return self.cache[rel]

    def has_key(self, rel, key):
        k = norm_key(key)
        if not k:
            return True
        toks = self.tokens(rel)
        if k in toks:
            return True
        return len(k) >= 4 and any(k in t for t in toks)


def iter_fields(fields, path):
    for i, f in enumerate(fields if isinstance(fields, list) else []):
        if not isinstance(f, dict):
            continue
        p = "%s[%d]" % (path, i)
        yield p, f
        if isinstance(f.get("children"), list):
            yield from iter_fields(f["children"], p + ".children")


def check_keys(doc, files, rep):
    idx = TokenIndex(files)
    for si, sc in enumerate(doc.get("scenarios") or []):
        if not isinstance(sc, dict):
            continue
        sp = "scenarios[%d]" % si
        req = sc.get("request") or {}
        # request keys: the file cited on the field, or the nearest parent that cites one
        groups = []
        for lst in ("pathParams", "queryParams"):
            for i, p in enumerate(req.get(lst) or []):
                if isinstance(p, dict):
                    ev = p.get("evidence")
                    rep.check("%s.request.%s[%d]" % (sp, lst, i))
                    if is_evidence(ev) and isinstance(p.get("name"), str) and not idx.has_key(ev.get("file"), p["name"]) and not files.lines(ev.get("file"))[1]:
                        rep.error("key-not-in-code", "%s.request.%s[%d]" % (sp, lst, i), "parameter %r does not appear in the cited file %s" % (p["name"], ev.get("file")))
        body = req.get("body")
        if isinstance(body, dict):
            base = "%s.request.body.fields" % sp
            for i, f in enumerate(body.get("fields") or []):
                walk_request_field(f, "%s[%d]" % (base, i), None, idx, files, rep)
        # response keys: the response evidence file plus the files cited by response-direction steps
        r = sc.get("response") or {}
        rb = r.get("body")
        if isinstance(rb, dict):
            cited = []
            ev = r.get("evidence")
            if is_evidence(ev):
                cited.append(ev.get("file"))
            for s in sc.get("steps") or []:
                if isinstance(s, dict) and s.get("direction") == "response" and is_evidence(s.get("evidence")):
                    cited.append(s["evidence"].get("file"))
            cited = [c for c in dict.fromkeys(cited) if isinstance(c, str) and not files.lines(c)[1]]
            rpath = sp + ".response"
            rep.check(rpath)
            if r.get("shapeSource") == "framework-default":
                rep.warn("key-not-in-code", rpath, "the response shape is a framework default that is not in the repo; its keys cannot be checked against code")
                continue
            if not cited:
                rep.error("key-not-in-code", rpath, "the response keys cannot be checked: none of the cited files could be read")
                continue
            for p, f in iter_fields(rb.get("fields"), rpath + ".body.fields"):
                k = f.get("key")
                if not isinstance(k, str) or k == "[]":
                    continue
                if not any(idx.has_key(c, k) for c in cited):
                    rep.error("key-not-in-code", p, "response key %r was not found in the cited code (%s)" % (k, ", ".join(cited)))


def walk_request_field(f, path, parent_file, idx, files, rep):
    if not isinstance(f, dict):
        return
    ev = f.get("evidence")
    rel = ev.get("file") if is_evidence(ev) else parent_file
    k = f.get("key")
    rep.check(owner_target(path))
    if isinstance(k, str) and k != "[]":
        if rel is None:
            rep.warn("key-not-in-code", path, "request key %r cites no file, so it cannot be checked" % k)
        elif not files.lines(rel)[1] and not idx.has_key(rel, k):
            rep.error("key-not-in-code", path, "request key %r was not found in the cited file %s" % (k, rel))
    for i, c in enumerate(f.get("children") or []):
        walk_request_field(c, "%s.children[%d]" % (path, i), rel, idx, files, rep)


# --------------------------------------------------------------------------- status
def status_variants(code):
    """Textual forms of a status code: the number and the standard names."""
    out = []
    try:
        name = http.HTTPStatus(code).name          # BAD_REQUEST
    except ValueError:
        return out
    if name in ("OK", "FOUND", "CONTINUE", "ACCEPTED", "GONE"):
        return out if name not in ("ACCEPTED",) else ["ACCEPTED"]
    parts = name.split("_")
    camel = "".join(p.capitalize() for p in parts)
    out += [name, camel, camel[0].lower() + camel[1:], name.lower()]
    return out


def status_in_text(code, text):
    if re.search(r"(?<!\d)%d(?!\d)" % code, text):
        return True
    if code == 200 and re.search(r"(?:\.ok\s*\(|Status\.OK\b|HttpStatus\.OK\b|StatusOK\b|\bok\s*\()", text):
        return True
    return any(v in text for v in status_variants(code))


def check_status(doc, files, rep):
    for si, sc in enumerate(doc.get("scenarios") or []):
        if not isinstance(sc, dict):
            continue
        r = sc.get("response")
        if not isinstance(r, dict) or not isinstance(r.get("status"), int):
            continue
        path = "scenarios[%d].response" % si
        code = r["status"]
        rep.check(path)
        cited = []
        ev = r.get("evidence")
        if is_evidence(ev):
            cited.append((ev.get("file"), ev.get("line")))
        for s in sc.get("steps") or []:
            if isinstance(s, dict) and s.get("direction") == "response" and is_evidence(s.get("evidence")):
                cited.append((s["evidence"].get("file"), s["evidence"].get("line")))
        if r.get("shapeSource") == "framework-default":
            rep.warn("status-in-code", path + ".status", "status %d comes from a framework default that is not in the repo, so it cannot be checked" % code)
            continue
        near_hit = any_hit = False
        readable = []
        for f, line in dict.fromkeys(cited):
            lines, err = files.lines(f)
            if err or not isinstance(line, int):
                continue
            readable.append(f)
            lo = max(0, line - 1 - STATUS_NEAR)
            if status_in_text(code, "\n".join(lines[lo:line + STATUS_NEAR])):
                near_hit = True
            if status_in_text(code, "\n".join(lines)):
                any_hit = True
        if near_hit:
            continue
        if not readable:
            rep.error("status-in-code", path + ".status", "status %d cannot be checked: none of the cited files could be read" % code)
        elif any_hit:
            rep.warn("status-in-code", path + ".status", "status %d appears in the cited file but not near the cited line" % code)
        elif code == 200:
            rep.warn("status-in-code", path + ".status", "status 200 is not written in the cited code; if it is the framework's implicit default say so with shapeSource framework-default")
        else:
            rep.error("status-in-code", path + ".status", "status %d does not appear (as a number or a standard name) in the cited code: %s" % (code, ", ".join(readable)))


# --------------------------------------------------------------------------- verification block
def coverage_targets(doc):
    """Every item that the verifier pass must give a status (endpoint-schema.md > verification)."""
    out = ["endpoint.handler"]
    for si, sc in enumerate(doc.get("scenarios") or []):
        if not isinstance(sc, dict):
            continue
        sp = "scenarios[%d]" % si
        for j, _ in enumerate(sc.get("steps") or []):
            out.append("%s.steps[%d]" % (sp, j))
        req = sc.get("request") or {}
        body = req.get("body")
        if isinstance(body, dict):
            for k, _ in enumerate(body.get("fields") or []):
                out.append("%s.request.body.fields[%d]" % (sp, k))
        for lst in ("pathParams", "queryParams"):
            for k, _ in enumerate(req.get(lst) or []):
                out.append("%s.request.%s[%d]" % (sp, lst, k))
        out.append(sp + ".response")
    return out


def check_verification(doc, rep, require):
    v = doc.get("verification")
    if v is None:
        if require:
            rep.error("verification", "verification", "the verification block is missing")
        return
    rep.check()
    if not isinstance(v, dict) or not isinstance(v.get("items"), list) or not isinstance(v.get("summary"), dict):
        rep.error("verification", "verification", "must be {summary, items}")
        return
    counts = {s: 0 for s in V_STATUS}
    seen = set()
    for i, it in enumerate(v["items"]):
        ip = "verification.items[%d]" % i
        rep.check()
        if not isinstance(it, dict):
            rep.error("verification", ip, "must be an object")
            continue
        st, tg = it.get("status"), it.get("target")
        if st not in V_STATUS:
            rep.error("verification", ip + ".status", "must be one of %s" % ", ".join(V_STATUS))
        else:
            counts[st] += 1
        if not (isinstance(it.get("reason"), str) and it["reason"].strip()):
            rep.error("verification", ip + ".reason", "is required")
        if st == "corrected" and not (it.get("was") and it.get("now")):
            rep.error("verification", ip, "a corrected item needs was and now")
        found, _ = resolve(doc, tg) if isinstance(tg, str) else (False, None)
        if not found:
            rep.error("verification", ip + ".target", "points at nothing: %r" % tg)
        seen.add(tg)
    for s in V_STATUS:
        if v["summary"].get(s) != counts[s]:
            rep.error("verification", "verification.summary.%s" % s, "says %r but the items count %d" % (v["summary"].get(s), counts[s]))
    for t in coverage_targets(doc):
        if t not in seen and require:
            rep.error("verification", "verification.items", "no verification item for %s" % t)


# --------------------------------------------------------------------------- drive
def verify_doc(doc, root, require_verification=False):
    rep = Report()
    check_schema(doc, rep)
    if isinstance(doc, dict) and isinstance(doc.get("scenarios"), list):
        files = Files(root)
        check_evidence(doc, files, rep)
        check_keys(doc, files, rep)
        check_status(doc, files, rep)
        check_verification(doc, rep, require_verification)
    return rep


def apply_line_fixes(doc, rep):
    """Correct cited line numbers where the snippet was found 1-2 lines away. Returns how many were changed."""
    n = 0
    for p in rep.problems:
        fl = p.get("fixLine")
        if fl and p["check"] == "evidence-snippet" and p["severity"] == "warning":
            ok, ev = resolve(doc, p["path"])
            if ok and is_evidence(ev):
                ev["line"] = fl
                n += 1
    return n


def format_text(rep, source, root, limit=200):
    d = rep.as_dict()["summary"]
    out = ["verify.py report for %s (project: %s)" % (source, root),
           "checks run: %d   errors: %d   warnings: %d" % (d["checks"], d["errors"], d["warnings"])]
    for title, items in (("ERRORS", rep.errors), ("WARNINGS", rep.warnings)):
        if items:
            out.append("")
            out.append(title)
            for i, p in enumerate(items[:limit], 1):
                out.append("%3d. %s [%s] %s" % (i, p["path"] or "(file)", p["check"], p["message"]))
            if len(items) > limit:
                out.append("     ... %d more" % (len(items) - limit))
    if not rep.problems:
        out.append("")
        out.append("No problems found.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check an endpoint-analysis.json against the project.")
    ap.add_argument("--input", required=True, help="endpoint-analysis.json")
    ap.add_argument("--root", default=".", help="project folder (default: current folder)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--out", help="also write the JSON report to this file")
    ap.add_argument("--require-verification", action="store_true", help="the verification block must exist and cover every item")
    ap.add_argument("--fix-drift", action="store_true", help="rewrite line numbers that are 1-2 lines off (changes the input file)")
    args = ap.parse_args(argv)
    try:
        if os.path.getsize(args.input) > 5_000_000:
            print("input is larger than 5 MB", file=sys.stderr)
            return 2
        with open(args.input, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        print("cannot read %s: %s" % (args.input, e), file=sys.stderr)
        return 2
    if not os.path.isdir(args.root):
        print("project folder not found: %s" % args.root, file=sys.stderr)
        return 2
    rep = verify_doc(doc, args.root, args.require_verification)
    if args.fix_drift and apply_line_fixes(doc, rep):
        with open(args.input, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
        rep = verify_doc(doc, args.root, args.require_verification)
    data = rep.as_dict()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    print(json.dumps(data, indent=2, ensure_ascii=False) if args.json else format_text(rep, args.input, os.path.abspath(args.root)))
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
