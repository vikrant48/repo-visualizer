#!/usr/bin/env python3
"""claude_headless.py - run a separate, READ-ONLY headless Claude (claude -p) and get its answer back.

Used by verify_pass.py (the independent verifier) and endpoint_pipeline.py (the analysis run).
The child run is given only Read, Grep and Glob, in "dontAsk" mode, with no session saved, no
slash commands, and deny rules for real .env files. It cannot write, run commands or browse.

Isolation from the project being analysed (it is untrusted): MCP servers are not loaded
(--strict-mcp-config with no config), and only USER settings are read (--setting-sources user), so a
project's .claude/settings.json, hooks, and .mcp.json cannot add tools, run commands, or widen
permissions. Project text still reaches the run as file contents, which the prompts treat as data.

Finding the claude program, in order: the explicit path, the CLAUDE_BIN environment variable,
`claude` on PATH, then the newest Claude Code VS Code extension's bundled binary.
"""
import glob
import json
import os
import shutil
import subprocess

READ_ONLY_TOOLS = "Read,Grep,Glob"
DENIED_TOOLS = "Bash,Write,Edit,NotebookEdit,WebFetch,WebSearch,Task,Agent"
ENV_DENY = ["Read(**/.env)", "Read(**/.env.local)", "Read(**/.env.*.local)", "Read(**/.env.production)",
            "Read(**/.env.prod)", "Read(**/.env.development)", "Read(**/.env.dev)", "Read(**/.env.staging)", "Read(**/.env.test)"]


class HeadlessError(Exception):
    pass


def prompt_json(obj, indent=None):
    """JSON for embedding in a prompt: < and > are escaped (still valid JSON, same value after parsing), so
    text from the repository can never close or open a tag such as </analysis> inside the prompt."""
    s = json.dumps(obj, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def safe_text(s, limit=200):
    """A short single-line text taken from the repository (a name, a path) made safe to place in a prompt."""
    s = "".join(ch if ch.isprintable() else " " for ch in str(s))
    s = " ".join(s.split())
    return s[:limit].replace("<", "(").replace(">", ")")


def find_claude(explicit=None):
    for cand in (explicit, os.environ.get("CLAUDE_BIN")):
        if cand:
            return cand
    found = shutil.which("claude")
    if found:
        return found
    home = os.path.expanduser("~")
    pats = [os.path.join(home, ".vscode", "extensions", "anthropic.claude-code-*", "resources", "native-binary", n)
            for n in ("claude.exe", "claude")]
    hits = sorted(h for p in pats for h in glob.glob(p))
    return hits[-1] if hits else None


def extract_json(text):
    """Return the largest JSON object found in text (whole text, a fenced block, or embedded), or None."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    try:
        v = json.loads(t)
        if isinstance(v, dict):
            return v
    except ValueError:
        pass
    dec = json.JSONDecoder()
    best, best_len = None, 0
    i = t.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(t, i)
            if isinstance(obj, dict) and end - i > best_len:
                best, best_len = obj, end - i
            i = t.find("{", end)
        except ValueError:
            i = t.find("{", i + 1)
    return best


def run_readonly(prompt, root, system=None, schema=None, model=None, timeout=900, claude_bin=None, max_budget=None):
    """Run one headless Claude and return {"text", "json", "cost", "turns", "is_error", "denials"}.

    The prompt goes in through stdin (no command-line length or quoting problems). Raises
    HeadlessError when the program is missing, fails, times out, or reports an error.
    """
    exe = find_claude(claude_bin)
    if not exe:
        raise HeadlessError("the claude program was not found (set CLAUDE_BIN or put claude on PATH)")
    cmd = [exe, "-p", "--tools", READ_ONLY_TOOLS, "--allowedTools", READ_ONLY_TOOLS, "--disallowedTools", DENIED_TOOLS,
           "--permission-mode", "dontAsk", "--no-session-persistence", "--disable-slash-commands",
           "--strict-mcp-config", "--setting-sources", "user",
           "--output-format", "json", "--settings", json.dumps({"permissions": {"deny": ENV_DENY}})]
    if system:
        cmd += ["--append-system-prompt", system]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    if model:
        cmd += ["--model", model]
    if max_budget:
        cmd += ["--max-budget-usd", str(max_budget)]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=root, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HeadlessError("the headless run timed out after %d seconds" % timeout)
    except OSError as e:
        raise HeadlessError("the claude program could not be started: %s" % e)
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise HeadlessError("the headless run did not return JSON (exit %s): %s" % (r.returncode, (r.stderr or r.stdout)[:300].strip()))
    if not isinstance(data, dict):
        raise HeadlessError("unexpected output from the headless run")
    if data.get("is_error") or r.returncode not in (0,) and not data.get("result"):
        raise HeadlessError("the headless run reported an error: %s" % str(data.get("result") or data.get("subtype") or r.stderr)[:300])
    structured = data.get("structured_output")
    text = data.get("result") if isinstance(data.get("result"), str) else ""
    parsed = structured if isinstance(structured, dict) else extract_json(text)
    return {"text": text, "json": parsed, "cost": data.get("total_cost_usd"), "turns": data.get("num_turns"),
            "is_error": bool(data.get("is_error")), "denials": data.get("permission_denials") or []}
