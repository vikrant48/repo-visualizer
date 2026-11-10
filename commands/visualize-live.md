---
description: Start the local interactive viewer: type any endpoint, get every scenario with request and response keys and a verified journey
allowed-tools: Read Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/repo-visualizer/scripts/*) Bash(python ${CLAUDE_PLUGIN_ROOT}/skills/repo-visualizer/scripts/*)
---

Start the repo-visualizer **live viewer** for the project in the current directory. It is a
local web page on `127.0.0.1`: the user types an endpoint, and the server locates it,
analyzes it, verifies it with a separate read-only run, and shows the result with
verification badges. Nothing is pre-traced and nothing is written into the project.

Do not analyze anything yourself. Follow these steps:

1. Run the server **in the background** (it keeps running until it is stopped; use the Bash
   tool with `run_in_background: true`). Use `python3`, or `python` if `python3` is not found:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/repo-visualizer/scripts/serve.py --root . --open
   ```

   `--open` opens the page in the user's default browser. Do not add `--port`; a random free
   port is used on purpose.

2. Read the first line of the server's output. It is a JSON object with `url`, `port`, and
   `cacheDir`. If there is no such line within a few seconds, or the output shows an error
   (Python missing, port or permission problem), say what went wrong and stop.
   The stderr line `warning: the claude program was not found ...` means analysis will fail
   until `claude` is on PATH (or `CLAUDE_BIN` is set); pass that on.

3. Tell the user, briefly:
   - The viewer is at `url`. It was opened in their browser; if not, open the link. The link
     contains a random session token after `#`. It is a password for this session: do not
     share it or paste it anywhere else. The token is not sent to the server in the URL.
   - Type an endpoint such as `POST /api/login`, `/api/login`, `login`, or a handler name.
     If several routes match, they pick one. Progress shows Locating, Analyzing, Verifying.
     One analysis runs at a time (others wait), each can take several minutes, and results
     are cached by endpoint and code (git commit plus uncommitted changes), so asking again
     is instant until the code changes. "Re-analyze" runs it again anyway.
   - Analysis uses headless Claude Code with read-only tools (Read, Grep, Glob) in this
     project folder, and each run times out after 5 minutes.
   - To stop the server, ask you to stop it (or press Ctrl+C where it runs).

4. Do not print the full URL again later unless the user asks, and never put it in a file.
