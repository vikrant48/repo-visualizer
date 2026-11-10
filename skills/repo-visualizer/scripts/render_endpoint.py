#!/usr/bin/env python3
"""render_endpoint.py - turn an endpoint-analysis.json into a standalone page and a terminal summary.

    python3 render_endpoint.py --input endpoint-analysis.json [--out endpoint-analysis.html] [--open] [--text]

The page is the same viewer that serve.py serves (assets/endpoint-viewer.html) with the analysis
embedded, so it works from a file with no server and no network: scenario tabs, the three panels,
the animated journey, verification badges with evidence, and scenario comparison. Searching for
other endpoints needs the server (/repo-visualizer:visualize-live).

Safety: the page carries a strict Content-Security-Policy (no network at all, scripts and styles
only with this page's nonce); the data is embedded as inert JSON with < > & escaped; the script
refuses to overwrite an HTML file that it did not create (it must start with the marker comment).

Standard library only. Exit code: 0 ok, 1 refused (existing file is not ours), 2 bad input.
"""
import argparse
import json
import os
import secrets
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), "assets", "endpoint-viewer.html")
MARKER = "<!-- repo-visualizer endpoint page (generated; safe to delete) -->"


def embed_json(obj):
    """JSON that cannot close a script element or start an HTML comment."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (s.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_page(doc, template_path=TEMPLATE):
    """Return the HTML text for an analysis (a dict as written by endpoint_pipeline.py)."""
    with open(template_path, encoding="utf-8") as fh:
        page = fh.read()
    nonce = secrets.token_urlsafe(16)
    ver = doc.get("verification") or {}
    data = {"analysis": doc, "status": "done_with_problems" if (ver.get("pass") or {}).get("major") else "done",
            "rerun": ((ver.get("pass") or {}).get("analysisAttempts") or 1) > 1}
    csp = ("default-src 'none'; script-src 'nonce-%s'; style-src 'nonce-%s'; img-src data:; base-uri 'none'; form-action 'none'" % (nonce, nonce))
    head_extra = '<meta http-equiv="Content-Security-Policy" content="%s">\n<meta name="referrer" content="no-referrer">\n' % csp
    if "<head>" not in page or '<script nonce="{{NONCE}}">' not in page:
        raise ValueError("the viewer template does not have the expected structure")
    page = page.replace("<head>\n", "<head>\n" + head_extra, 1)
    embedded = '<script type="application/json" id="embedded">%s</script>\n' % embed_json(data)
    page = page.replace('<script nonce="{{NONCE}}">', embedded + '<script nonce="{{NONCE}}">', 1)
    return MARKER + "\n" + page.replace("{{NONCE}}", nonce)


def write_page(doc, out_path, template_path=TEMPLATE):
    """Write the page. Returns (ok, message). Refuses to replace a file that is not one of ours."""
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8", errors="replace") as fh:
                first = fh.readline().strip()
        except OSError as e:
            return False, "cannot read the existing %s (%s)" % (out_path, e.__class__.__name__)
        if first != MARKER:
            return False, "%s already exists and was not created by repo-visualizer; it was not overwritten" % out_path
    html = render_page(doc, template_path)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    return True, out_path


def summary_text(doc, route=None, html_path=None, json_path=None):
    """A plain-text summary for the terminal (ASCII only, so any console can print it)."""
    ep = doc.get("endpoint") or {}
    v = doc.get("verification") or {}
    sm = v.get("summary") or {}
    pas = v.get("pass") or {}
    lines = ["%s %s  (service: %s)" % (ep.get("method", "?"), ep.get("path", "?"), ep.get("service", "?"))]
    h = ep.get("handler") or {}
    if h:
        lines.append("Handler: %s at %s:%s" % (h.get("symbol", "?"), h.get("file", "?"), h.get("line", "?")))
    if ep.get("summary"):
        lines.append(ep["summary"])
    lines.append("")
    lines.append("Verification: %d verified, %d corrected, %d unverified  (analysis ran %s)" % (
        sm.get("verified", 0), sm.get("corrected", 0), sm.get("unverified", 0),
        "twice, the first run had major problems" if (pas.get("analysisAttempts") or 1) > 1 else "once"))
    lines.append("")
    lines.append("Scenarios (%d):" % len(doc.get("scenarios") or []))
    for sc in doc.get("scenarios") or []:
        r = sc.get("response") or {}
        lines.append("  %-4s %-14s %s" % (r.get("status", "?"), sc.get("outcome", ""), sc.get("name", "")))
        lines.append("       when: %s" % sc.get("trigger", ""))
    attention = [i for i in (v.get("items") or []) if i.get("status") != "verified"]
    if attention:
        lines.append("")
        lines.append("Needs attention (%d):" % len(attention))
        for i in attention[:8]:
            lines.append("  [%s] %s: %s" % (i.get("status"), i.get("target"), (i.get("reason") or "")[:160]))
            if i.get("status") == "corrected":
                lines.append("      was: %s -> now: %s" % (i.get("was"), i.get("now")))
        if len(attention) > 8:
            lines.append("  ... and %d more in the page" % (len(attention) - 8))
    lim = (doc.get("meta") or {}).get("limitations") or []
    if lim:
        lines.append("")
        lines.append("Limitations:")
        for x in lim[:6]:
            lines.append("  - %s" % x)
        if len(lim) > 6:
            lines.append("  ... and %d more" % (len(lim) - 6))
    if json_path or html_path:
        lines.append("")
        if json_path:
            lines.append("Data: %s" % json_path)
        if html_path:
            lines.append("Page: %s" % html_path)
    return "\n".join(lines)


def open_in_browser(path):
    try:
        return bool(webbrowser.open("file:///" + os.path.abspath(path).replace("\\", "/").lstrip("/")))
    except Exception:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render an endpoint-analysis.json as a standalone page.")
    ap.add_argument("--input", required=True, help="endpoint-analysis.json")
    ap.add_argument("--out", default="endpoint-analysis.html", help="output page (default: endpoint-analysis.html next to the input)")
    ap.add_argument("--open", action="store_true", help="open the page in the default browser")
    ap.add_argument("--text", action="store_true", help="print a plain-text summary")
    args = ap.parse_args(argv)
    try:
        with open(args.input, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        print("cannot read %s: %s" % (args.input, e), file=sys.stderr)
        return 2
    if not isinstance(doc, dict) or not isinstance(doc.get("scenarios"), list) or not isinstance(doc.get("endpoint"), dict):
        print("%s is not an endpoint-analysis.json" % args.input, file=sys.stderr)
        return 2
    out = args.out if os.path.isabs(args.out) or os.sep in args.out or "/" in args.out else os.path.join(os.path.dirname(os.path.abspath(args.input)), args.out)
    ok, msg = write_page(doc, out)
    if not ok:
        print(msg, file=sys.stderr)
        return 1
    if args.text:
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
        print(summary_text(doc, json_path=args.input, html_path=out))
    else:
        print(json.dumps({"page": out}))
    if args.open:
        open_in_browser(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
