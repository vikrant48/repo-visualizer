#!/usr/bin/env python3
"""Short inventory of a project, for the repo-visualizer skill.

Standard library only. Walks the project (skipping dependency, build and VCS
folders and binary files) and prints a SHORT report of: languages by file
count, manifests, Docker/Kubernetes/IaC files, gateway/proxy configs, env
example files, likely entry points and folders that look like separate
services. Output is capped at MAX_LINES lines even for huge repos.

Read-only. Never prints file contents, only names and classifications. Real
.env files are not listed or read; only *.example / *.sample style templates.

Usage:
    python3 inventory.py --path /path/to/project
    python3 inventory.py            (uses the current directory)
"""
import argparse
import os
import re
import sys

MAX_LINES = 78          # hard cap on report length (limit is 80)
MAX_FILES = 100000      # stop walking after this many files
MAX_SNIFF = 400         # max files whose first bytes are read for classification
MAX_LINE_LEN = 118

SKIP_DIRS = {
    "node_modules", ".git", ".hg", ".svn", "dist", "build", "out", "target",
    "venv", ".venv", "env", ".env", "virtualenv", "vendor", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".idea",
    ".vscode", ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache", "coverage",
    "site-packages", "bower_components", "Pods", ".terraform", "obj",
}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".psd",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg", ".webm",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".jar", ".war", ".ear", ".class", ".exe", ".dll", ".so", ".dylib", ".bin",
    ".o", ".a", ".lib", ".pyc", ".pyo", ".whl", ".egg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".map", ".lock", ".sqlite",
    ".db", ".parquet", ".pb", ".onnx", ".pt", ".h5",
}

LANGS = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala", ".groovy": "Groovy", ".go": "Go", ".rb": "Ruby",
    ".php": "PHP", ".cs": "C#", ".rs": "Rust", ".swift": "Swift",
    ".dart": "Dart", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++",
    ".hpp": "C++", ".ex": "Elixir", ".exs": "Elixir", ".vue": "Vue",
    ".svelte": "Svelte", ".sql": "SQL", ".proto": "Protobuf", ".tf": "Terraform",
    ".sh": "Shell", ".ps1": "PowerShell", ".html": "HTML", ".css": "CSS",
    ".scss": "CSS",
}

MANIFESTS = {
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts", "go.mod", "pyproject.toml",
    "setup.py", "setup.cfg", "pipfile", "cargo.toml", "composer.json",
    "gemfile", "mix.exs", "deno.json", "pubspec.yaml", "build.sbt",
    "pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json",
}
MANIFEST_PATTERNS = (
    re.compile(r"^requirements([-_.].*)?\.txt$"),
    re.compile(r".*\.(csproj|fsproj|sln)$"),
)

COMPOSE_RE = re.compile(r"^(docker-)?compose([.-].*)?\.ya?ml$")
ENV_EXAMPLE_RE = re.compile(
    r"^(\.env\.(example|sample|template|dist|defaults)|example\.env|env\.example)$")

INFRA_NAMES = {
    "chart.yaml": "Helm chart", "kustomization.yaml": "Kustomize",
    "kustomization.yml": "Kustomize", "skaffold.yaml": "Skaffold",
    "helmfile.yaml": "Helmfile", "serverless.yml": "Serverless",
    "serverless.yaml": "Serverless", "procfile": "Procfile",
    "app.yaml": "App Engine/App config", "vercel.json": "Vercel",
    "netlify.toml": "Netlify", "fly.toml": "Fly.io",
    "nginx.conf": None,  # handled as gateway
}

GATEWAY_NAMES = {
    "nginx.conf": "nginx", "traefik.yml": "Traefik", "traefik.yaml": "Traefik",
    "traefik.toml": "Traefik", "kong.yml": "Kong", "kong.yaml": "Kong",
    "kong.conf": "Kong", "envoy.yaml": "Envoy", "envoy.yml": "Envoy",
    "haproxy.cfg": "HAProxy", "caddyfile": "Caddy", "krakend.json": "KrakenD",
    "apisix.yaml": "APISIX", "default.conf": "nginx-style conf",
}

ENTRY_EXACT = {
    "main.py", "app.py", "server.py", "wsgi.py", "asgi.py", "manage.py",
    "__main__.py", "run.py", "main.go", "program.cs", "startup.cs", "artisan",
    "config.ru", "main.rs", "main.dart",
}
ENTRY_JS_BASES = {"app", "server", "main"}          # depth <= 4
ENTRY_INDEX_BASES = {"index"}                       # depth <= 2
JS_EXTS = {".js", ".ts", ".mjs", ".cjs"}
ENTRY_PATH_SUFFIXES = (
    "config/routes.rb", "bootstrap/app.php", "public/index.php",
    "routes/web.php", "routes/api.php",
)
ENTRY_JAVA_RE = re.compile(r"(Application|Main|App)\.(java|kt|scala)$")

NOISE_DIRS = {
    "test", "tests", "__tests__", "spec", "e2e", "examples", "example", "docs",
    "doc", "docs_src", "fixtures", "samples", "sample", "testdata", "mocks",
}


def clamp(s):
    return s if len(s) <= MAX_LINE_LEN else s[:MAX_LINE_LEN - 3] + "..."


def read_head(path, n):
    """Read up to n bytes of a text file; return '' for binary/unreadable."""
    try:
        with open(path, "rb") as f:
            data = f.read(n)
    except OSError:
        return ""
    if b"\x00" in data:
        return ""
    return data.decode("utf-8", "ignore")


def parse_compose_services(text):
    """Very small parser: names under the top-level 'services:' key."""
    names, in_services, indent = [], False, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^services\s*:", line):
            in_services, indent = True, None
            continue
        if in_services:
            if not line.startswith((" ", "\t")):
                break
            cur = len(line) - len(line.lstrip(" \t"))
            if indent is None:
                indent = cur
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*:\s*$", line)
            if m and cur == indent:
                names.append(m.group(1))
    return names


def is_manifest(lname):
    if lname in MANIFESTS:
        return True
    return any(p.match(lname) for p in MANIFEST_PATTERNS)


def scan(root):
    st = {
        "files": 0, "dirs": 0, "truncated": False,
        "langs": {}, "manifests": [], "infra": [], "gateways": [],
        "envs": [], "entries": [], "compose_services": [],
        "k8s": 0, "tf": 0, "markers": {}, "sniffed": 0,
    }

    def mark(dirrel, label):
        st["markers"].setdefault(dirrel, [])
        if label not in st["markers"][dirrel]:
            st["markers"][dirrel].append(label)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        st["dirs"] += 1
        reldir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        reldir = "." if reldir == "." else reldir
        parts = [] if reldir == "." else reldir.split("/")
        in_noise = any(p.lower() in NOISE_DIRS for p in parts)

        for fn in sorted(filenames):
            if st["files"] >= MAX_FILES:
                st["truncated"] = True
                return st
            st["files"] += 1
            lname = fn.lower()
            base, ext = os.path.splitext(lname)
            if ext in BINARY_EXTS or lname.endswith(".min.js"):
                continue
            full = os.path.join(dirpath, fn)
            rel = fn if reldir == "." else reldir + "/" + fn
            depth = len(parts)

            if ext in LANGS:
                st["langs"][LANGS[ext]] = st["langs"].get(LANGS[ext], 0) + 1
            if ext == ".tf":
                st["tf"] += 1

            # manifests
            if is_manifest(lname):
                st["manifests"].append(rel)
                mark(reldir, fn)

            # Docker
            if lname.startswith("dockerfile") or ext == ".dockerfile":
                st["infra"].append((rel, "Dockerfile"))
                mark(reldir, "Dockerfile")
            elif COMPOSE_RE.match(lname):
                st["infra"].append((rel, "Compose"))
                text = read_head(full, 65536)
                for name in parse_compose_services(text):
                    st["compose_services"].append(name)
            elif lname in INFRA_NAMES and INFRA_NAMES[lname]:
                st["infra"].append((rel, INFRA_NAMES[lname]))

            # env templates (real .env files are intentionally ignored)
            if ENV_EXAMPLE_RE.match(lname):
                st["envs"].append(rel)

            # gateways by name
            if lname in GATEWAY_NAMES:
                st["gateways"].append((rel, GATEWAY_NAMES[lname]))
            elif ext == ".conf" and st["sniffed"] < MAX_SNIFF:
                st["sniffed"] += 1
                head = read_head(full, 8192)
                if re.search(r"\b(proxy_pass|upstream\s+\w+|server_name)\b", head):
                    st["gateways"].append((rel, "nginx-style conf"))
            elif (ext in (".yml", ".yaml", ".properties")
                  and base.startswith("application") and st["sniffed"] < MAX_SNIFF):
                st["sniffed"] += 1
                head = read_head(full, 32768)
                if re.search(r"cloud\.gateway|^\s*gateway\s*:", head, re.M):
                    st["gateways"].append((rel, "Spring Cloud Gateway"))

            # Kubernetes / SAM sniff for other yaml
            if (ext in (".yml", ".yaml") and not COMPOSE_RE.match(lname)
                    and lname not in INFRA_NAMES and not base.startswith("application")
                    and st["sniffed"] < MAX_SNIFF):
                try:
                    small = os.path.getsize(full) < 262144
                except OSError:
                    small = False
                if small:
                    st["sniffed"] += 1
                    head = read_head(full, 4096)
                    if re.search(r"^apiVersion\s*:", head, re.M) and re.search(
                            r"^kind\s*:", head, re.M):
                        st["k8s"] += 1
                        if st["k8s"] <= 6:
                            st["infra"].append((rel, "Kubernetes manifest"))
                    elif "AWSTemplateFormatVersion" in head or "AWS::Serverless" in head:
                        st["infra"].append((rel, "AWS SAM/CloudFormation"))

            # entry points (skip tests/docs/examples)
            if not in_noise:
                is_entry = False
                if lname in ENTRY_EXACT and depth <= 4:
                    is_entry = True
                elif ext in JS_EXTS and base in ENTRY_JS_BASES and depth <= 4:
                    is_entry = True
                elif ext in JS_EXTS and base in ENTRY_INDEX_BASES and depth <= 2:
                    is_entry = True
                elif ENTRY_JAVA_RE.search(fn) and depth <= 8:
                    is_entry = True
                elif lname in ("urls.py", "settings.py") and depth <= 3:
                    is_entry = True
                elif any(rel.endswith(s) for s in ENTRY_PATH_SUFFIXES):
                    is_entry = True
                if is_entry:
                    st["entries"].append(rel)
    return st


def build_report(root, st):
    out = []
    add = out.append

    add("== repo-visualizer inventory ==")
    add("Path: " + root)
    note = " (stopped at %d files)" % MAX_FILES if st["truncated"] else ""
    add("Scanned %d files in %d dirs%s; skipped node_modules, .git, dist, build, venv, vendor, binaries."
        % (st["files"], st["dirs"], note))
    add("")

    # languages
    add("Languages (by file count):")
    langs = sorted(st["langs"].items(), key=lambda kv: (-kv[1], kv[0]))
    if langs:
        shown = ["%s %d" % kv for kv in langs[:8]]
        extra = len(langs) - 8
        line = "  " + " | ".join(shown) + (" | +%d more" % extra if extra > 0 else "")
        add(line)
    else:
        add("  none detected")
    add("")

    def listing(title, items, cap):
        add(title)
        if not items:
            add("  none found")
        else:
            for it in items[:cap]:
                add("  " + it)
            if len(items) > cap:
                add("  ... +%d more" % (len(items) - cap))
        add("")

    # manifests (shallowest first)
    mans = sorted(st["manifests"], key=lambda p: (p.count("/"), p))
    listing("Manifests:", mans, 12)

    # infra
    infra_items = sorted(st["infra"], key=lambda t: (t[0].count("/"), t[0]))
    lines = ["%s  [%s]" % (p, kind) for p, kind in infra_items]
    if st["k8s"] > 6:
        lines.append("(+%d more Kubernetes manifests)" % (st["k8s"] - 6))
    if st["tf"]:
        lines.append("%d Terraform file(s)" % st["tf"])
    listing("Docker / Compose / Kubernetes / IaC:", lines, 12)

    if st["compose_services"]:
        seen, names = set(), []
        for n in st["compose_services"]:
            if n not in seen:
                seen.add(n)
                names.append(n)
        txt = ", ".join(names[:25]) + (" ..." if len(names) > 25 else "")
        add("Compose services (%d): %s" % (len(names), txt))
        add("")

    gws = sorted(set(st["gateways"]), key=lambda t: (t[0].count("/"), t[0]))
    listing("Gateway / proxy configs:", ["%s  [%s]" % g for g in gws], 6)

    listing("Env templates (variable names only; real .env files not read):",
            sorted(st["envs"], key=lambda p: (p.count("/"), p)), 4)

    entries = sorted(set(st["entries"]), key=lambda p: (p.count("/"), p))
    listing("Likely entry points:", entries, 12)

    # service folders
    svc = []
    for d, marks in st["markers"].items():
        if d == ".":
            continue
        parts = d.split("/")
        if len(parts) > 3 or any(p.lower() in NOISE_DIRS for p in parts):
            continue
        svc.append((d, marks))
    svc.sort(key=lambda t: (t[0].count("/"), t[0]))
    root_marks = st["markers"].get(".")
    items = ["%s  [%s]" % (d, ", ".join(m[:3])) for d, m in svc]
    if root_marks:
        items.insert(0, "(root)  [%s]" % ", ".join(root_marks[:3]))
    title = "Folders that look like separate services/packages:"
    if len(svc) <= 1 and not st["compose_services"]:
        title += "  (likely a single app)"
    listing(title, items, 15)

    while out and out[-1] == "":
        out.pop()
    out = [clamp(line) for line in out]
    if len(out) > MAX_LINES:
        out = out[:MAX_LINES - 1] + ["... (report truncated)"]
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Short project inventory for repo-visualizer (stdlib only).")
    ap.add_argument("--path", help="project root (default: current directory)")
    ap.add_argument("root", nargs="?", help="same as --path (positional)")
    args = ap.parse_args()

    root = os.path.abspath(args.path or args.root or ".")
    if not os.path.isdir(root):
        print("error: not a directory: %s" % root, file=sys.stderr)
        return 2
    st = scan(root)
    print("\n".join(build_report(root, st)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
