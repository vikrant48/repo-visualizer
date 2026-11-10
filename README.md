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

## Usage

Run it inside your project:

```
/repo-visualizer:visualize                  # map the whole project
/repo-visualizer:visualize "POST /login"    # one endpoint's request and response journey
/repo-visualizer:visualize billing-service  # one service and its connections
```

Plugin commands are namespaced, so the command is `/repo-visualizer:visualize`. You can also just ask in plain words ("diagram how requests flow through this project") and the skill activates.

Claude writes `architecture.json` and `architecture.html` to your project root. Open `architecture.html` in a browser.

For large projects and monorepos, Claude first renders an overview built from your infrastructure files, then traces the 5 most important flows. Use the focused forms above to dig into more.

## What you get

| View | What it shows |
|---|---|
| **Overview map** | Level 1 shows frontends, services, data stores and external systems, left to right, with a shield on each service showing how many middleware protect it. Click a service to expand its middleware chain (in order) and only its direct connections; everything else fades. Parallel connections are merged into one line with a count. Services can be grouped into collapsible domain boxes. Every component is classified by business domain, tier (which column it sits in) and importance (how many flows pass through it); middleware name the service they belong to. Hover a node to highlight its neighbours; use Filters for component types, connection kinds and importance; unconfirmed links are off by default; search jumps to a node and focuses its neighbourhood. |
| **Request journey** | Pick a flow, then step through it. A dot travels forward along the request path and back along the response path. Toggle branches such as cache hit vs miss, or an early 401. |
| **Sequence diagram** | The same flow as a sequence diagram. |

In every view: click a component, connection, or step to see what it does and its evidence; search; light and dark mode; and a list of uncertain connections.

## Privacy and safety

- Analysis happens locally in your Claude Code session. The plugin makes no network calls of its own.
- Claude only creates `architecture.json` and `architecture.html`. It does not modify your code, and the render script refuses to overwrite an `architecture.html` it did not create.
- Secrets are never copied into the output: only environment variable *names*. Real `.env` files are not read, only `.env.example`-style templates, and the render script rejects output that looks like a secret.
- Opening `architecture.html` loads three pinned JavaScript libraries from the jsDelivr CDN (Cytoscape.js, ELK, Mermaid), checked with Subresource Integrity hashes. The overview map and sequence diagram need an internet connection to draw. The request journey and the text fallback work offline. The page contains your architecture data, so treat it like your source code when sharing it.

## Limitations

- The map is built by Claude reading your code, so it reflects what could be traced. Check the *uncertain* list, and treat the diagram as a starting point, not a guarantee. Dynamic dispatch, reflection, and runtime configuration can hide connections.
- The inventory step uses Python from your PATH. If Python is missing, Claude falls back to searching files itself, which is slower on big repos.
- Framework guidance covers Express, NestJS, FastAPI, Django, Flask, Spring Boot, Go (net/http, gin, echo, chi), Laravel, and Rails. Other stacks work but with less guidance.

## Repository layout

```
.claude-plugin/plugin.json        plugin manifest
.claude-plugin/marketplace.json   marketplace listing (lets you install from this repo)
commands/visualize.md             the /repo-visualizer:visualize command
skills/repo-visualizer/
  SKILL.md                        the workflow Claude follows
  references/                     detection patterns, architecture.json schema
  scripts/                        inventory.py (project scan), render.py (validate + render)
  assets/template.html            the interactive page template
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
