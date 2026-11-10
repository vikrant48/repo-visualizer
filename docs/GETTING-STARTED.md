# Getting started with repo-visualizer

repo-visualizer is a Claude Code plugin for **backends**. You run one command inside your project, and Claude writes `architecture.html`: an interactive page showing how a request travels through your code (gateway, middleware, services, cache, database or queue, and back) and how your services connect.

This guide takes you from nothing installed to a working diagram.

## Before you start

You need:

1. **Claude Code**, either the terminal app (`claude`) or the VS Code extension, signed in.
2. **Python 3.8 or newer** on your PATH. Check with `python --version` (or `python3 --version`). No packages to install.
3. **A web browser** to open the result. Internet access is needed for the overview map and sequence diagram, because their drawing libraries load from a CDN. The request journey view works offline.

## Step 1: Install the plugin (once per computer)

Pick the way that matches how you use Claude Code.

### A. Terminal Claude Code

Start `claude` in any folder, then type:

```
/plugin marketplace add vikrant48/repo-visualizer
/plugin install repo-visualizer@repo-visualizer
```

### B. VS Code extension

The chat panel in the extension may answer `/plugin isn't available in this environment`. If so, install from a normal terminal instead. Open the VS Code terminal (``Ctrl+` ``) and run:

```
claude plugin marketplace add vikrant48/repo-visualizer
claude plugin install repo-visualizer@repo-visualizer
```

If the terminal says `claude` is not found, the extension still ships its own copy. Run it by full path. On Windows it is under:

```
%USERPROFILE%\.vscode\extensions\anthropic.claude-code-<version>-win32-x64\resources\native-binary\claude.exe
```

In PowerShell, set a shortcut for that path, then use it:

```powershell
$claude = "C:\Users\<you>\.vscode\extensions\anthropic.claude-code-<version>-win32-x64\resources\native-binary\claude.exe"
& $claude plugin marketplace add vikrant48/repo-visualizer
& $claude plugin install repo-visualizer@repo-visualizer
```

(On macOS and Linux, look for a similar `native-binary` folder under `~/.vscode/extensions/anthropic.claude-code-*`.)

### Check that it installed

```
claude plugin list
```

You should see `repo-visualizer@repo-visualizer`, version, and `Status: enabled`.

## Step 2: Start a fresh Claude Code session

A session that was already running will not see a newly installed plugin. Do one of:

- Terminal: type `/exit`, then start `claude` again in your project folder.
- VS Code: run **Developer: Reload Window** (`Ctrl+Shift+P`), then start a **new chat**.

If you cannot get the plugin to appear in the VS Code chat panel, run `claude` in the VS Code terminal from your project folder. The command works there.

## Step 3: Run it in your project

Open **your own project's folder** (the repo you want to understand), start Claude Code there, and type:

```
/repo-visualizer:visualize
```

The command is namespaced with the plugin name, so type the full `/repo-visualizer:visualize`.

Claude will work for a minute or two and may ask permission to:

- run the plugin's two Python scripts (a project scan and the final render), and
- write `architecture.json` in your project folder.

Approving these is expected. Claude does not change your code. The only files it creates are `architecture.json` and `architecture.html` in your project root.

When it finishes, it prints the path of `architecture.html` and a short summary, including anything it could not prove.

## Step 4: Open the result

Double-click `architecture.html` in your project folder, or open it in any browser.

### What you will see

| Tab | What it is for |
|---|---|
| **Overview map** | Every component and connection, left to right (user to databases). Middleware is grouped as a "security layer". |
| **Request journey** | Pick a flow (for example `POST /login`) and step through it. A dot travels forward along the request path, then back along the response path. |
| **Sequence diagram** | The same flow as a classic sequence diagram. |

### Using the page

- **Right panel:** opens on the **Legend** tab. Click a component type in the legend to hide or show it. Click any component, connection, or step to see **Details**: what it does and the exact `file:line` that proves it (with a Copy button).
- **Backend only:** frontend code (Angular, React, Vue, Svelte, mobile apps, JSP/JSF pages, server-side templates) is skipped and listed as an "ignored frontend" in the project scan. All of it is represented by one node, **Client / User**, alone on the far left. It connects to your gateway (or, with no gateway, to the service that exposes the endpoint) with one line per entry point; the line shows how many endpoints it carries, and clicking it lists them. A webhook sender, an external scheduler, or another system calling your API appears as its own **External caller** node.
- **Overview levels:** the map starts at Level 1: frontends, services, data stores, and external systems. A yellow shield on a service is its middleware count (hover for the kinds: auth, CORS, rate limit, ...). **Click a service** to expand it into Level 2: its middleware chain in order plus only its direct connections, with everything else faded. Use **Back to overview** (or `Esc`) to return. Several connections between the same two components are merged into one line, thicker for more connections, with a count (`×3`). Click a line to see what it contains. Edge labels are off by default; hover a line or tick **Edge labels**. When services belong to business domains, they sit in dashed boxes: click a box title to collapse it, or use **Collapse domains**.
- **Focus tools (overview):** hover a component to highlight it and its direct neighbours; the rest fades and only the highlighted lines show labels. Click it to keep the highlight, and click empty space to clear it. **Filters** shows or hides component types (frontends, services, databases, caches, queues, external systems), connection kinds (HTTP/gRPC, database, cache, queue), and components by importance (Core, Supporting, Minor: how many flows pass through them; important components are drawn larger and bolder); tick **Middleware** there to draw every service's middleware chain at once. **Show unconfirmed** (off by default) adds the links Claude could not prove. Searching jumps to a component and focuses its neighbourhood. **Fit** fits the screen and **Reset** restores the default view.
- **See the graph larger:** click **Expand graph** (or the **✕** on the panel, or **Panel** in the header) to hide the right panel. Use **+**, **−**, the scroll wheel, and drag to zoom and move. **Fit** shows the whole graph again.
- **Request journey:** use **Play**, **Prev**, **Next**, or the left and right arrow keys. Buttons above the diagram switch between the main path and variants such as *Cache hit* or *Invalid JWT, 401*.
- **Search:** press `/` and type a component, endpoint, or file name.
- **Dark and light mode:** the sun/moon button in the header.
- **Uncertain tab:** links Claude suspected but could not prove. They are never drawn as facts. Each one says what would confirm it.

## Step 5: Go deeper on one thing

**One service:** add its name after the command.

```
/repo-visualizer:visualize billing-service
```

The overview then shows that service, what it connects to, and the gateway in front of it. Each run **overwrites** the previous `architecture.html` and `architecture.json`. Copy them elsewhere first if you want to keep an old one.

**One endpoint** has its own commands. Nothing is pre-traced: you type the endpoint, Claude analyzes it, a separate read-only run verifies it, and then it is shown.

```
/repo-visualizer:endpoint "POST /api/login"
/repo-visualizer:visualize-live
```

- `/repo-visualizer:endpoint "POST /api/login"` analyzes that one endpoint without a server. It prints a summary in the terminal (scenarios with their status codes, how many items are verified, corrected, or unverified, and what needs attention), writes `endpoint-analysis.json` and `endpoint-analysis.html` to your project root, and opens the page in your browser. It can take several minutes. If several routes match it asks you to pick one.
- `/repo-visualizer:visualize-live` starts a local viewer (on your own machine only) and opens it. Type any endpoint (`POST /api/login`, `/api/login`, `login`, or a handler name), watch the progress (Locating, Analyzing, Verifying), then browse the scenarios as tabs with the request keys, the animated journey, and the response keys. Click a badge (verified, corrected, unverified) to see the exact code that backs it, or use **Compare scenarios** to see where two paths split, such as cache hit versus miss. Asking again for the same endpoint is instant until your code changes; **Re-analyze** runs it again anyway.
- Both need Python and the `claude` program on your PATH, because the analysis runs headless Claude Code with read-only tools in your project folder.

On large projects (more than about 500 files, or three or more services), the overview is built from your infrastructure files first, then Claude traces the 5 most important flows. Use the service and endpoint forms above to see more.

## Good to know

- **It reflects what Claude could trace in your code.** Treat it as a well-evidenced starting point, not a guarantee. Dynamic behavior (reflection, runtime config) can hide connections. Check the *Uncertain* tab.
- **Privacy:** analysis happens in your Claude Code session. Secrets are never copied into the output, only variable *names*, and Claude is told not to read real `.env` files (it uses `.env.example` style files). The page contains your architecture, so share it as carefully as your source code.
- **Git:** consider adding `architecture.html`, `architecture.json`, `endpoint-analysis.html` and `endpoint-analysis.json` to your project's `.gitignore`, or commit them deliberately as documentation.

## Updating

```
claude plugin marketplace update repo-visualizer
claude plugin update repo-visualizer@repo-visualizer
```

Then start a new Claude Code session. Use the full-path form from Step 1B if `claude` is not on your PATH.

## Uninstalling

```
claude plugin uninstall repo-visualizer@repo-visualizer
claude plugin marketplace remove repo-visualizer
```

## Troubleshooting

| Problem | What to try |
|---|---|
| `/plugin isn't available in this environment` | Use the terminal commands in Step 1B. |
| `claude` is not recognized | Use the bundled `claude.exe` path from Step 1B, or install the Claude Code CLI. |
| `/repo-visualizer:visualize` is not found | Start a new session (Step 2). Confirm `claude plugin list` shows it enabled. |
| "python not found" during the run | Install Python 3.8+ and make sure `python --version` works in the same terminal. |
| Claude reports validation errors | It normally fixes these and retries by itself. If it stops, run the command again. |
| The page is blank, or the map and sequence views say they need libraries | You are offline or a network filter blocks `cdn.jsdelivr.net`. The Request journey tab still works. |
| Result looks wrong or incomplete | Check the *Uncertain* tab, then run a focused command on the part you care about. |
| An existing `architecture.html` was not overwritten | The tool refuses to replace a file it did not create. Rename or move yours. |

Found a bug or have an idea? Open an issue at https://github.com/vikrant48/repo-visualizer/issues.
