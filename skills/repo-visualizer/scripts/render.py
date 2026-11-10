#!/usr/bin/env python3
"""Validate architecture.json and render architecture.html (repo-visualizer).

Standard library only. Reads architecture.json, validates it against the
schema in references/graph-schema.md, injects it into assets/template.html and
writes architecture.html.

On invalid input it prints EVERY problem it found (with the JSON path and what
is expected) to stderr and exits with code 1, so the file can be fixed and the
script re-run. It never prints the value of anything that looks like a secret.

Usage:
    python3 render.py --input architecture.json --output architecture.html

Exit codes: 0 ok | 1 validation errors | 2 usage or I/O error
"""
import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TOKEN = "__ARCHITECTURE_JSON__"
MARKER = 'id="arch-data"'          # present in every file this script writes
MAX_SHOWN = 60                     # cap on printed errors
MAX_FS_BYTES = 20 * 1024 * 1024    # skip line-count check for huge files

NODE_TYPES = ["client", "external-caller", "gateway", "middleware", "service", "database",
              "cache", "queue", "external-api", "storage"]
CATEGORIES = ["auth", "security", "cors", "rate-limit", "validation", "logging",
              "error-handler"]
EDGE_KINDS = ["http", "grpc", "call", "other", "db-query", "cache-read", "cache-write", "publishes",
              "subscribes", "middleware-next", "response"]
DIRECTIONS = ["request", "response"]
ENDPOINT_TYPES = ("client", "external-caller")
LEGACY_CLIENT_TYPES = ("user", "frontend")
CLIENT_NAME = "Client / User"
FRONTEND_FILE_RE = re.compile(r"(\.(vue|svelte|jsx|tsx|jsp|jspx|jsf|xhtml)$)|(\.component\.(ts|html|scss|css)$)", re.I)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
TIERS = ["client", "caller", "gateway", "service", "datastore", "external"]
TYPE_TIER = {"client": "client", "external-caller": "caller", "gateway": "gateway", "middleware": "service", "service": "service",
             "database": "datastore", "cache": "datastore", "queue": "datastore", "storage": "datastore", "external-api": "external"}

SECRET_PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credentials inside a URL", re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("API key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("JWT literal", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("secret assigned a value", re.compile(
        r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key)"
        r"\s*[=:]\s*[\"']?(?=[^\s\"',;]*[0-9_/+=@$!%^&*-])[^\s\"',;]{8,}")),
]


def is_str(v):
    return isinstance(v, str) and v.strip() != ""


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def quoted_list(items):
    return ", ".join('"%s"' % i for i in items)


class Validator:
    def __init__(self, root, fs_check):
        self.root = root
        self.fs_check = fs_check
        self.errors = []
        self.warnings = []
        self._lines = {}
        self._text = {}

    def err(self, path, msg):
        self.errors.append("%s: %s" % (path, msg))

    def warn(self, path, msg):
        self.warnings.append("%s: %s" % (path, msg))

    # ---------------------------------------------------------------- helpers
    def line_count(self, abspath):
        if abspath in self._lines:
            return self._lines[abspath]
        n = None
        try:
            if os.path.getsize(abspath) <= MAX_FS_BYTES:
                with open(abspath, "rb") as f:
                    data = f.read()
                n = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        except OSError:
            n = None
        self._lines[abspath] = n
        return n

    def check_evidence(self, path, ev, required=True):
        if not isinstance(ev, list):
            self.err(path, 'must be a list of {"file": "...", "line": N} objects')
            return
        if required and not ev:
            self.err(path, 'needs at least one {"file", "line"} entry. Evidence is mandatory; '
                           'if you cannot point at code, remove this item or move the link to "uncertain"')
            return
        for i, item in enumerate(ev):
            p = "%s[%d]" % (path, i)
            if not isinstance(item, dict):
                self.err(p, 'must be an object like {"file": "src/app.ts", "line": 12}')
                continue
            f, ln = item.get("file"), item.get("line")
            if is_str(f) and FRONTEND_FILE_RE.search(f):
                self.warn(p + ".file", '"%s" looks like frontend code. This tool maps the backend only: cite backend evidence '
                                       "(the route definition, gateway config, CORS or security config)" % f)
            ok_file = True
            if not is_str(f):
                self.err(p + ".file", "is required and must be a non-empty string")
                ok_file = False
            else:
                if "\\" in f:
                    self.err(p + ".file", 'use forward slashes ("src/app.ts"), got a backslash')
                    ok_file = False
                if f.startswith("/") or re.match(r"^[A-Za-z]:", f):
                    self.err(p + ".file", "must be relative to the project root, not absolute")
                    ok_file = False
                elif ".." in f.replace("\\", "/").split("/"):
                    self.err(p + ".file", 'must not contain ".."')
                    ok_file = False
            ok_line = True
            if not is_int(ln) or ln < 1:
                self.err(p + ".line", "is required and must be an integer >= 1 (1-based line number)")
                ok_line = False
            if "note" in item and not isinstance(item["note"], str):
                self.err(p + ".note", "must be a string when present")
            if self.fs_check and ok_file:
                ap = os.path.normpath(os.path.join(self.root, f))
                try:
                    inside = os.path.commonpath([os.path.realpath(ap), os.path.realpath(self.root)]) == os.path.realpath(self.root)
                except ValueError:
                    inside = False
                if not inside:
                    self.err(p + ".file", "resolves outside the project root")
                elif not os.path.isfile(ap):
                    self.err(p + ".file", '"%s" does not exist under the project root. '
                                          "Use a path you actually read" % f)
                elif ok_line:
                    n = self.line_count(ap)
                    if n is not None and ln > n:
                        self.err(p + ".line", '%d is past the end of "%s" (%d lines)' % (ln, f, n))
                    elif n is not None:
                        txt = self.get_line(ap, ln)
                        if txt is not None and not txt.strip():
                            self.warn(p + ".line", 'line %d of "%s" is empty: the evidence is probably off by a few lines' % (ln, f))
                        elif txt is not None and re.fullmatch(r"[\s{}()\[\];,]+", txt):
                            self.warn(p + ".line", 'line %d of "%s" is only punctuation (%s): the evidence is probably off by a few lines' % (ln, f, txt.strip()))

    def get_line(self, abspath, n):
        lines = self._text.get(abspath)
        if lines is None:
            try:
                with open(abspath, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.read().splitlines() if os.path.getsize(abspath) <= MAX_FS_BYTES else []
            except OSError:
                lines = []
            self._text[abspath] = lines
        return lines[n - 1] if 0 < n <= len(lines) else None

    def check_secrets(self, path, value):
        if isinstance(value, str):
            for label, rx in SECRET_PATTERNS:
                if rx.search(value):
                    self.err(path, "looks like a secret (%s). Remove the value and use the variable "
                                   "name only (the matched text is not shown)" % label)
                    break
        elif isinstance(value, dict):
            for k, v in value.items():
                self.check_secrets("%s.%s" % (path, k), v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                self.check_secrets("%s[%d]" % (path, i), v)

    def check_id(self, path, v, seen, what):
        if not is_str(v):
            self.err(path + ".id", "is required and must be a non-empty string")
            return None
        if not ID_RE.match(v):
            self.err(path + ".id", '"%s" must be lowercase kebab-case (a-z, 0-9, "-" or "_"), e.g. "user-service"' % v)
        if v in seen:
            self.err(path + ".id", 'duplicate %s id "%s" (first used at %s)' % (what, v, seen[v]))
        else:
            seen[v] = path
        return v

    # ---------------------------------------------------------------- sections
    def validate(self, data):
        if not isinstance(data, dict):
            self.err("$", "top level must be a JSON object with project, nodes, edges, flows, uncertain")
            return
        for key in ("project", "nodes", "edges", "flows", "uncertain"):
            if key not in data:
                self.err("$", 'missing required top-level key "%s"' % key)
        for key in data:
            if key not in ("project", "nodes", "edges", "flows", "uncertain", "meta", "$schema"):
                self.warn("$", 'unknown top-level key "%s" (it is ignored)' % key)

        self.check_project(data.get("project"))
        node_ids, node_type = self.check_nodes(data.get("nodes"))
        edge_pairs = self.check_edges(data.get("edges"), node_ids, node_type)
        self.check_flows(data.get("flows"), node_ids, node_type, edge_pairs)
        self.check_importance(data, node_ids)
        self.check_uncertain(data.get("uncertain"), node_ids, edge_pairs)
        self.check_meta(data.get("meta"))
        self.check_secrets("$", data)
        self.check_orphans(data, node_ids)
        self.check_middleware_owners(data, node_ids, node_type)

    def check_project(self, p):
        if not isinstance(p, dict):
            if p is not None:
                self.err("project", "must be an object with name, summary, languages, frameworks")
            return
        for k in ("name", "summary"):
            if not is_str(p.get(k)):
                self.err("project.%s" % k, "is required and must be a non-empty string")
        for k in ("languages", "frameworks"):
            v = p.get(k)
            if not isinstance(v, list) or not all(is_str(x) for x in v):
                self.err("project.%s" % k, "is required and must be a list of strings (may be empty)")

    def check_nodes(self, nodes):
        ids, types = {}, {}
        if nodes is None:
            return ids, types
        if not isinstance(nodes, list):
            self.err("nodes", "must be a list")
            return ids, types
        if not nodes:
            self.err("nodes", "is empty: at least one component is required")
        seen, owners = {}, []
        self.owner_of = {}
        for i, n in enumerate(nodes):
            p = "nodes[%d]" % i
            if not isinstance(n, dict):
                self.err(p, "must be an object")
                continue
            nid = self.check_id(p, n.get("id"), seen, "node")
            if nid:
                p = "nodes[%d](%s)" % (i, nid)
                ids[nid] = n
            if not is_str(n.get("name")):
                self.err(p + ".name", "is required and must be a non-empty string")
            t = n.get("type")
            if t in LEGACY_CLIENT_TYPES:
                self.err(p + ".type", 'type "%s" is not used any more. Every frontend, browser and app is ONE node: '
                                      '{"id": "client", "name": "%s", "type": "client", "tier": "client"}. Do not create nodes for frontend apps' % (t, CLIENT_NAME))
            elif t not in NODE_TYPES:
                self.err(p + ".type", 'is "%s"; must be one of: %s' % (t, quoted_list(NODE_TYPES)))
            else:
                if nid:
                    types[nid] = t
            if t == "client":
                if nid and nid != "client":
                    self.err(p + ".id", 'the client node\'s id must be "client" (got "%s")' % nid)
                if n.get("name") != CLIENT_NAME:
                    self.err(p + ".name", 'the client node must be named exactly "%s" (got %r)' % (CLIENT_NAME, n.get("name")))
            cat = n.get("category")
            if t == "middleware":
                if cat not in CATEGORIES:
                    self.err(p + ".category", 'is required for middleware (got %r); must be one of: %s'
                             % (cat, quoted_list(CATEGORIES)))
            elif cat is not None:
                self.err(p + ".category", 'is only allowed when type is "middleware" (this node is "%s"); remove it' % t)
            if "tech" in n and n["tech"] is not None and not isinstance(n["tech"], str):
                self.err(p + ".tech", "must be a string when present")
            if not is_str(n.get("domain")):
                self.err(p + ".domain", 'is required: the business area it belongs to (for example "recruit", "payments", "platform"), '
                                        "inferred from names, folders and package names")
            tier = n.get("tier")
            if tier not in TIERS:
                self.err(p + ".tier", 'is %r; must be one of: %s (it decides the overview column)' % (tier, quoted_list(TIERS)))
            elif t in TYPE_TIER and tier != TYPE_TIER[t]:
                self.warn(p + ".tier", 'is "%s", but type "%s" is normally in tier "%s". Fine if intended (for example an external service '
                                       "you do not own); otherwise fix it" % (tier, t, TYPE_TIER[t]))
            imp = n.get("importance")
            if not is_int(imp) or not 1 <= imp <= 3:
                self.err(p + ".importance", "is %r; must be an integer 1 to 3 (how many flows pass through it: 3 = most, 1 = one or none)" % (imp,))
            if t == "middleware":
                if not is_str(n.get("owner")):
                    self.err(p + ".owner", "is required for middleware: the id of the service it belongs to")
                else:
                    owners.append((p + ".owner", n["owner"]))
                    if nid:
                        self.owner_of[nid] = n["owner"]
            elif n.get("owner") is not None:
                self.err(p + ".owner", 'is only allowed on middleware (this node is "%s"); remove it' % t)
            if n.get("group") is not None:
                self.warn(p + ".group", 'is deprecated and ignored: use "owner" (the id of the service the middleware belongs to)')
            if not is_str(n.get("description")):
                self.err(p + ".description", "is required: one sentence on what it does in this project")
            self.check_evidence(p + ".evidence", n.get("evidence"))
        clients = [i for i, ty in types.items() if ty == "client"]
        if len(clients) > 1:
            self.err("nodes", 'only ONE client node is allowed (found %s): every browser, app and frontend is the single "%s" node' % (quoted_list(clients), CLIENT_NAME))
        for p, o in owners:
            if o not in ids:
                self.err(p, 'node id "%s" does not exist in "nodes"%s' % (o, self.suggest(o, ids)))
            elif types.get(o) != "service":
                self.err(p, '"%s" is a "%s" node; a middleware owner must be a "service"' % (o, types.get(o)))
        forms = {}
        for n in ids.values():
            if is_str(n.get("domain")):
                forms.setdefault(re.sub(r"[\s_-]+", "", n["domain"].lower()), set()).add(n["domain"].strip())
        for fs in forms.values():
            if len(fs) > 1:
                self.warn("nodes", "the same domain is spelled several ways (%s); use one spelling everywhere" % ", ".join(sorted(repr(f) for f in fs)))
        if len(nodes) >= 8 and len(forms) > max(6, len(nodes) // 2):
            self.warn("nodes", "has %d different domains for %d nodes; group related components under fewer, broader business areas" % (len(forms), len(nodes)))
        return ids, types

    def check_edges(self, edges, node_ids, node_type):
        pairs = set()
        entry_seen = {}
        has_gateway = any(v == "gateway" for v in node_type.values())
        if edges is None:
            return pairs
        if not isinstance(edges, list):
            self.err("edges", "must be a list")
            return pairs
        seen = set()
        for i, e in enumerate(edges):
            p = "edges[%d]" % i
            if not isinstance(e, dict):
                self.err(p, "must be an object")
                continue
            s, t = e.get("source"), e.get("target")
            for k, v in (("source", s), ("target", t)):
                if not is_str(v):
                    self.err("%s.%s" % (p, k), "is required: a node id")
                elif v not in node_ids:
                    self.err("%s.%s" % (p, k), 'node id "%s" does not exist in "nodes"%s' % (v, self.suggest(v, node_ids)))
            if e.get("kind") not in EDGE_KINDS:
                self.err(p + ".kind", 'is %r; must be one of: %s' % (e.get("kind"), quoted_list(EDGE_KINDS)))
            if not is_str(e.get("label")):
                self.err(p + ".label", 'is required (e.g. "POST /login", "GET user:{email}")')
            self.check_evidence(p + ".evidence", e.get("evidence"))
            src_t, dst_t = node_type.get(s) if is_str(s) else None, node_type.get(t) if is_str(t) else None
            if src_t in ENDPOINT_TYPES:
                if dst_t is not None and dst_t not in ("gateway", "service"):
                    self.err(p + ".target", 'a %s connects to the backend entry point: a "gateway" or a "service", not a "%s"' % (src_t, dst_t))
                elif src_t == "client" and has_gateway and dst_t == "service":
                    self.warn(p, 'the client connects straight to service "%s" although the project has a gateway. If traffic really goes '
                                 "through the gateway, make this edge client -> gateway" % t)
                self.check_endpoints(p + ".endpoints", e.get("endpoints"))
                if is_str(s) and is_str(t):
                    if (s, t) in entry_seen:
                        self.err(p, 'duplicate of %s: use ONE edge per entry point and list every endpoint in its "endpoints"' % entry_seen[(s, t)])
                    else:
                        entry_seen[(s, t)] = "edges[%d]" % i
            elif e.get("endpoints") is not None:
                self.err(p + ".endpoints", "is only allowed on edges that start at the client or an external caller")
            if is_str(s) and is_str(t):
                pairs.add((s, t))
                key = (s, t, e.get("kind"), e.get("label"))
                if key in seen:
                    self.warn(p, "duplicate of an earlier edge (%s -> %s, %s)" % (s, t, e.get("kind")))
                seen.add(key)
        return pairs

    def check_endpoints(self, path, eps):
        if not isinstance(eps, list) or not eps:
            self.err(path, 'is required on edges from the client or an external caller: a list of {"method", "path", "evidence"}, '
                           "one entry per endpoint behind this entry point")
            return
        seen = set()
        for i, ep in enumerate(eps):
            pp = "%s[%d]" % (path, i)
            if not isinstance(ep, dict):
                self.err(pp, 'must be an object like {"method": "POST", "path": "/login", "evidence": [...]}')
                continue
            m, pth = ep.get("method"), ep.get("path")
            if not (is_str(m) and re.match(r"^[A-Z][A-Z0-9_-]{1,11}$", m)):
                self.err(pp + ".method", 'is %r; use the HTTP method or protocol in capitals, e.g. "GET", "POST", "WS", "GRPC"' % (m,))
            if not is_str(pth):
                self.err(pp + ".path", 'is required: the full public path, e.g. "/api/users/{id}"')
            elif (m, pth) in seen:
                self.err(pp, 'duplicate endpoint "%s %s"' % (m, pth))
            seen.add((m, pth))
            self.check_evidence(pp + ".evidence", ep.get("evidence"))

    @staticmethod
    def suggest(value, node_ids):
        if not isinstance(value, str):
            return ""
        close = difflib.get_close_matches(value, list(node_ids), n=3, cutoff=0.6)
        if not close:
            close = [n for n in node_ids if value.lower() in n.lower() or n.lower() in value.lower()]
        return " (did you mean %s?)" % quoted_list(close[:3]) if close else ""

    def check_flows(self, flows, node_ids, node_type, edge_pairs):
        if flows is None:
            return
        if not isinstance(flows, list):
            self.err("flows", "must be a list")
            return
        if not flows:
            self.warn("flows", "is empty: no request journey will be shown")
        seen = {}
        for i, f in enumerate(flows):
            p = "flows[%d]" % i
            if not isinstance(f, dict):
                self.err(p, "must be an object")
                continue
            fid = self.check_id(p, f.get("id"), seen, "flow")
            if fid:
                p = "flows[%d](%s)" % (i, fid)
            if not is_str(f.get("name")):
                self.err(p + ".name", 'is required, e.g. "POST /login"')
            if not is_str(f.get("summary")):
                self.err(p + ".summary", "is required (one sentence)")
            if "notes" in f and f["notes"] is not None and not isinstance(f["notes"], str):
                self.err(p + ".notes", "must be a string when present")
            explained = is_str(f.get("notes"))
            steps = f.get("steps")
            step_ids = {}
            main_idx = {}
            if not isinstance(steps, list) or not steps:
                self.err(p + ".steps", "is required and must be a non-empty ordered list")
                steps = []
            for j, st in enumerate(steps):
                sid = self.check_step("%s.steps[%d]" % (p, j), st, node_ids, step_ids)
                if sid:
                    main_idx[sid] = j
            # round-trip rules on the main path
            if steps and all(isinstance(s, dict) for s in steps) and not explained:
                first, last = steps[0], steps[-1]
                if node_type.get(first.get("node")) not in ENDPOINT_TYPES or first.get("direction") != "request":
                    self.err(p + ".steps[0]", 'every flow starts with a "request" step at the "client" node ("%s"), or at an external-caller node '
                                              "for webhooks and scheduled calls (or explain why not in the flow's \"notes\")" % CLIENT_NAME)
                if last.get("direction") != "response" or last.get("node") != first.get("node"):
                    self.err(p + ".steps[%d]" % (len(steps) - 1),
                             'the main path must end with a "response" step back at the node where the flow started ("%s"). '
                             "Trace the full return path, or explain in the flow's \"notes\" (e.g. fire-and-forget)" % first.get("node"))
            branches = f.get("branches")
            branch_ids = {}
            if branches is not None and not isinstance(branches, list):
                self.err(p + ".branches", "must be a list when present")
                branches = []
            for b_i, b in enumerate(branches or []):
                self.check_branch("%s.branches[%d]" % (p, b_i), b, node_ids, node_type, step_ids, main_idx, branch_ids, explained, steps[0].get("node") if steps and isinstance(steps[0], dict) else None)
            self.check_hops(p, f, node_ids, node_type, edge_pairs)

    def check_step(self, p, st, node_ids, step_ids):
        if not isinstance(st, dict):
            self.err(p, "must be an object")
            return None
        sid = st.get("id")
        if not is_str(sid):
            self.err(p + ".id", 'is required and must be unique within the flow ("s1", "s2", ... or "b1" in branches)')
            sid = None
        elif sid in step_ids:
            self.err(p + ".id", 'duplicate step id "%s" within this flow (first at %s)' % (sid, step_ids[sid]))
        else:
            step_ids[sid] = p
        n = st.get("node")
        if not is_str(n):
            self.err(p + ".node", "is required: a node id")
        elif n not in node_ids:
            self.err(p + ".node", 'node id "%s" does not exist in "nodes"%s' % (n, self.suggest(n, node_ids)))
        if st.get("direction") not in DIRECTIONS:
            self.err(p + ".direction", 'is %r; must be "request" or "response"' % (st.get("direction"),))
        if not is_str(st.get("action")):
            self.err(p + ".action", "is required: what happens here, in one sentence")
        if "data" in st and st["data"] is not None and not isinstance(st["data"], str):
            self.err(p + ".data", "must be a string when present")
        self.check_evidence(p + ".evidence", st.get("evidence"))
        return sid

    def check_branch(self, p, b, node_ids, node_type, step_ids, main_idx, branch_ids, explained, start_node=None):
        if not isinstance(b, dict):
            self.err(p, "must be an object")
            return
        bid = b.get("id")
        if not is_str(bid):
            self.err(p + ".id", 'is required, e.g. "cache-hit"')
        elif bid in branch_ids:
            self.err(p + ".id", 'duplicate branch id "%s" within this flow' % bid)
        else:
            branch_ids[bid] = p
        for k in ("label", "condition"):
            if not is_str(b.get(k)):
                self.err("%s.%s" % (p, k), "is required")
        after = b.get("after")
        if not is_str(after) or after not in main_idx:
            self.err(p + ".after", '%r is not the id of a main-path step; use one of: %s'
                     % (after, quoted_list(list(main_idx)) or "(none)"))
        if "rejoin" not in b:
            self.err(p + ".rejoin", 'is required: a main-path step id, or null if the branch ends the journey')
        rejoin = b.get("rejoin")
        if rejoin is not None:
            if not is_str(rejoin) or rejoin not in main_idx:
                self.err(p + ".rejoin", '%r is not the id of a main-path step; use one of: %s or null'
                         % (rejoin, quoted_list(list(main_idx)) or "(none)"))
            elif after in main_idx and main_idx[rejoin] <= main_idx[after]:
                self.err(p + ".rejoin", 'step "%s" must come AFTER the "after" step "%s" on the main path' % (rejoin, after))
        steps = b.get("steps")
        if not isinstance(steps, list) or not steps:
            self.err(p + ".steps", "is required and must be a non-empty list")
            steps = []
        for j, st in enumerate(steps):
            self.check_step("%s.steps[%d]" % (p, j), st, node_ids, step_ids)
        if "outcome" in b and b["outcome"] is not None:
            o = b["outcome"]
            if not isinstance(o, dict):
                self.err(p + ".outcome", 'must be an object like {"status": 401, "description": "..."}')
            else:
                if "status" in o and not is_int(o["status"]):
                    self.err(p + ".outcome.status", "must be an integer")
                if "description" in o and not isinstance(o["description"], str):
                    self.err(p + ".outcome.description", "must be a string")
        if rejoin is None and "rejoin" in b and steps and not explained and isinstance(steps[-1], dict):
            last = steps[-1]
            if last.get("direction") != "response" or (start_node is not None and last.get("node") != start_node):
                self.err("%s.steps[%d]" % (p, len(steps) - 1),
                         'a branch with "rejoin": null ends the journey, so its last step must be a "response" '
                         'back at the node where the flow started ("%s"), or explain in the flow\'s "notes"' % start_node)

    def linked(self, x, y, node_type, edge_pairs):
        """True if x and y share an edge (either way) or are linked through
        middleware-only nodes (middleware that does nothing on that side)."""
        adj = {}
        for a, b in edge_pairs:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        ow = self.owner_of
        if node_type.get(y) == "middleware" and ow.get(y) and ((x, ow[y]) in edge_pairs or (ow[y], x) in edge_pairs):
            return True  # an entry edge to the owning service covers the hop into its middleware chain
        if node_type.get(x) == "middleware" and ow.get(x) and ((y, ow[x]) in edge_pairs or (ow[x], y) in edge_pairs):
            return True
        seen, stack = {x}, [x]
        while stack:
            cur = stack.pop()
            for nxt in adj.get(cur, ()):
                if nxt == y:
                    return True
                if nxt not in seen and node_type.get(nxt) == "middleware":
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def check_hops(self, p, f, node_ids, node_type, edge_pairs):
        """Warn when consecutive steps move between nodes that have no edge."""
        main = [s for s in (f.get("steps") or []) if isinstance(s, dict)]
        seqs = [main]
        for b in f.get("branches") or []:
            if not isinstance(b, dict):
                continue
            ids = [s.get("id") for s in main]
            if b.get("after") not in ids:
                continue
            ai = ids.index(b.get("after"))
            seq = main[:ai + 1] + [s for s in (b.get("steps") or []) if isinstance(s, dict)]
            if b.get("rejoin") in ids:
                seq += main[ids.index(b.get("rejoin")):]
            seqs.append(seq)
        warned = set()
        for seq in seqs:
            for a, b in zip(seq, seq[1:]):
                x, y = a.get("node"), b.get("node")
                if x == y or x not in node_ids or y not in node_ids:
                    continue
                if (x, y) in warned or self.linked(x, y, node_type, edge_pairs):
                    continue
                warned.add((x, y))
                self.warn(p, 'steps move %s -> %s but "edges" has no connection between them; add one with evidence' % (x, y))

    def check_uncertain(self, unc, node_ids, edge_pairs):
        if unc is None:
            return
        if not isinstance(unc, list):
            self.err("uncertain", "must be a list (use [] when there is nothing uncertain)")
            return
        for i, u in enumerate(unc):
            p = "uncertain[%d]" % i
            if not isinstance(u, dict):
                self.err(p, "must be an object")
                continue
            for k in ("source", "target", "reason", "would_confirm"):
                if not is_str(u.get(k)):
                    self.err("%s.%s" % (p, k), "is required and must be a non-empty string")
            if u.get("suspected_kind") not in EDGE_KINDS:
                self.err(p + ".suspected_kind", 'is %r; must be one of: %s' % (u.get("suspected_kind"), quoted_list(EDGE_KINDS)))
            if "hints" in u and u["hints"] is not None:
                self.check_evidence(p + ".hints", u["hints"], required=False)
            s, t = u.get("source"), u.get("target")
            if s in node_ids and t in node_ids and (s, t) in edge_pairs:
                self.warn(p, "%s -> %s is already a proven edge; remove it from uncertain" % (s, t))

    def check_meta(self, meta):
        if meta is None:
            return
        if not isinstance(meta, dict):
            self.err("meta", "must be an object when present")
            return
        if "generated_at" in meta and not isinstance(meta["generated_at"], str):
            self.err("meta.generated_at", "must be a string")
        for k in ("skipped", "limitations"):
            if k in meta and (not isinstance(meta[k], list) or not all(isinstance(x, str) for x in meta[k])):
                self.err("meta." + k, "must be a list of strings")

    def check_importance(self, data, node_ids):
        """importance = how many flows pass through the node; warn only when it is wildly off."""
        flows = [f for f in (data.get("flows") or []) if isinstance(f, dict)]
        if not flows:
            return
        count = {}
        for f in flows:
            seen = set()
            for s in f.get("steps") or []:
                if isinstance(s, dict):
                    seen.add(s.get("node"))
            for b in f.get("branches") or []:
                for s in (b.get("steps") or []) if isinstance(b, dict) else []:
                    if isinstance(s, dict):
                        seen.add(s.get("node"))
            for nid in seen:
                count[nid] = count.get(nid, 0) + 1
        for nid, n in node_ids.items():
            imp = n.get("importance")
            if not is_int(imp) or not 1 <= imp <= 3:
                continue
            c = count.get(nid, 0)
            share = c / float(len(flows))
            expected = 3 if share >= 0.6 else (2 if (c >= 2 or share >= 0.25) else 1)
            if abs(imp - expected) >= 2:
                self.warn("nodes(%s).importance" % nid, "is %d, but %d of %d flows pass through it (about %d expected). "
                                                        "Importance counts the flows whose steps go through the node" % (imp, c, len(flows), expected))

    def check_middleware_owners(self, data, node_ids, node_type):
        """The overview expands a service's middleware chain in order, so the chain's edges should reach the owner."""
        out = {}
        for e in data.get("edges") or []:
            if isinstance(e, dict) and e.get("kind") != "response":
                out.setdefault(e.get("source"), []).append(e.get("target"))
        for mid, n in node_ids.items():
            owner = n.get("owner")
            if node_type.get(mid) != "middleware" or not is_str(owner) or node_type.get(owner) != "service":
                continue
            if n.get("category") == "error-handler":
                continue  # an exception handler wraps the handlers; it is not a hop in the request chain
            seen, stack, found = {mid}, [mid], False
            while stack and not found:
                for t in out.get(stack.pop(), []):
                    if t == owner:
                        found = True
                        break
                    if t not in seen and node_type.get(t) == "middleware":
                        seen.add(t)
                        stack.append(t)
            if not found:
                self.warn("nodes(%s)" % mid, 'no chain of edges leads from this middleware to its owner "%s". Link the chain with "middleware-next" edges '
                                             "(entry -> first middleware -> ... -> last middleware -> service) so the overview can show it in order" % owner)

    def check_orphans(self, data, node_ids):
        used = set()
        for e in data.get("edges") or []:
            if isinstance(e, dict):
                used.update([e.get("source"), e.get("target")])
        for f in data.get("flows") or []:
            if isinstance(f, dict):
                for s in (f.get("steps") or []):
                    if isinstance(s, dict):
                        used.add(s.get("node"))
                for b in f.get("branches") or []:
                    if isinstance(b, dict):
                        for s in b.get("steps") or []:
                            if isinstance(s, dict):
                                used.add(s.get("node"))
        for nid in node_ids:
            if nid not in used:
                self.warn("nodes(%s)" % nid, "is not connected to anything (no edge or flow step uses it)")


# ---------------------------------------------------------------- rendering
def serialize(data):
    """JSON safe to embed inside a <script type=application/json> element."""
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (s.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def write_atomic(path, text):
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".arch-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fail(msg, code=2):
    print("error: " + msg, file=sys.stderr)
    return code


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Validate architecture.json and render architecture.html.")
    ap.add_argument("--input", default="architecture.json", help="path to architecture.json (default: ./architecture.json)")
    ap.add_argument("--output", help="path for architecture.html (default: next to the input file)")
    ap.add_argument("--root", help="project root used to check evidence files (default: the input file's folder)")
    ap.add_argument("--template", default=str(here.parent / "assets" / "template.html"), help=argparse.SUPPRESS)
    ap.add_argument("--no-fs-check", action="store_true", help="do not check that evidence files and lines exist")
    ap.add_argument("--force", action="store_true", help="overwrite the output even if it was not created by this tool")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_file():
        return fail("input file not found: %s" % inp)
    root = Path(args.root) if args.root else inp.resolve().parent
    out = Path(args.output) if args.output else inp.resolve().parent / "architecture.html"
    tpl = Path(args.template)
    if not tpl.is_file():
        return fail("template not found: %s" % tpl)

    try:
        raw = inp.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        return fail("cannot read %s: %s" % (inp, e))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print("architecture.json is not valid JSON: %s (line %d, column %d). Fix the syntax and run again."
              % (e.msg, e.lineno, e.colno), file=sys.stderr)
        return 1

    v = Validator(str(root.resolve()), fs_check=not args.no_fs_check)
    v.validate(data)

    if v.errors:
        n = len(v.errors)
        print("architecture.json is invalid: %d problem%s. Fix them all in architecture.json, then run render.py again."
              % (n, "" if n == 1 else "s"), file=sys.stderr)
        if v.fs_check:
            print("(evidence files are checked relative to: %s)\n" % v.root, file=sys.stderr)
        else:
            print(file=sys.stderr)
        for i, e in enumerate(v.errors[:MAX_SHOWN], 1):
            print("  %d. %s" % (i, e), file=sys.stderr)
        if n > MAX_SHOWN:
            print("  ... and %d more (fix the above first)" % (n - MAX_SHOWN), file=sys.stderr)
        if v.warnings:
            print("\n%d warning(s) will show after errors are fixed." % len(v.warnings), file=sys.stderr)
        print("\nSchema reference: references/graph-schema.md", file=sys.stderr)
        return 1

    html = tpl.read_text(encoding="utf-8")
    if html.count(TOKEN) != 1:
        return fail("template is broken: expected exactly one %s placeholder, found %d" % (TOKEN, html.count(TOKEN)))
    html = html.replace(TOKEN, serialize(data), 1)

    if out.suffix.lower() not in (".html", ".htm"):
        return fail("--output must be an .html file, got: %s" % out)
    if out.resolve() in (inp.resolve(), tpl.resolve()):
        return fail("refusing to overwrite the input or template file: %s" % out)
    if out.exists() and not args.force:
        try:
            if MARKER not in out.read_text(encoding="utf-8", errors="ignore"):
                return fail("%s exists and was not created by repo-visualizer; refusing to overwrite it. "
                            "Choose another --output or pass --force." % out)
        except OSError as e:
            return fail("cannot inspect existing %s: %s" % (out, e))
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(str(out), html)
    except OSError as e:
        return fail("cannot write %s: %s" % (out, e))

    print("OK: wrote %s" % out.resolve())
    print("    %d components, %d connections, %d flows, %d uncertain"
          % (len(data["nodes"]), len(data["edges"]), len(data["flows"]), len(data["uncertain"])))
    print("    open: %s" % out.resolve().as_uri())
    if v.warnings:
        print("\n%d warning(s) (the file was still written):" % len(v.warnings))
        for w in v.warnings[:15]:
            print("  - " + w)
        if len(v.warnings) > 15:
            print("  ... and %d more" % (len(v.warnings) - 15))
    return 0


if __name__ == "__main__":
    sys.exit(main())
