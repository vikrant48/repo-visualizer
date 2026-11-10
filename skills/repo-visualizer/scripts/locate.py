#!/usr/bin/env python3
"""locate.py - find the backend route a user typed.

The user can type "POST /api/login", "/api/login", "login" or "getPHHireCount".
This script validates that text, scans the project's route definitions (Spring,
JAX-RS, Express, NestJS, FastAPI, Flask, Django/DRF, Go), adds the prefixes from
controllers, routers, mounts, context paths and gateways, and prints ONE JSON
object saying whether the route was found once, several times, or not at all.

    python3 locate.py --root PROJECT --query "POST /api/login"

The query is DATA. It is validated, reduced to a safe character set, and only
ever compared with strings (never used as a regex, a path to open, or a command).
Everything printed from the repository goes through json.dumps.

Status values in the JSON:
  one      exactly one route matched; continue with it
  several  more than one; show the candidates so the user picks
  none     nothing matched; "closest" lists near misses; "fallback": "claude"
           means the script could not read every route, so Claude should search
  invalid  the input was rejected; "reason" says why

Read-only. Never reads .env files. Stdlib only.
"""
import argparse
import difflib
import json
import os
import posixpath
import re
import sys

MAX_QUERY = 200
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
SRC_EXT = {".java", ".kt", ".groovy", ".scala", ".js", ".mjs", ".cjs", ".ts", ".py", ".go"}
CFG_EXT = {".yml", ".yaml", ".properties", ".conf"}
SKIP_DIRS = {
    ".git", "node_modules", "target", "build", "dist", "out", ".venv", "venv", "env", "__pycache__",
    "vendor", ".gradle", ".idea", ".vscode", "bin", "obj", "coverage", ".next", ".angular", ".nuxt",
    "test", "tests", "__tests__", "spec", "e2e", "site-packages", ".tox", ".mypy_cache", "migrations",
}
TEST_FILE_RE = re.compile(r"(\.test\.|\.spec\.|Test\.java$|Tests\.java$|IT\.java$|_test\.go$|_test\.py$|^test_.*\.py$)")
FRONTEND_MARKERS = ("from '@angular/", 'from "@angular/', "from 'react'", 'from "react"', "from 'vue'", 'from "vue"')
MAX_FILE = 1_000_000
MAX_CFG = 300_000
MAX_FILES = 20000
MAX_CANDIDATES = 20
SNIPPET = 200

PARAM_SEG = re.compile(r"^(\{.*\}|:\w.*|<.*>|\*+|\$\{.*\}|\[.*\])$")


# ----------------------------------------------------------------------------- query
def parse_query(raw):
    """Return (query dict, None) or (None, reason)."""
    if not isinstance(raw, str):
        return None, "the query must be text"
    if len(raw) > MAX_QUERY:
        return None, "the query is longer than %d characters" % MAX_QUERY
    text = "".join(ch for ch in raw if ch.isprintable()).strip()
    toks = text.split()
    if not toks:
        return None, "the query is empty"
    if len(toks) > 2:
        return None, "type an endpoint like 'POST /api/login', '/api/login', 'login' or a handler name, not a sentence"
    method = None
    if len(toks) == 2:
        m = toks[0].upper().rstrip(":")
        if m not in METHODS:
            return None, "when two words are given the first must be an HTTP method (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS)"
        method, target = m, toks[1]
    else:
        target = toks[0]
        if target in METHODS:
            return None, "an HTTP method needs a path or a name after it"
    target = re.sub(r"^[A-Za-z][A-Za-z0-9+.\-]*://[^/]*", "", target)   # a full URL becomes its path
    target = re.split(r"[?#]", target, maxsplit=1)[0]
    safe = re.sub(r"[^A-Za-z0-9_\-./{}:$*~%@]", "", target)
    if not safe or not re.search(r"[A-Za-z0-9]", safe):
        return None, "nothing usable is left after removing characters that are not part of a path or a name"
    if ".." in safe:
        return None, "'..' is not allowed"
    kind = "path" if "/" in safe else "name"
    out = {"method": method, "kind": kind, "stripped": safe != target}
    if kind == "path":
        out["path"] = norm_path(safe)
    else:
        out["name"] = safe
    return out, None


# ----------------------------------------------------------------------------- paths
def norm_path(p):
    p = re.sub(r"/+", "/", "/" + (p or "").strip())
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def join_path(*parts):
    return norm_path("/".join(x.strip("/") for x in parts if x and x.strip("/")))


def segs(p):
    return [s for s in p.split("/") if s]


def is_param(s):
    return bool(PARAM_SEG.match(s))


def seg_eq(a, b):
    return is_param(a) or is_param(b) or a.lower() == b.lower()


def param_hits(route, query):
    """How many route segments are parameters standing in for a literal typed segment (fewer = more specific)."""
    return sum(1 for a, b in zip(segs(route), segs(query)) if is_param(a) and not is_param(b))


def full_match(route, query):
    r, q = segs(route), segs(query)
    return len(r) == len(q) and all(seg_eq(x, y) for x, y in zip(r, q))


def tail_match(short, long_):
    s, l = segs(short), segs(long_)
    return 0 < len(s) < len(l) and all(seg_eq(x, y) for x, y in zip(s, l[-len(s):]))


# ----------------------------------------------------------------------------- scan state
class Ctx:
    def __init__(self):
        self.raw = []              # raw routes before prefix resolution
        self.consts = {}           # constant name -> value or None when ambiguous
        self.module_prefix = []    # (module_root, prefix, kind)  kind: spring | jaxrs
        self.nest_prefix = {}      # package dir -> global prefix
        self.mounts = []           # express mounts
        self.imports = {}          # file -> {local name: module string}
        self.py_prefix = {}        # (file, var) -> own prefix
        self.py_mounts = []        # fastapi / flask mounts
        self.py_imports = {}       # file -> {local name: (module, original)}
        self.dj_paths = []         # django path() entries
        self.dj_registers = []     # DRF router.register entries
        self.api_view = {}         # function name -> methods
        self.class_methods = {}    # class name -> http method names defined in it
        self.gateways = []         # {prefix, strip, service}
        self.nginx = []            # {location, upstream_uri}
        self.mentions = []
        self.files = 0
        self.skipped_frontends = 0
        self.scan_errors = 0
        self.pyfiles = []


def rel(root, p):
    return os.path.relpath(p, root).replace(os.sep, "/")


def snip(s):
    s = s.strip()[:SNIPPET]
    try:
        from verify import scrub_text, SNIPPET_SECRET
        if any(rx.search(s) for rx in SNIPPET_SECRET):
            return "[removed: this line looks like it contains a secret]"
        return scrub_text(s)
    except ImportError:
        return s


# ----------------------------------------------------------------------------- expressions (java)
def parse_expr(expr):
    """'"/a" + B' or '{"/a", "/b"}' -> list of alternatives; each is a list of ('s', str) / ('c', name)."""
    expr = expr.strip()
    if expr.startswith("{") and expr.endswith("}"):
        expr = expr[1:-1]
    alts = []
    depth, cur = 0, ""
    for ch in expr:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            alts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        alts.append(cur)
    out = []
    for a in alts:
        parts = []
        for piece in re.split(r"\+", a):
            piece = piece.strip()
            m = re.fullmatch(r'"((?:[^"\\]|\\.)*)"', piece)
            if m:
                parts.append(("s", m.group(1)))
            elif re.fullmatch(r"[A-Za-z_][\w.]*", piece):
                parts.append(("c", piece.split(".")[-1]))
            elif piece:
                parts.append(("x", piece))
        if parts:
            out.append(parts)
    return out


def ann_args(argstr):
    """Pull path expression and HTTP methods out of the text inside an annotation's parentheses."""
    methods = re.findall(r"RequestMethod\.(\w+)", argstr)
    m = re.search(r"\b(?:value|path)\s*=\s*(\{[^}]*\}|[^,)]+)", argstr)
    if m:
        expr = m.group(1)
    else:
        first = argstr.strip()
        expr = "" if (not first or re.match(r"^\w+\s*=", first)) else re.split(r",\s*(?=\w+\s*=)", first)[0]
    return (parse_expr(expr) if expr else [[("s", "")]]), [x.upper() for x in methods]


SPRING_MAP = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
              "PatchMapping": "PATCH", "RequestMapping": "ANY"}
ANN_RE = re.compile(r"@((?:\w+\.)*\w+)\s*(\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))?")


def scan_java(ctx, path, lines):
    pending = []          # (name, argstr, lineno, text)
    prefix_alts = [[("s", "")]]
    fw_class = None
    cls = None
    depth = 0             # brace depth: a class declared at depth > 0 is nested and changes nothing
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        m = re.match(r'^(?:(?:public|private|protected|static|final)\s+)*(?:val|String)\s+(\w+)\s*(?::\s*String)?\s*=\s*"([^"]*)"', s)
        if m and "static" in s or (m and s.startswith("const val")) or (m and s.startswith("val ")):
            name = m.group(1)
            ctx.consts[name] = m.group(2) if name not in ctx.consts else (m.group(2) if ctx.consts[name] == m.group(2) else None)
        if s.startswith("@"):
            start = i
            text = s
            while text.count("(") > text.count(")") and i + 1 < n:
                i += 1
                text += " " + lines[i].strip()
            rest = text
            for am in ANN_RE.finditer(text):
                pending.append((am.group(1).split(".")[-1], (am.group(2) or "")[1:-1], start + 1, lines[start].strip()))
            rest = ANN_RE.sub("", text).strip()
            if rest and "(" in rest and not re.search(r"\b(class|interface)\b", rest) and any(a[0] in SPRING_MAP or a[0] in METHODS for a in pending):
                scan_decl(ctx, path, rest, pending, start + 1, locals_=(prefix_alts, fw_class, cls))
                pending = []
            # a wrapped parameter line such as "@RequestBody Foo f) {" still opens or closes a block
            tail = re.sub(r'"(?:[^"\\]|\\.)*"', '""', rest)
            depth = max(0, depth + tail.count("{") - tail.count("}"))
            i += 1
            continue
        if not s or s.startswith(("//", "*", "/*")):
            i += 1
            continue
        code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', s)
        opened, closed = code.count("{"), code.count("}")
        cm = re.search(r"\b(?:class|interface|object)\s+(\w+)", s)
        if cm and depth > 0 and not s.startswith("import"):
            pending = []          # a nested class: the controller's own prefix and name stay
        elif cm and not s.startswith("import"):
            cls = cm.group(1)
            class_prefix = [[("s", "")]]
            class_alts = None
            kind = None
            for name, args, ln, txt in pending:
                if name == "RequestMapping":
                    class_alts = ann_args(args)[0]
                    kind = "spring"
                elif name == "Path":
                    class_alts = ann_args(args)[0]
                    kind = "jaxrs"
                elif name == "ApplicationPath":
                    ctx.module_prefix.append((module_root(path), ann_args(args)[0], "jaxrs", path, ln))
            prefix_alts = class_alts or [[("s", "")]]
            fw_class = kind
            pending = []
        elif "(" in s and any(a[0] in SPRING_MAP or a[0] in METHODS for a in pending):
            scan_decl(ctx, path, s, pending, i + 1, locals_=(prefix_alts, fw_class, cls))
            pending = []
        elif s.endswith(";") or "{" in s or "=" in s:
            pending = []
        depth = max(0, depth + opened - closed)
        i += 1


def scan_decl(ctx, path, decl, pending, lineno, locals_):
    if locals_ is None:
        return
    prefix_alts, fw_class, cls = locals_
    m = re.search(r"(\w+)\s*\(", decl)
    handler = m.group(1) if m else None
    http, paths, anns = [], None, []
    for name, args, ln, txt in pending:
        if name in SPRING_MAP:
            alts, meths = ann_args(args)
            http += meths or [SPRING_MAP[name]]
            paths = alts
            anns.append((ln, txt, "spring"))
        elif name in METHODS:
            http.append(name)
            anns.append((ln, txt, "jaxrs"))
        elif name == "Path":
            paths = ann_args(args)[0]
    if not http:
        return
    fw = "jaxrs" if any(a[2] == "jaxrs" for a in anns) else "spring"
    ln, txt = anns[0][0], anns[0][1]
    for method in sorted(set(http)):
        ctx.raw.append({"method": method, "own": paths or [[("s", "")]], "class_prefix": prefix_alts, "fw": fw,
                        "file": path, "line": ln, "handler_line": lineno, "handler": handler, "cls": cls, "snippet": snip(txt)})


def module_root(path):
    i = path.find("/src/")
    return path[:i] if i >= 0 else ""


# ----------------------------------------------------------------------------- express / nest
EXPRESS_RECV = re.compile(r"^(app|router|route|routes|server|api|r|\w*[Rr]outer|\w*[Aa]pp|\w*[Rr]outes?)$")
EXPRESS_ROUTE = re.compile(r"\b(\w+)\.(get|post|put|delete|patch|all|head|options)\(\s*(['\"`])([^'\"`]*)\3\s*(?:,(.*))?$")
EXPRESS_CHAIN = re.compile(r"\b(\w+)\.route\(\s*(['\"`])([^'\"`]*)\2\s*\)((?:\s*\.\s*\w+\([^)]*\))+)")
EXPRESS_USE = re.compile(r"\b(\w+)\.use\(\s*(['\"`])([^'\"`]*)\2\s*,\s*(.+?)\)\s*;?\s*$")
NEST_ROUTE = re.compile(r"@(Get|Post|Put|Delete|Patch|All|Head|Options)\(\s*(?:(['\"`])([^'\"`]*)\2)?\s*\)")
NEST_CTRL = re.compile(r"@Controller\(\s*(?:(['\"`])([^'\"`]*)\1|\{[^}]*\bpath\s*:\s*(['\"`])([^'\"`]*)\3[^}]*\})?\s*\)")
KEYWORDS = {"async", "function", "req", "res", "next", "return", "err", "await", "new", "const", "let", "var", "request", "response"}


def js_handler(rest):
    rest = re.sub(r"\s//.*$", "", rest or "")           # a trailing comment is not a handler
    if not rest.strip():
        return "(anonymous)"
    if "=>" in rest or re.search(r"\bfunction\b", rest):
        return "(anonymous)"
    toks = [t for t in re.findall(r"[A-Za-z_$][\w$.]*", rest) if t not in KEYWORDS]
    return toks[-1] if toks else "(anonymous)"


def scan_js(ctx, path, lines):
    for m in re.finditer(r"\b(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)|import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]", "\n".join(lines)):
        name = m.group(1) or m.group(3)
        mod = m.group(2) or m.group(4)
        ctx.imports.setdefault(path, {})[name] = mod
    ctrl, ctrl_class = None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.endswith("(") and re.search(r"\.(get|post|put|delete|patch|use|route)\($", s) and i + 1 < len(lines):
            s = s + lines[i + 1].strip()
        cm = NEST_CTRL.search(s)
        if cm:
            ctrl = cm.group(2) if cm.group(2) is not None else (cm.group(4) if cm.group(4) is not None else "")
            continue
        km = re.match(r"(?:export\s+)?(?:default\s+)?class\s+(\w+)", s)
        if km:
            ctrl_class = km.group(1)
        nm = NEST_ROUTE.search(s)
        if nm and ctrl is not None:
            handler = None
            for j in range(i + 1, min(i + 6, len(lines))):
                t = lines[j].strip()
                if t.startswith("@") or not t:
                    continue
                hm = re.match(r"(?:public\s+|private\s+|async\s+|static\s+)*(\w+)\s*\(", t)
                handler = hm.group(1) if hm else None
                break
            ctx.raw.append({"method": nm.group(1).upper() if nm.group(1) != "All" else "ANY", "own": [[("s", nm.group(3) or "")]],
                            "class_prefix": [[("s", ctrl)]], "fw": "nestjs", "file": path, "line": i + 1, "handler_line": i + 1,
                            "handler": handler, "cls": ctrl_class, "snippet": snip(line)})
            continue
        gm = re.search(r"setGlobalPrefix\(\s*['\"`]([^'\"`]*)['\"`]", s)
        if gm:
            ctx.nest_prefix[pkg_dir(path)] = gm.group(1)
        um = EXPRESS_USE.search(s)
        if um:
            ctx.mounts.append({"recv": um.group(1), "prefix": um.group(3), "ref": um.group(4).strip(), "file": path, "line": i + 1})
            continue
        chain = EXPRESS_CHAIN.search(s)
        if chain and EXPRESS_RECV.match(chain.group(1)):
            for meth, args in re.findall(r"\.\s*(\w+)\(([^)]*)\)", chain.group(4)):
                if meth.lower() in ("get", "post", "put", "delete", "patch", "all", "head", "options"):
                    ctx.raw.append({"method": meth.upper() if meth.lower() != "all" else "ANY", "own": [[("s", chain.group(3))]], "class_prefix": [[("s", "")]],
                                    "fw": "express", "file": path, "line": i + 1, "handler_line": i + 1, "handler": js_handler(args),
                                    "cls": None, "recv": chain.group(1), "snippet": snip(line)})
            continue
        em = EXPRESS_ROUTE.search(s)
        if em and EXPRESS_RECV.match(em.group(1)) and ".http." not in s and "this.http" not in s:
            ctx.raw.append({"method": em.group(2).upper() if em.group(2) != "all" else "ANY", "own": [[("s", em.group(4))]], "class_prefix": [[("s", "")]],
                            "fw": "express", "file": path, "line": i + 1, "handler_line": i + 1, "handler": js_handler(em.group(5)),
                            "cls": None, "recv": em.group(1), "snippet": snip(line)})


def pkg_dir(path):
    d = posixpath.dirname(path)
    while d and d != "." and "/" in d and not d.endswith("/src") and posixpath.basename(d) != "src":
        d = posixpath.dirname(d)
    d = posixpath.dirname(d) if posixpath.basename(d) == "src" else d
    return "" if d in (".", "/") else d


# ----------------------------------------------------------------------------- python
PY_ROUTE = re.compile(r"^@(\w+)\.(get|post|put|delete|patch|options|head)\(\s*['\"]([^'\"]*)['\"]")
PY_FLASK = re.compile(r"^@(\w+)\.route\(\s*['\"]([^'\"]*)['\"](.*)\)")
DJ_PATH = re.compile(r"^\s*(?:path|re_path|url)\(\s*(r?)['\"]([^'\"]*)['\"]\s*,\s*(.*)$", re.S)


def scan_py(ctx, path, lines):
    ctx.pyfiles.append(path)
    text = "\n".join(lines)
    for m in re.finditer(r"^from\s+([\w.]+)\s+import\s+(\w+)(?:\s+as\s+(\w+))?", text, re.M):
        ctx.py_imports.setdefault(path, {})[m.group(3) or m.group(2)] = (m.group(1), m.group(2))
    for m in re.finditer(r"^import\s+([\w.]+)(?:\s+as\s+(\w+))?", text, re.M):
        ctx.py_imports.setdefault(path, {})[m.group(2) or m.group(1).split(".")[-1]] = (m.group(1), None)
    for m in re.finditer(r"^(\w+)\s*=\s*APIRouter\((.*?)\)\s*$", text, re.M):
        pm = re.search(r"prefix\s*=\s*['\"]([^'\"]*)['\"]", m.group(2))
        ctx.py_prefix[(path, m.group(1))] = pm.group(1) if pm else ""
    for m in re.finditer(r"^(\w+)\s*=\s*Blueprint\((.*?)\)\s*$", text, re.M):
        pm = re.search(r"url_prefix\s*=\s*['\"]([^'\"]*)['\"]", m.group(2))
        ctx.py_prefix[(path, m.group(1))] = pm.group(1) if pm else ""
    for m in re.finditer(r"\b(\w+)\.(include_router|register_blueprint)\(\s*([\w.]+)\s*(?:,(.*?))?\)\s*$", text, re.M):
        pm = re.search(r"(?:prefix|url_prefix)\s*=\s*['\"]([^'\"]*)['\"]", m.group(4) or "")
        ctx.py_mounts.append({"recv": m.group(1), "ref": m.group(3), "prefix": pm.group(1) if pm else "", "file": path})
    cur_cls = None
    for i, line in enumerate(lines):
        cm = re.match(r"class\s+(\w+)", line)
        if cm:
            cur_cls = cm.group(1)
        dm = re.match(r"\s+(?:async\s+)?def\s+(get|post|put|patch|delete)\s*\(\s*self", line)
        if dm and cur_cls:
            ctx.class_methods.setdefault(cur_cls, set()).add(dm.group(1).upper())
        s = line.strip()
        am = re.match(r"^@api_view\(\s*\[([^\]]*)\]", s)
        if am:
            for j in range(i + 1, min(i + 6, len(lines))):
                fm = re.match(r"(?:async\s+)?def\s+(\w+)", lines[j].strip())
                if fm:
                    ctx.api_view[fm.group(1)] = [x.upper() for x in re.findall(r"['\"](\w+)['\"]", am.group(1))]
                    break
        rm = PY_ROUTE.match(s)
        fm = PY_FLASK.match(s)
        if rm or fm:
            if rm:
                var, method, own = rm.group(1), rm.group(2).upper(), rm.group(3)
                methods = [method]
            else:
                var, own = fm.group(1), fm.group(2)
                mm = re.search(r"methods\s*=\s*\[([^\]]*)\]", fm.group(3))
                methods = [x.upper() for x in re.findall(r"['\"](\w+)['\"]", mm.group(1))] if mm else ["GET"]
            handler = None
            for j in range(i + 1, min(i + 8, len(lines))):
                dm2 = re.match(r"\s*(?:async\s+)?def\s+(\w+)", lines[j])
                if dm2:
                    handler = dm2.group(1)
                    break
            for meth in methods:
                ctx.raw.append({"method": meth, "own": [[("s", own)]], "class_prefix": [[("s", "")]], "fw": "fastapi/flask",
                                "file": path, "line": i + 1, "handler_line": i + 1, "handler": handler, "cls": None, "recv": var,
                                "snippet": snip(line)})
    # django urlpatterns (statements may span lines)
    if re.search(r"\b(urlpatterns|router\.register)\b", text):
        stmts, buf, depth = [], "", 0
        for i, line in enumerate(lines):
            if not buf and not re.match(r"\s*(path|re_path|url)\(", line):
                rm2 = re.match(r"\s*(\w+)\.register\(\s*r?['\"]([^'\"]*)['\"]\s*,\s*([\w.]+)", line)
                if rm2:
                    ctx.dj_registers.append({"var": rm2.group(1), "prefix": rm2.group(2), "view": rm2.group(3).split(".")[-1], "file": path, "line": i + 1, "snippet": snip(line)})
                continue
            if not buf:
                start = i + 1
            buf += line.strip() + " "
            depth = buf.count("(") - buf.count(")")
            if depth <= 0:
                stmts.append((start, buf.strip()))
                buf = ""
        for ln, st in stmts:
            m = DJ_PATH.match(st)
            if not m:
                continue
            is_re, route, target = m.group(1), m.group(2), m.group(3)
            if st.startswith("re_path") or st.startswith("url") or is_re:
                route = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", route).replace("^", "").replace("$", "").replace("\\.", ".")
                route = re.sub(r"[()?\\]", "", route)
            inc = re.match(r"include\(\s*(?:\(\s*)?['\"]([\w.]+)['\"]", target) or None
            incvar = re.match(r"include\(\s*(\w+)\.urls", target)
            handler = None
            if not inc and not incvar:
                hm = re.match(r"([\w.]+?)(?:\.as_view\([^)]*\))?\s*(?:,|\))", target)
                handler = hm.group(1).split(".")[-1] if hm else None
            ctx.dj_paths.append({"route": route, "file": path, "line": ln, "include": inc.group(1) if inc else None,
                                 "incvar": incvar.group(1) if incvar else None, "handler": handler, "snippet": snip(st)})


# ----------------------------------------------------------------------------- go
GO_ROUTE = re.compile(r"\b(\w+)\.(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|Any|Get|Post|Put|Delete|Patch|Options|Head|Handle|HandleFunc)\(\s*\"([^\"]*)\"\s*(?:,\s*(.*))?$")
GO_GROUP = re.compile(r"(\w+)\s*:?=\s*(\w+)\.(?:Group|Route|PathPrefix|Subrouter)\(\s*\"([^\"]*)\"")
GO_CHI_ROUTE = re.compile(r"(\w+)\.Route\(\s*\"([^\"]*)\"\s*,\s*func\((\w+)\s+chi\.Router\)")
GO_ROOT = re.compile(r"gin\.(?:New|Default)\(|chi\.NewRouter\(|echo\.New\(|mux\.NewRouter\(|http\.NewServeMux\(|http\.HandleFunc|fiber\.New\(")


def scan_go(ctx, path, lines):
    groups = {}
    has_root = any(GO_ROOT.search(l) for l in lines)
    for i, line in enumerate(lines):
        s = line.strip()
        gm = GO_GROUP.search(s)
        if gm:
            groups[gm.group(1)] = join_path(groups.get(gm.group(2), ""), gm.group(3))
            continue
        cm = GO_CHI_ROUTE.search(s)
        if cm:
            groups[cm.group(3)] = join_path(groups.get(cm.group(1), ""), cm.group(2))
            continue
        m = GO_ROUTE.search(s)
        if not m:
            continue
        recv, verb, own, rest = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        method = verb.upper()
        if method in ("HANDLE", "HANDLEFUNC"):
            method = "ANY"
            pm = re.match(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/.*)$", own)
            if pm:
                method, own = pm.group(1), pm.group(2)
            mm = re.search(r"\.Methods\(\s*\"(\w+)\"", rest)
            if mm:
                method = mm.group(1).upper()
        elif method == "ANY":
            method = "ANY"
        rest = re.sub(r"\s//.*$", "", rest)             # a trailing comment is not a handler
        if "func(" in rest or "func (" in rest:
            handler = "(anonymous)"
        else:
            args = re.sub(r"\)\s*(\.\w+\(.*)?$", "", rest.strip())
            toks = [t.strip() for t in args.split(",") if t.strip()]
            hm = re.findall(r"[A-Za-z_][\w.]*", toks[-1]) if toks else []
            handler = hm[-1] if hm else None
        prefix = groups.get(recv, "")
        ctx.raw.append({"method": method, "own": [[("s", own)]], "class_prefix": [[("s", prefix)]], "fw": "go", "file": path, "line": i + 1,
                        "handler_line": i + 1, "handler": handler, "cls": None, "recv": recv, "snippet": snip(line),
                        "prefix_unknown": (not has_root) and recv not in groups})


# ----------------------------------------------------------------------------- config (gateway, context path)
def scan_cfg(ctx, root, path, text):
    base = posixpath.basename(path)
    if base.startswith("application") and base.endswith((".properties", ".yml", ".yaml")):
        for key in ("server.servlet.context-path", "server.servlet.contextPath", "server.context-path", "spring.mvc.servlet.path"):
            m = re.search(r"^\s*%s\s*[:=]\s*(\S+)" % re.escape(key), text, re.M)
            if m:
                ctx.module_prefix.append((module_root(path), [[("s", m.group(1).strip("'\""))]], "spring", path, 0))
        m = re.search(r"^\s*context-path\s*:\s*(\S+)", text, re.M)
        if m:
            ctx.module_prefix.append((module_root(path), [[("s", m.group(1).strip("'\""))]], "spring", path, 0))
    if path.endswith((".yml", ".yaml")) and "Path=" in text:
        for chunk in re.split(r"\n\s*-\s+id\s*:", text)[1:]:
            uri = re.search(r"uri\s*:\s*(\S+)", chunk)
            strip = re.search(r"StripPrefix\s*=\s*(\d+)", chunk)
            for pm in re.finditer(r"Path\s*=\s*([^\n]+)", chunk):
                for p in pm.group(1).split(","):
                    p = p.strip().strip("'\"")
                    if p.startswith("/"):
                        ctx.gateways.append({"prefix": re.sub(r"/?\*+$", "", p) or "/", "strip": int(strip.group(1)) if strip else 0,
                                             "service": re.sub(r"^\w+://", "", uri.group(1)) if uri else None, "file": path})
    if path.endswith(".conf") and "location" in text:
        loc = None
        for line in text.splitlines():
            lm = re.search(r"location\s+(?:[=~*^]+\s+)?([^\s{]+)\s*\{", line)
            if lm:
                loc = lm.group(1)
            pm = re.search(r"proxy_pass\s+\w+://[^/;\s]+(/[^;\s]*)?;", line)
            if pm and loc:
                ctx.nginx.append({"location": loc, "upstream_uri": pm.group(1), "file": path})


# ----------------------------------------------------------------------------- walk
def find_frontend_roots(root):
    out = set()
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        if "angular.json" in fn or "nuxt.config.js" in fn or "nuxt.config.ts" in fn or "vue.config.js" in fn:
            out.add(os.path.relpath(dp, root).replace(os.sep, "/"))
    return out


def walk(root, ctx, needle):
    frontends = find_frontend_roots(root)
    seen = 0
    for dp, dn, fn in os.walk(root):
        reld = os.path.relpath(dp, root).replace(os.sep, "/")
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
        if any(reld == f or reld.startswith(f + "/") for f in frontends if f != "."):
            ctx.skipped_frontends += 1
            dn[:] = []
            continue
        if "." in frontends and reld == "." and False:
            continue
        for f in sorted(fn):
            ext = os.path.splitext(f)[1].lower()
            full = os.path.join(dp, f)
            p = rel(root, full)
            if f.lower().startswith(".env") and f.lower() not in (".env.example", ".env.sample", ".env_sample"):
                continue
            if os.path.islink(full):
                continue            # a link could lead out of the project
            if ext in CFG_EXT:
                try:
                    if os.path.getsize(full) > MAX_CFG:
                        continue
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        scan_cfg(ctx, root, p, fh.read())
                except OSError:
                    pass
                continue
            if ext not in SRC_EXT or TEST_FILE_RE.search(f):
                continue
            seen += 1
            if seen > MAX_FILES:
                return
            try:
                if os.path.getsize(full) > MAX_FILE:
                    continue
                with open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            if ext in (".js", ".mjs", ".cjs", ".ts") and any(mk in text[:4000] for mk in FRONTEND_MARKERS):
                continue
            ctx.files += 1
            lines = text.splitlines()
            if needle and len(ctx.mentions) < 10:
                for i, l in enumerate(lines):
                    if needle in l.lower() and not l.lstrip().startswith(("import ", "from ", "package ", "#", "//")):
                        ctx.mentions.append({"file": p, "line": i + 1, "snippet": snip(l)})
                        if len(ctx.mentions) >= 10:
                            break
            try:
                if ext in (".java", ".kt", ".groovy", ".scala"):
                    scan_java(ctx, p, lines)
                elif ext in (".js", ".mjs", ".cjs", ".ts"):
                    scan_js(ctx, p, lines)
                elif ext == ".py":
                    scan_py(ctx, p, lines)
                elif ext == ".go":
                    scan_go(ctx, p, lines)
            except Exception:      # one odd file must never stop the search
                ctx.scan_errors += 1


# ----------------------------------------------------------------------------- prefix resolution
def resolve_alts(alts, consts):
    """list of alternatives -> list of strings; None entries are unresolved."""
    out = []
    for parts in alts:
        val, ok = "", True
        for kind, v in parts:
            if kind == "s":
                val += v
            elif kind == "c":
                cv = consts.get(v)
                if cv is None:
                    ok = False
                    break
                val += cv
            else:
                ok = False
                break
        out.append(val if ok else None)
    return out


def mod_stem_candidates(file):
    base = posixpath.splitext(file)[0]
    c = {base}
    if posixpath.basename(base) == "index":
        c.add(posixpath.dirname(base))
    return c


def express_prefixes(ctx, r, depth=0):
    """Prefixes for an express route: mounts by receiver variable in the same file, or by import from another file."""
    out = []
    for m in ctx.mounts:
        target_local = m["file"] == r["file"] and m["ref"] == r.get("recv") and r.get("recv") not in (None,)
        target_import = False
        if not target_local:
            ref = m["ref"]
            rm = re.match(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", ref)
            mod = rm.group(1) if rm else ctx.imports.get(m["file"], {}).get(re.sub(r"[^\w$].*", "", ref))
            if mod and mod.startswith("."):
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(m["file"]), mod))
                target_import = resolved in mod_stem_candidates(r["file"])
        if target_local or target_import:
            base = [m["prefix"]]
            if depth < 3:
                up = express_prefixes(ctx, {"file": m["file"], "recv": m["recv"]}, depth + 1)
                if up:
                    base = [join_path(u, m["prefix"]) for u in up]
            out += base
    return out


def py_prefixes(ctx, file, var, depth=0):
    own = ctx.py_prefix.get((file, var), "")
    out = []
    for m in ctx.py_mounts:
        ref = m["ref"]
        tfile, tvar = None, None
        imp = ctx.py_imports.get(m["file"], {})
        if "." in ref:
            head, attr = ref.split(".", 1)
            mod, orig = imp.get(head, (head, None))
            tfile = (orig and py_module_file(ctx, mod + "." + orig, m["file"])) or py_module_file(ctx, mod, m["file"])
            tvar = attr.split(".")[-1]
        elif ref in imp:
            tfile = py_module_file(ctx, imp[ref][0], m["file"])
            tvar = imp[ref][1] or ref
        else:
            tfile, tvar = m["file"], ref
        if tfile == file and tvar == var:
            up = py_prefixes(ctx, m["file"], m["recv"], depth + 1) if depth < 3 else [""]
            for u in up or [""]:
                out.append(join_path(u, m["prefix"], own))
    return out or ([own] if own else [""])


def py_module_file(ctx, mod, from_file):
    parts = mod.lstrip(".").replace(".", "/")
    for f in ctx.pyfiles:
        if f.endswith(parts + ".py") or f.endswith(parts + "/__init__.py"):
            return f
    return None


def django_routes(ctx):
    """Expand include() chains into raw routes."""
    by_file = {}
    for d in ctx.dj_paths:
        by_file.setdefault(d["file"], []).append(d)
    included = {}
    for d in ctx.dj_paths:
        if d["include"]:
            tf = py_module_file(ctx, d["include"], d["file"])
            if tf:
                included.setdefault(tf, []).append(d)
        if d["incvar"]:
            included.setdefault(d["file"] + "::" + d["incvar"], []).append(d)

    def file_prefixes(f, depth=0):
        if f not in included or depth > 4:
            return [""]
        out = []
        for inc in included[f]:
            for up in file_prefixes(inc["file"], depth + 1):
                out.append(join_path(up, inc["route"]))
        return out

    out = []
    for d in ctx.dj_paths:
        if d["include"] or d["incvar"]:
            continue
        methods = ctx.api_view.get(d["handler"]) or sorted(ctx.class_methods.get(d["handler"], [])) or ["ANY"]
        for meth in methods:
            for pre in file_prefixes(d["file"]):
                out.append({"method": meth, "own": [[("s", d["route"])]], "class_prefix": [[("s", pre)]], "fw": "django", "file": d["file"],
                            "line": d["line"], "handler_line": d["line"], "handler": d["handler"], "cls": None, "snippet": d["snippet"]})
    for rg in ctx.dj_registers:
        pres = file_prefixes(rg["file"] + "::" + rg["var"]) if (rg["file"] + "::" + rg["var"]) in included else file_prefixes(rg["file"])
        for pre in pres:
            for suffix, meths in (("", ["GET", "POST"]), ("/{pk}", ["GET", "PUT", "PATCH", "DELETE"])):
                for meth in meths:
                    out.append({"method": meth, "own": [[("s", rg["prefix"] + suffix)]], "class_prefix": [[("s", pre)]], "fw": "django-drf",
                                "file": rg["file"], "line": rg["line"], "handler_line": rg["line"], "handler": rg["view"], "cls": rg["view"],
                                "snippet": rg["snippet"]})
    return out


def finalize(ctx):
    """Turn raw routes into routes with full paths."""
    routes, unresolved = [], 0
    raw = list(ctx.raw) + django_routes(ctx)
    for r in raw:
        own = resolve_alts(r["own"], ctx.consts)
        cls_pre = resolve_alts(r["class_prefix"], ctx.consts)
        if any(x is None for x in own) or any(x is None for x in cls_pre):
            unresolved += 1
        own = [x for x in own if x is not None] or [None]
        cls_pre = [x for x in cls_pre if x is not None] or [None]
        extra = [""]
        prefix_unknown = bool(r.get("prefix_unknown"))
        notes = []
        if r["fw"] == "express":
            extra = express_prefixes(ctx, r) or [""]
            if extra == [""] and r.get("recv") not in ("app", "server", "api") and not r["file"].endswith(("app.js", "server.js", "app.ts", "server.ts", "main.ts")):
                prefix_unknown = True
        elif r["fw"] == "fastapi/flask":
            extra = py_prefixes(ctx, r["file"], r.get("recv")) or [""]
            if (r["file"], r.get("recv")) in ctx.py_prefix and not any(extra):
                prefix_unknown = prefix_unknown or not any(m for m in ctx.py_mounts)
        elif r["fw"] == "nestjs":
            extra = [ctx.nest_prefix.get(pkg_dir(r["file"]), "")]
        mods = []
        if r["fw"] in ("spring", "jaxrs"):
            for root_, alts, kind, f, ln in ctx.module_prefix:
                if kind == r["fw"] and (root_ == "" or r["file"].startswith(root_ + "/")):
                    mods += [x for x in resolve_alts(alts, ctx.consts) if x is not None]
        mods = mods[:2] or [""]
        count = 0
        for m_ in mods:
            for e in extra:
                for cp in cls_pre:
                    for o in own:
                        if o is None or cp is None:
                            full = None
                        else:
                            full = join_path(m_, e, cp, o)
                        routes.append({"method": r["method"], "path": full, "own": o, "framework": r["fw"], "file": r["file"], "line": r["line"],
                                       "handlerLine": r["handler_line"], "handler": r["handler"], "class": r["cls"], "snippet": r["snippet"],
                                       "prefixUnknown": prefix_unknown})
                        count += 1
                        if count >= 6:
                            break
    seen, out = set(), []
    for r in routes:
        k = (r["method"], r["path"], r["file"], r["line"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out, unresolved


# ----------------------------------------------------------------------------- matching
def gateway_variants(ctx, q):
    """Paths the service may see for a path typed as seen at the gateway."""
    out = []
    for g in ctx.gateways:
        gp, qs = segs(g["prefix"]), segs(q)
        if gp and len(qs) >= len(gp) and all(seg_eq(a, b) for a, b in zip(gp, qs)):
            n = g["strip"] if g["strip"] else 0
            if n:
                out.append((norm_path("/" + "/".join(qs[n:])), "gateway rule %s (StripPrefix=%d)" % (g["prefix"], n)))
    for ng in ctx.nginx:
        loc = ng["location"].rstrip("/")
        if ng["upstream_uri"] is not None and (q == loc or q.startswith(loc + "/")) and loc:
            out.append((join_path(ng["upstream_uri"], q[len(loc):]), "nginx location %s with a proxy_pass path" % ng["location"]))
    seen, uniq = set(), []
    for v in out:
        if v[0] not in seen and v[0] != q:
            seen.add(v[0])
            uniq.append(v)
    return uniq


def score_route(ctx, r, q, variants):
    """Return (score, how) for one route against a parsed query, or (0, None)."""
    if q["method"] and r["method"] not in (q["method"], "ANY"):
        return 0, None
    if q["kind"] == "path":
        rp = r["path"]
        if rp is None:
            return 0, None
        if full_match(rp, q["path"]):
            return 100 - 0.1 * param_hits(rp, q["path"]), "exact path"
        for vp, why in variants:
            if full_match(rp, vp):
                return 95 - 0.1 * param_hits(rp, vp), "path through " + why
        if tail_match(rp, q["path"]):
            return 80, "route is the tail of the typed path (a prefix that is not in the code, such as a gateway or context path)"
        if tail_match(q["path"], rp):
            return 70, "typed path is the tail of the route"
        return 0, None
    name = q["name"]
    low = name.lower()
    h = (r["handler"] or "").lower()
    cls = (r["class"] or "").lower()
    if h and (low == h or low == (cls + "." + h)):
        return 90, "handler name"
    last = [s for s in segs(r["path"] or "") if not is_param(s)]
    if last and last[-1].lower() == low:
        return 60, "last path segment"
    if low in [s.lower() for s in last]:
        return 40, "a path segment"
    if len(low) >= 4 and h and low in h:
        return 50, "part of the handler name"
    return 0, None


def decide(ctx, q, routes):
    variants = gateway_variants(ctx, q["path"]) if q["kind"] == "path" else []
    scored = []
    for r in routes:
        sc, how = score_route(ctx, r, q, variants)
        if sc:
            scored.append((sc, how, r))
    scored.sort(key=lambda t: (-t[0], t[2]["file"], t[2]["line"]))
    if not scored:
        return "none", [], variants
    top = scored[0][0]
    if top >= 90:
        # a route with a literal segment beats one with a {param} in that place (Spring and Express pick the same)
        keep = [t for t in scored if t[0] == top] if q["kind"] == "path" else [t for t in scored if t[0] >= 90]
    elif top >= 60:
        keep = [t for t in scored if t[0] >= 60 and t[0] >= top - 10]
    else:
        keep = []
    if not keep:
        return "none", [], variants
    return ("one" if len(keep) == 1 else "several"), keep, variants


def closest(q, routes, limit=5):
    out = []
    want = (q.get("path") or q.get("name")).lower()
    for r in routes:
        cands = []
        if r["path"]:
            cands.append(r["path"].lower())
            if q["kind"] == "name":
                cands += [s.lower() for s in segs(r["path"]) if not is_param(s)]
        if r["handler"]:
            cands.append(r["handler"].lower())
        best = max((difflib.SequenceMatcher(None, want, c).ratio() for c in cands), default=0)
        if best >= 0.5:
            out.append((best, r))
    out.sort(key=lambda t: -t[0])
    return [dict(route_view(r), similarity=round(b, 2)) for b, r in out[:limit]]


def route_view(r, how=None, score=None):
    v = {"method": r["method"], "path": r["path"], "handler": r["handler"], "class": r["class"], "framework": r["framework"],
         "file": r["file"], "line": r["line"], "handlerLine": r["handlerLine"], "snippet": r["snippet"]}
    if how:
        v["matchedBy"] = how
    if score is not None:
        v["score"] = score
    if r.get("prefixUnknown"):
        v["note"] = "the router's mount prefix could not be determined from the code"
    return v


# ----------------------------------------------------------------------------- main
def run(root, raw_query):
    q, why = parse_query(raw_query)
    if q is None:
        return {"status": "invalid", "reason": why}
    needle = None
    if q["kind"] == "path":
        lit = [s for s in segs(q["path"]) if not is_param(s)]
        needle = lit[-1].lower() if lit and len(lit[-1]) >= 3 else None
    else:
        needle = q["name"].lower() if len(q["name"]) >= 3 else None
    ctx = Ctx()
    walk(root, ctx, needle)
    routes, unresolved = finalize(ctx)
    status, keep, variants = decide(ctx, q, routes)
    out = {"status": status, "query": {k: v for k, v in q.items()},
           "scanned": {"files": ctx.files, "routes": len(routes), "unresolvedPaths": unresolved,
                       "frameworks": sorted({r["framework"] for r in routes}), "skippedFrontendDirs": ctx.skipped_frontends, "filesWithParseErrors": ctx.scan_errors}}
    if variants:
        out["gatewayRewrites"] = [{"path": p, "via": w} for p, w in variants]
    if status in ("one", "several"):
        out["matches"] = [route_view(r, how, sc) for sc, how, r in keep[:MAX_CANDIDATES]]
        if status == "several":
            out["message"] = "%d routes match; the user picks one" % len(keep)
    else:
        out["matches"] = []
        out["closest"] = closest(q, routes)
        out["message"] = "not found"
        prefix_gaps = sum(1 for r in routes if r.get("prefixUnknown"))
        if not routes or unresolved or prefix_gaps:
            out["fallback"] = "claude"
            out["fallbackReason"] = ("no routes could be read by the script" if not routes else
                                     "%d route path(s) use values the script cannot resolve and %d router(s) have an unknown mount prefix" % (unresolved, prefix_gaps))
        if ctx.mentions:
            out["mentions"] = ctx.mentions
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find the backend route a user typed.")
    ap.add_argument("--root", default=".", help="project folder (default: current folder)")
    ap.add_argument("--query", required=True, help="what the user typed, at most 200 characters")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(json.dumps({"status": "invalid", "reason": "project folder not found"}))
        return 2
    print(json.dumps(run(root, args.query), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
