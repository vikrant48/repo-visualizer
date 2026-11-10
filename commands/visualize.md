---
description: Map this project's backend architecture (gateway, middleware, services, data stores, request journeys) as an interactive architecture.html
argument-hint: '[service-name]'
allowed-tools: Read Grep Glob Write(architecture.json) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/repo-visualizer/scripts/*) Bash(python ${CLAUDE_PLUGIN_ROOT}/skills/repo-visualizer/scripts/*)
---

Create the architecture overview for the project in the current directory by running the
**repo-visualizer** skill in **map mode**: invoke it with the Skill tool and follow its
workflow and hard rules exactly (backend only, evidence on everything, no invented
connections, no secrets, never modify the user's code).

Requested focus: `$ARGUMENTS`

Strip surrounding quotes and spaces from the focus, then:

- **No argument:** analyze the whole project.
- **Looks like an endpoint** (starts with an HTTP method such as `POST /login`, or starts
  with `/`): this command does not analyze single endpoints. Do not write any file. Tell the
  user to run `/repo-visualizer:endpoint "<what they typed>"` for a verified analysis of one
  endpoint, or `/repo-visualizer:visualize-live` to explore endpoints in the browser, and stop.
- **Anything else** (for example `billing-service` or `gateway`): treat it as a service or
  component name and let the skill's "Focus modes" section scope the overview to it and its
  connections.

When the skill finishes:

1. Tell the user to open `architecture.html` in a browser (give the full path).
2. State the focus used. If it was focused, say the page is partial and that
   `/repo-visualizer:visualize` with no argument maps the whole project.
3. If an earlier `architecture.html` or `architecture.json` existed in the project root, say
   that this run replaced it.
4. If the focus matched nothing or was ambiguous, show the skill's list of candidates and ask
   the user to run the command again with an exact name. Do not write any file in that case.
5. Mention the other two commands in one line: `/repo-visualizer:endpoint "POST /api/login"`
   analyzes one endpoint (every scenario, request and response keys, verified), and
   `/repo-visualizer:visualize-live` opens the interactive viewer.
