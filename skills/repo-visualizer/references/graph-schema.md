# architecture.json schema

`architecture.json` is the only data contract between the analysis (Claude) and
`scripts/render.py`. The renderer validates it and fails on violations, so
follow this file exactly. All paths are relative to the user's project root,
use forward slashes, and all line numbers are 1-based.

Top level (all keys required except `meta`):

```json
{
  "project": {},
  "nodes": [],
  "edges": [],
  "flows": [],
  "uncertain": [],
  "meta": {}
}
```

## Evidence (used everywhere)

```json
{ "file": "services/user/src/app.ts", "line": 18 }
```

- `file`: a file that exists in the project and that you actually read.
- `line`: the line where the thing is defined, registered, configured, or called.
- Optional `note`: short text on what that line proves (never a secret value).
- Every node, edge, and flow step needs `evidence` with **at least one** entry.
  An empty or missing list is a validation error.

## project

| Field | Type | Notes |
|---|---|---|
| `name` | string | Project or repo name. |
| `summary` | string | 1-3 sentences on what the system does. |
| `languages` | string[] | e.g. `["TypeScript", "Python"]`. |
| `frameworks` | string[] | e.g. `["Express", "FastAPI", "Prisma"]`. |

## nodes

One entry per component. Ids are unique, lowercase kebab-case, stable.

| Field | Required | Notes |
|---|---|---|
| `id` | yes | e.g. `rate-limit-mw`, `user-service`, `postgres`. |
| `name` | yes | Display label, using the project's own naming. |
| `type` | yes | `user` `frontend` `gateway` `middleware` `service` `database` `cache` `queue` `external-api` `storage` |
| `category` | middleware only | `auth` `security` `cors` `rate-limit` `validation` `logging` `error-handler`. Required when `type` is `middleware`, forbidden otherwise. Use `security` for security-headers (helmet etc.). |
| `tech` | no | Lowercase technology when evidenced: `redis`, `postgres`, `jwt`, `nginx`, `kafka`. |
| `group` | no | Middleware only: the owning service (e.g. `user-service`). Middleware with the same `group` is drawn together as one security layer. Set it when the project has more than one service; omit it otherwise. |
| `description` | yes | One sentence: what it does in this project. |
| `evidence` | yes | Evidence[]. |

Notes:
- `user` is the end user or external caller. Its evidence points at code or
  config showing something calls in: a client call, or for backend-only projects
  the gateway `listen`/`location` config, the route definition, or a CORS origin entry.
- `frontend` is a UI app that is part of the repo (React, Vue, mobile app).
- Config values (hosts, passwords, URLs with credentials) never appear. Only
  variable names, e.g. `"description": "Reads REDIS_URL"`.

## edges

The whole-system connection map (the overview), independent of any one flow.

| Field | Required | Notes |
|---|---|---|
| `source` | yes | Node id. |
| `target` | yes | Node id. |
| `kind` | yes | `http` `grpc` `db-query` `cache-read` `cache-write` `publishes` `subscribes` `middleware-next` `response` |
| `label` | yes | Short text, e.g. `POST /login`, `GET user:{email}`, `topic orders.created`. |
| `evidence` | yes | Evidence[]. |

- `middleware-next`: one hop in a middleware chain (request direction).
- `response`: only for a return path that is **not** simply the reverse of a
  request edge (e.g. a webhook callback, or an error handler that replies
  through the gateway). Normal returns are derived by the
  renderer from the `response` steps in flows; do not duplicate them as edges.
- `publishes`/`subscribes`: source is the producer/consumer, target is the queue.

## flows

One per important endpoint or journey (typically 2 to 5).

| Field | Required | Notes |
|---|---|---|
| `id` | yes | kebab-case, e.g. `post-login`. |
| `name` | yes | e.g. `POST /login`. |
| `summary` | yes | One sentence. |
| `steps` | yes | Ordered main path (the success path). |
| `branches` | no | Alternative paths (see below). |
| `notes` | no | Caveats, e.g. "fire-and-forget: no response path". |

### step

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Unique within the flow: `s1`, `s2`, ... |
| `node` | yes | Node id where this step happens. |
| `direction` | yes | `request` (travelling toward the data) or `response` (travelling back to the user). |
| `action` | yes | What happens here, in **one** sentence. |
| `data` | no | What passes or is produced, e.g. `JWT token checked`, `user row returned`, `cached for 60s`. Never real values. |
| `evidence` | yes | Evidence[]. |

Rules:
- Steps are in the order they execute. Consecutive steps imply the hop from
  one node to the next; if that hop has no edge in `edges`, add one (the
  renderer warns). Model real calls: after a cache miss the **service** queries
  the database, so insert a step back at the service instead of going from
  the cache straight to the database. A hop that skips middleware which does
  nothing on that side (for example auth on the way back) is accepted.
- The main path covers the **full round trip**: request steps all the way to the
  deepest node, then response steps all the way back to the `user` node,
  including response middleware, error handlers, cache writes, and added
  headers. Cache writes are their own step (node = the cache).
- A middleware that does nothing on the response side simply has no response step.

### branch

An alternative path that leaves the main path, e.g. cache hit vs cache miss,
or an early 401.

| Field | Required | Notes |
|---|---|---|
| `id` | yes | e.g. `cache-hit`, `auth-fail-401`. Unique within the flow. |
| `label` | yes | Short display name. |
| `condition` | yes | When this path is taken, e.g. `Redis returns a value`. |
| `after` | yes | Step id on the main path where it diverges. |
| `steps` | yes | Step[] (ids unique across the whole flow, e.g. `b1`, `b2`). |
| `rejoin` | yes | Main-path step id it rejoins, or `null` if it ends the journey (e.g. early error response back to the user). |
| `outcome` | no | `{ "status": 401, "description": "..." }` for terminal branches. |

A branch with `rejoin: null` must end with response steps that reach the
`user` node. Evidence is required on every branch step.

## uncertain

Suspected connections you could not prove. They render as dashed/flagged items
and are never mixed into `edges`.

| Field | Required | Notes |
|---|---|---|
| `source` | yes | Node id (or a free-text name if the node itself is unproven). |
| `target` | yes | Same. |
| `suspected_kind` | yes | One of the edge `kind` values. |
| `reason` | yes | Why you suspect it (e.g. "env var PAYMENTS_URL exists but no client code found"). |
| `would_confirm` | yes | What evidence would settle it. |
| `hints` | no | Evidence[] of the partial clues. |

## meta (optional)

```json
{
  "generated_at": "2026-11-09",
  "skipped": ["services/legacy-report: out of focus"],
  "limitations": ["Kubernetes manifests not found; routing taken from nginx.conf only"]
}
```

## Validation rules the renderer enforces

1. Required fields present, enums valid, ids unique per collection.
2. Every `source`, `target`, step `node`, `after`, and `rejoin` resolves.
3. Every node, edge, step, and branch step has non-empty evidence.
4. `category` present on middleware nodes and absent on others.
5. Each flow has a `user` or `frontend` node in its first request step and
   returns to it in its last response step (unless `notes` explains otherwise).
6. Strings contain no obvious secret patterns (private keys, `password=...`,
   bearer tokens, connection strings with credentials).

## Complete example: POST /login

The file paths and line numbers below are illustrative.

```json
{
  "project": {
    "name": "shop-api",
    "summary": "REST backend for an online shop. Nginx fronts an Express user service backed by Postgres with Redis caching.",
    "languages": ["TypeScript"],
    "frameworks": ["Express", "Prisma"]
  },
  "nodes": [
    {
      "id": "user", "name": "Web client", "type": "user", "tech": "browser",
      "description": "Browser calling the API with credentials from the login form.",
      "evidence": [{ "file": "web/src/api/auth.ts", "line": 14, "note": "POST /login call" }]
    },
    {
      "id": "gateway", "name": "nginx gateway", "type": "gateway", "tech": "nginx",
      "description": "Terminates traffic on port 80 and proxies /login to user-service.",
      "evidence": [{ "file": "infra/nginx.conf", "line": 22, "note": "location /login proxy_pass" }]
    },
    {
      "id": "cors-mw", "name": "cors()", "type": "middleware", "category": "cors", "tech": "cors",
      "description": "Checks the Origin header against the allowed origin list and adds CORS headers.",
      "evidence": [{ "file": "services/user/src/app.ts", "line": 18 }]
    },
    {
      "id": "rate-limit-mw", "name": "loginLimiter", "type": "middleware", "category": "rate-limit", "tech": "express-rate-limit",
      "description": "Limits login attempts per IP and adds X-RateLimit headers.",
      "evidence": [{ "file": "services/user/src/app.ts", "line": 19 }]
    },
    {
      "id": "auth-mw", "name": "authenticate", "type": "middleware", "category": "auth", "tech": "jwt",
      "description": "Verifies a bearer JWT when present; /login is on the public allow-list, so a missing token passes.",
      "evidence": [{ "file": "services/user/src/middleware/auth.ts", "line": 9 }]
    },
    {
      "id": "user-service", "name": "user-service", "type": "service", "tech": "express",
      "description": "Handles POST /login: looks up the user, checks the password hash, signs a JWT.",
      "evidence": [{ "file": "services/user/src/routes/auth.routes.ts", "line": 7 }]
    },
    {
      "id": "redis", "name": "Redis", "type": "cache", "tech": "redis",
      "description": "Caches user records by email. Address comes from REDIS_URL.",
      "evidence": [{ "file": "docker-compose.yml", "line": 15 }]
    },
    {
      "id": "postgres", "name": "Postgres", "type": "database", "tech": "postgres",
      "description": "Primary store holding the users table. Address comes from DATABASE_URL.",
      "evidence": [{ "file": "docker-compose.yml", "line": 4 }]
    }
  ],
  "edges": [
    { "source": "user", "target": "gateway", "kind": "http", "label": "POST /login",
      "evidence": [{ "file": "web/src/api/auth.ts", "line": 14 }] },
    { "source": "gateway", "target": "cors-mw", "kind": "http", "label": "proxy_pass user-service",
      "evidence": [{ "file": "infra/nginx.conf", "line": 23 }] },
    { "source": "cors-mw", "target": "rate-limit-mw", "kind": "middleware-next", "label": "next()",
      "evidence": [{ "file": "services/user/src/app.ts", "line": 19 }] },
    { "source": "rate-limit-mw", "target": "auth-mw", "kind": "middleware-next", "label": "next()",
      "evidence": [{ "file": "services/user/src/app.ts", "line": 20 }] },
    { "source": "auth-mw", "target": "user-service", "kind": "middleware-next", "label": "next() to login handler",
      "evidence": [{ "file": "services/user/src/app.ts", "line": 24 }] },
    { "source": "user-service", "target": "redis", "kind": "cache-read", "label": "GET user:{email}",
      "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 31 }] },
    { "source": "user-service", "target": "postgres", "kind": "db-query", "label": "SELECT user by email",
      "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 38 }] },
    { "source": "user-service", "target": "redis", "kind": "cache-write", "label": "SET user:{email} EX 60",
      "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 44 }] }
  ],
  "flows": [
    {
      "id": "post-login",
      "name": "POST /login",
      "summary": "User logs in; the user record comes from Redis if cached, otherwise from Postgres and is then cached.",
      "steps": [
        { "id": "s1", "node": "user", "direction": "request",
          "action": "Browser sends credentials to the API.", "data": "email + password in JSON body",
          "evidence": [{ "file": "web/src/api/auth.ts", "line": 14 }] },
        { "id": "s2", "node": "gateway", "direction": "request",
          "action": "nginx matches /login and proxies to user-service.", "data": "HTTP request forwarded",
          "evidence": [{ "file": "infra/nginx.conf", "line": 22 }] },
        { "id": "s3", "node": "cors-mw", "direction": "request",
          "action": "Origin is checked against the allow-list.", "data": "Origin header validated",
          "evidence": [{ "file": "services/user/src/app.ts", "line": 18 }] },
        { "id": "s4", "node": "rate-limit-mw", "direction": "request",
          "action": "Request is counted against the per-IP login limit.", "data": "attempt counter incremented",
          "evidence": [{ "file": "services/user/src/app.ts", "line": 19 }] },
        { "id": "s5", "node": "auth-mw", "direction": "request",
          "action": "No bearer token is present and /login is public, so the request passes.", "data": "public route, no JWT required",
          "evidence": [{ "file": "services/user/src/middleware/auth.ts", "line": 14 }] },
        { "id": "s6", "node": "user-service", "direction": "request",
          "action": "Login handler asks the repository for the user by email.", "data": "email",
          "evidence": [{ "file": "services/user/src/routes/auth.routes.ts", "line": 7 }] },
        { "id": "s7", "node": "redis", "direction": "request",
          "action": "Cache lookup for the user record (miss on the main path).", "data": "key user:{email}",
          "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 31 }] },
        { "id": "s8", "node": "user-service", "direction": "request",
          "action": "Cache miss, so the repository falls through to the database.", "data": "no cached value",
          "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 35 }] },
        { "id": "s9", "node": "postgres", "direction": "request",
          "action": "Query the users table by email.", "data": "SELECT ... WHERE email = $1",
          "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 38 }] },
        { "id": "s10", "node": "postgres", "direction": "response",
          "action": "Database returns the matching user row.", "data": "user row returned",
          "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 40 }] },
        { "id": "s11", "node": "user-service", "direction": "response",
          "action": "Repository receives the row from the database.", "data": "user row",
          "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 41 }] },
        { "id": "s12", "node": "redis", "direction": "response",
          "action": "Repository writes the user row back to the cache.", "data": "cached for 60s",
          "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 44 }] },
        { "id": "s13", "node": "user-service", "direction": "response",
          "action": "Password hash is verified and a JWT is signed.", "data": "JWT issued, 200 JSON body",
          "evidence": [{ "file": "services/user/src/routes/auth.routes.ts", "line": 15 }] },
        { "id": "s14", "node": "rate-limit-mw", "direction": "response",
          "action": "Rate-limit headers are attached to the outgoing response.", "data": "X-RateLimit-* headers",
          "evidence": [{ "file": "services/user/src/app.ts", "line": 19 }] },
        { "id": "s15", "node": "cors-mw", "direction": "response",
          "action": "CORS headers are attached to the outgoing response.", "data": "Access-Control-Allow-Origin",
          "evidence": [{ "file": "services/user/src/app.ts", "line": 18 }] },
        { "id": "s16", "node": "gateway", "direction": "response",
          "action": "nginx relays the response to the client.", "data": "200 + JWT",
          "evidence": [{ "file": "infra/nginx.conf", "line": 22 }] },
        { "id": "s17", "node": "user", "direction": "response",
          "action": "Browser receives the token and stores it.", "data": "JWT stored client-side",
          "evidence": [{ "file": "web/src/api/auth.ts", "line": 21 }] }
      ],
      "branches": [
        {
          "id": "cache-hit",
          "label": "Cache hit",
          "condition": "Redis already holds user:{email}",
          "after": "s7",
          "rejoin": "s13",
          "steps": [
            { "id": "b1", "node": "redis", "direction": "response",
              "action": "Redis returns the cached user record; Postgres is skipped.", "data": "cached user row",
              "evidence": [{ "file": "services/user/src/repo/user.repo.ts", "line": 33 }] }
          ]
        },
        {
          "id": "auth-fail-401",
          "label": "Invalid JWT, 401",
          "condition": "A bearer token is sent but fails verification",
          "after": "s4",
          "rejoin": null,
          "outcome": { "status": 401, "description": "Request rejected before reaching user-service." },
          "steps": [
            { "id": "c1", "node": "auth-mw", "direction": "request",
              "action": "JWT verification throws and the middleware ends the request with 401.", "data": "401 Unauthorized",
              "evidence": [{ "file": "services/user/src/middleware/auth.ts", "line": 22 }] },
            { "id": "c2", "node": "rate-limit-mw", "direction": "response",
              "action": "Rate-limit headers are still attached.", "data": "X-RateLimit-* headers",
              "evidence": [{ "file": "services/user/src/app.ts", "line": 19 }] },
            { "id": "c3", "node": "cors-mw", "direction": "response",
              "action": "CORS headers are still attached.", "data": "Access-Control-Allow-Origin",
              "evidence": [{ "file": "services/user/src/app.ts", "line": 18 }] },
            { "id": "c4", "node": "gateway", "direction": "response",
              "action": "nginx relays the 401 to the client.", "data": "401",
              "evidence": [{ "file": "infra/nginx.conf", "line": 22 }] },
            { "id": "c5", "node": "user", "direction": "response",
              "action": "Browser receives 401 and redirects to the login form.", "data": "401 handled client-side",
              "evidence": [{ "file": "web/src/api/client.ts", "line": 30 }] }
          ]
        }
      ]
    }
  ],
  "uncertain": [
    {
      "source": "user-service",
      "target": "payments-api",
      "suspected_kind": "http",
      "reason": "Env var PAYMENTS_URL is declared in .env.example but no client code that uses it was found.",
      "would_confirm": "An HTTP client or SDK call that reads PAYMENTS_URL.",
      "hints": [{ "file": ".env.example", "line": 6, "note": "variable name only" }]
    }
  ],
  "meta": {
    "generated_at": "2026-11-09",
    "skipped": [],
    "limitations": ["Kubernetes manifests not present; routing taken from infra/nginx.conf only"]
  }
}
```
