# repo-visualizer

A [Claude Code](https://claude.com/claude-code) plugin that reads your **backend** code and generates an interactive page, `architecture.html`, showing:

- **The full journey of a request:** the Client / User, gateway, middleware (auth, CORS, rate limit, validation, security headers), services, cache, database or queue, and the response travelling back to the client.
- **How all your services connect**, as one overview map.

It is backend-only: frontend code (Angular, React, Vue, mobile apps, JSP/JSF pages) is never read, and every frontend is one node, **Client / User**, connected to your gateway (or to the service that exposes the endpoint) by a single line that lists its endpoints. Anything else that calls your backend, such as a payment webhook or an external cron, gets its own node.

Every component, connection, and step in the page is backed by a `file:line` reference to your backend code. Anything Claude suspects but cannot prove is listed separately as *uncertain* instead of being drawn as fact.

## Install

In Claude Code:

```
/plugin marketplace add vikrant48/repo-visualizer
/plugin install repo-visualizer@repo-visualizer
```

Or from a terminal:

```
claude plugin marketplace add vikrant48/repo-visualizer
claude plugin install repo-visualizer@repo-visualizer
```

**Requirements:** Python 3.8+ (`python3` or `python`) on your PATH. No packages to install.

**New to this, or using the VS Code extension?** Follow the step-by-step [Getting started guide](docs/GETTING-STARTED.md). The extension's chat panel may not accept `/plugin`, and the guide shows the terminal route that works.

## Update

When a new version is published, update the marketplace listing and then the plugin. Run these in a terminal, then start a new Claude Code session:

```
claude plugin marketplace update repo-visualizer
claude plugin update repo-visualizer@repo-visualizer
```

To check which version you have, run `claude plugin list`. If the update does not show a newer version, run the first command again, because the plugin only updates when the marketplace listing has a higher `version`.

## Usage

Run it inside your project:

```
/repo-visualizer:visualize                      # architecture overview of the whole backend
/repo-visualizer:visualize billing-service      # overview focused on one service
/repo-visualizer:visualize-live                 # start the interactive viewer in your browser
/repo-visualizer:endpoint "POST /api/login"     # analyze one endpoint, print a summary, open the result page
```

Plugin commands are namespaced (`/repo-visualizer:<command>`). You can also just ask in plain words ("diagram how requests flow through this project", "what can go wrong in POST /login?") and the skill activates.

### How each command works

| Command | What it does | Writes | Needs |
|---|---|---|---|
| `/repo-visualizer:visualize [service]` | **Architecture overview, backend only.** Claude reads your infrastructure files and code, writes the architecture as JSON, and a script validates it and renders the page. For large projects it renders an overview first, then traces the 5 most important flows. Frontends are never read: they are one "Client / User" node. | `architecture.json`, `architecture.html` in your project root | Python (falls back to Glob if missing) |
| `/repo-visualizer:visualize-live` | **Starts the live viewer.** Runs `scripts/serve.py` in the background (a local server on `127.0.0.1`, random port, session token in the link) and opens the viewer in your browser. You type any endpoint; it is analyzed on demand, verified, and shown. One analysis at a time, results cached by endpoint and code. | Nothing in your project (the cache lives outside it) | Python, the `claude` program on PATH |
| `/repo-visualizer:endpoint "POST /api/login"` | **The same analysis without the server.** Runs locate, analyze, verify (and one re-run if needed), prints a plain-text summary in the terminal, writes the result, and opens the saved page in your browser. The page is a copy of one analysis; to browse other endpoints use the live viewer. If several routes match it asks you to pick; if none match it shows the closest. | `endpoint-analysis.json`, `endpoint-analysis.html` in your project root | Python, the `claude` program on PATH |

What you can type as an endpoint: `POST /api/login`, `/api/login`, `login`, or a handler name such as `getPHHireCount` (at most 200 characters; anything that is not a method, path or name is refused).

Open `architecture.html` (overview) or `endpoint-analysis.html` (endpoint) in a browser if it does not open by itself. The overview and the saved endpoint page are both plain files you can keep or delete.

For large projects and monorepos, the overview is built from your infrastructure files first, then Claude traces the 5 most important flows. Use `/repo-visualizer:visualize <service>` to dig into one service, and the endpoint commands to dig into one endpoint.

## What you get

| View | What it shows |
|---|---|
| **Overview map** | Level 1 shows frontends, services, data stores and external systems, left to right, with a shield on each service showing how many middleware protect it. Click a service to expand its middleware chain (in order) and only its direct connections; everything else fades. Parallel connections are merged into one line with a count. Services can be grouped into collapsible domain boxes. Every component is classified by business domain, tier (which column it sits in) and importance (how many flows pass through it); middleware name the service they belong to. Hover a node to highlight its neighbours; use Filters for component types, connection kinds and importance; unconfirmed links are off by default; search jumps to a node and focuses its neighbourhood. |
| **Request journey** | Pick a flow, then step through it. A dot travels forward along the request path and back along the response path. Toggle branches such as cache hit vs miss, or an early 401. |
| **Sequence diagram** | The same flow as a sequence diagram. |

In every view: click a component, connection, or step to see what it does and its evidence; search; light and dark mode; and a list of uncertain connections.

## Endpoint mode (on demand)

Type an endpoint (`POST /api/login`, `/api/login`, `login`, or a handler name such as `getPHHireCount`) and Claude analyzes just that endpoint. Nothing is pre-traced. The result is `endpoint-analysis.json`:

- **Every scenario**: success, each validation failure, auth or permission failure, cache hit or miss, not found, conflict, downstream failure or timeout.
- **Request and response as keys and types only**: headers by name, path and query parameters, body key trees with validation rules, response status and body key trees. Never example values.
- **The journey**: ordered steps through middleware, services, cache, database, queue and external calls, each with `file:line` and the exact quoted code.
- **Verified**: a script checks that every cited file, line, snippet, key and status code really is in your code, then a separate read-only Claude run that does not trust the first analysis re-reads the code. Each item is marked verified, corrected, or unverified (with a reason). Major errors trigger one re-run, then verification again.

Locating the route is a fast script (Spring, JAX-RS, Express, NestJS, FastAPI, Flask, Django, Go, including router, context-path and gateway prefixes); Claude is used only when the script cannot find it. If several routes match you pick one. The analysis and verification runs are headless `claude -p` runs limited to Read, Grep and Glob.

**Viewer:** `python3 scripts/serve.py --root . --open` (or ask Claude to start it) serves a local page on `127.0.0.1` at a random port. Type an endpoint ("Enter an endpoint, e.g. POST /api/login") and press Analyze; if several routes match you pick one. A progress bar shows Locating, Analyzing, Verifying (and Re-analyzing if the verifier found major problems). The result has a tab per scenario, colored by status code (2xx green, 4xx amber, 5xx red), and three panels for the selected scenario:

1. **Request**: a collapsible key tree with type, required or optional, and validation rules.
2. **Journey**: an animated path from Client through middleware, services, cache and database and back (request path solid, response path dashed, numbered steps), with the step list beside it. Play, pause, step, or click a node or step.
3. **Response**: the status code and a collapsible key tree with types.

Every key and step has a badge: ✓ verified, ✎ corrected, ? unverified. Click a badge to see the file, line and quoted code. **Compare scenarios** puts two scenarios side by side and shows where their paths split (for example cache hit versus miss), plus the request and response keys that differ. A history row lists the endpoints analyzed in this page session; click one to show it again. One job runs at a time and the rest queue. Results are cached outside your project by endpoint and code fingerprint (git commit plus uncommitted changes), so asking again is instant until the code changes; **Re-analyze** runs it again anyway. The link printed at start contains a random session token that every API call must carry; the server also checks the Host and Origin headers and never writes into your project. Each headless run times out after 5 minutes (`--timeout`), and a job can use up to four runs.

## Privacy and safety

- Analysis happens locally in your Claude Code session. The plugin makes no network calls of its own.
- Claude only creates `architecture.json` and `architecture.html` (overview), or `endpoint-analysis.json` and `endpoint-analysis.html` (endpoint command). The live viewer writes nothing into your project. The render scripts refuse to overwrite an HTML file they did not create. It does not modify your code, and the render script refuses to overwrite an `architecture.html` it did not create.
- Secrets are never copied into the output: only environment variable *names*. Real `.env` files are not read, only `.env.example`-style templates, and the render script rejects output that looks like a secret.
- Opening `architecture.html` loads three pinned JavaScript libraries from the jsDelivr CDN (Cytoscape.js, ELK, Mermaid), checked with Subresource Integrity hashes. The overview map and sequence diagram need an internet connection to draw. The request journey and the text fallback work offline. The page contains your architecture data, so treat it like your source code when sharing it.

### Security model of endpoint mode and the live viewer

The code being analyzed is treated as untrusted. What is enforced, and where:

- **The search box cannot change Claude's instructions.** The typed text is reduced to an optional HTTP method plus one path or name made of letters, digits and `_ - . / { } : $ * ~ % @` (at most 200 characters; sentences, quotes, spaces beyond the method, `..` and markup are refused). It is only ever compared with route strings. Claude is started only after the text resolves to a real route, and **the typed text is not put in any prompt at all**: the prompt gets only a whitelisted, sanitised description of the located route (method, path, handler, file, line). Source lines are not passed to prompts either.
- **Text from your repository is data.** It reaches the analysis and the verifier only as file contents and as JSON in which `<` and `>` are escaped, so a snippet like `</analysis>` cannot close a prompt section. Both runs are told that repository text is never an instruction.
- **The headless Claude runs are read-only and isolated.** They get only Read, Grep and Glob (`--tools`), in `dontAsk` mode, with Bash, Write, Edit, web and agent tools denied, no session saved, no slash commands, deny rules for real `.env` files, no MCP servers, and **only user settings** (a project's `.claude/settings.json`, hooks and `.mcp.json` are ignored). A hostile project folder was tested with a real `.env`, a hook, an MCP server, `permissions.allow: Bash(*)`, an injected `CLAUDE.md` and a source file that orders a command: nothing ran, nothing was written, the `.env` value was not read.
- **The server is local and authenticated.** It binds to `127.0.0.1` only. Every API call needs a random session token (constant-time compare); the token travels in the URL fragment and is removed from the address bar. The Host header must be `127.0.0.1` or `localhost` with the server's port (DNS rebinding), Origin and Sec-Fetch-Site must be same-origin, bodies must be JSON of at most 4 KB, sockets time out after 20 seconds, there are no CORS headers, and the page has a strict Content-Security-Policy.
- **No shell anywhere.** Subprocesses (`claude`, `git`) get argument lists; the prompt goes in through stdin. The scripts contain no `shell=True`, `os.system`, `eval` or unsafe deserialization (checked by a test).
- **Keys and types only in any output.** The checker rejects value, example and default keys, emails, JWTs, UUIDs, bearer tokens, credential assignments and private keys, and the result is **redacted**: offending text is replaced and that item becomes `unverified`. The verifier's own notes and the problems list are scrubbed too, and the route scanner masks source lines that look like secrets. The viewer shows all repository text as text, never as HTML.
- **Evidence paths stay inside the project.** Cited files must be relative, may not leave the project (links included) and may not be `.env` files.

Residual risks you should know about: (1) the commands tell Claude to refuse an unsafe endpoint text before it reaches a shell; that check is an instruction followed by the model, backed by Claude Code's own permission matching of the allowed script commands, not by code. (2) The session token is printed once so Claude can hand you the link; it stays in your local session transcript. (3) Results are cached as files outside your project (under your user profile) and contain code snippets from your repository. (4) The pattern-based value and secret detection can miss an unusual secret format. (5) Claude Code reads a project's `CLAUDE.md` as context, which is text from the repository.

## Limitations

- The map is built by Claude reading your code, so it reflects what could be traced. Check the *uncertain* list, and treat the diagram as a starting point, not a guarantee. Dynamic dispatch, reflection, and runtime configuration can hide connections.
- The inventory step uses Python from your PATH. If Python is missing, Claude falls back to searching files itself, which is slower on big repos.
- Framework guidance covers Express, NestJS, FastAPI, Django, Flask, Spring Boot, Go (net/http, gin, echo, chi), Laravel, and Rails. Other stacks work but with less guidance.

## Repository layout

```
.claude-plugin/plugin.json        plugin manifest
.claude-plugin/marketplace.json   marketplace listing (lets you install from this repo)
commands/visualize.md             /repo-visualizer:visualize (architecture overview)
commands/visualize-live.md        /repo-visualizer:visualize-live (starts the viewer server)
commands/endpoint.md              /repo-visualizer:endpoint (one endpoint, summary + page)
skills/repo-visualizer/
  SKILL.md                        the workflow Claude follows
  references/                     detection patterns, architecture.json and endpoint-analysis.json schemas, endpoint analysis guide
  scripts/                        inventory.py, render.py (overview); locate.py, verify.py, verify_pass.py, endpoint_pipeline.py, render_endpoint.py, serve.py (endpoint mode)
  assets/template.html            the overview page template (architecture.html)
  assets/endpoint-viewer.html     the endpoint viewer (served live, or embedded in endpoint-analysis.html)
```

## Development

Test locally without publishing:

```
/plugin marketplace add /path/to/repo-visualizer
/plugin install repo-visualizer@repo-visualizer
```

Validate the manifests with `claude plugin validate .` Edits to a local plugin apply on the next session or `/reload-plugins`. If you publish a change, bump `version` in both `plugin.json` and `marketplace.json`; installed users only update when the version changes.

## License

MIT. See [LICENSE](LICENSE).
