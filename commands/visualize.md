---
description: Map this project's request flow and service connections as an interactive architecture.html (optionally focused on one endpoint or service)
argument-hint: '["POST /login" | service-name]'
allowed-tools: Read Grep Glob Write(architecture.json) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/repo-visualizer/scripts/*) Bash(python ${CLAUDE_PLUGIN_ROOT}/skills/repo-visualizer/scripts/*)
---

Create the architecture visualization for the project in the current directory by
running the **repo-visualizer** skill: invoke it with the Skill tool and follow
its workflow and hard rules exactly (evidence on everything, no invented
connections, no secrets, never modify the user's code).

Requested focus: `$ARGUMENTS`

Pass the focus to the skill as it was given, and let the skill's "Focus modes"
section decide how to scope it:

- **No argument:** analyze the whole project.
- **An endpoint** (`"POST /login"`, `GET /users/:id`, or a path starting with
  `/`): focus on that one endpoint's full request and response journey.
- **Anything else** (for example `billing-service` or `gateway`): treat it as a
  service or component name and focus on it and its connections.

When the skill finishes:

1. Tell the user to open `architecture.html` in a browser (give the full path).
2. State the focus used. If it was focused, say the page is partial and that
   `/repo-visualizer:visualize` with no argument maps the whole project.
3. If an earlier `architecture.html` or `architecture.json` existed in the
   project root, say that this run replaced it.
4. If the focus matched nothing or was ambiguous, show the skill's list of
   candidates and ask the user to run the command again with an exact name.
   Do not write any file in that case.
