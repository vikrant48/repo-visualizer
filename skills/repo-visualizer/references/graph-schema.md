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
| `type` | yes | `client` `external-caller` `gateway` `middleware` `service` `database` `cache` `queue` `external-api` `storage`. There is exactly ONE `client` node and no other frontend nodes (see Notes). |
| `category` | middleware only | `auth` `security` `cors` `rate-limit` `validation` `logging` `error-handler`. Required when `type` is `middleware`, forbidden otherwise. Use `security` for security-headers (helmet etc.). |
| `tech` | no | Lowercase technology when evidenced: `redis`, `postgres`, `jwt`, `nginx`, `kafka`. |
| `domain` | yes | The business area it belongs to, inferred from names, folders and package names (for example `recruit`, `workforce`, `admin`, `platform`, `payments`). One short lowercase word or hyphenated phrase, spelled the same everywhere. A data store or external system gets the domain that uses it most, or `shared`. Services are drawn in one collapsible box per domain when there are 2 or more. |
| `tier` | yes | `client` `caller` `gateway` `service` `datastore` `external`. Decides the overview column, left to right (the client alone on the far left). Expected: `client` -> `client`; `external-caller` -> `caller`; `gateway` -> `gateway`; `service` and `middleware` -> `service`; `database`/`cache`/`queue`/`storage` -> `datastore`; `external-api` -> `external`. A different value is allowed (for example an external service you do not own) but draws a warning. |
| `importance` | yes | Integer 1 to 3 from the flows: 3 = on at least 60% of flows, 2 = on at least 25% of flows or on 2 or more, 1 = otherwise. Count a flow when any of its steps (main path or branch) is at the node, including a step where middleware just passes the request on. Important nodes are drawn larger and bolder, and the overview can filter by importance. |
| `owner` | middleware only | Id of the `service` the middleware belongs to. Required for middleware, forbidden otherwise. A chain shared by several services (one app, many controllers) names the primary service (the entry service the client connects to), and its last middleware links to every service it protects. A response-only error handler (`@RestControllerAdvice`, an error middleware) is still a `middleware` node with that `owner`. |
| `description` | yes | One sentence: what it does in this project. |
| `evidence` | yes | Evidence[]. |

Notes:
- `tier` decides the node's column in the overview map, left to right: client (alone), caller, gateway, security layer
  (middleware, drawn per `owner`), service, datastore, external. Pick `type` by what the component *is*,
  not by who calls it (a queue consumer is a `service`; the queue itself is a datastore).
- The overview hides middleware: each service shows a shield with the number of middleware that name it as
  `owner` (plus any its chain edges lead to); clicking the service expands the chain in order. Always connect
  the chain with `middleware-next` edges: first middleware -> ... -> last -> service (the entry edge from the client or
  gateway points at the service; the overview routes it through the chain).
- **This tool maps the backend only.** Every frontend (Angular, React, Vue, mobile apps, JSP/JSF pages,
  server-side templates) is ONE node: `{ "id": "client", "name": "Client / User", "type": "client", "tier": "client" }`.
  Never create nodes for frontend apps and never read or cite frontend code. The client's evidence is backend
  code or config: a route definition, the gateway `listen`/`location` config, a CORS origin entry, a security config.
- **Where the client connects:** the backend entry point. If there is a gateway or proxy (nginx, Spring Cloud
  Gateway, Kong, ...) the client connects to the gateway; otherwise to the service that exposes the endpoint.
- **External callers:** something other than a browser or app that calls the backend (a payment provider's
  webhook, an external cron or scheduler, another company's system) gets its own `external-caller` node
  (tier `caller`) so it is not hidden. An in-process scheduler (`@Scheduled`) is not a caller.
- Config values (hosts, passwords, URLs with credentials) never appear. Only
  variable names, e.g. `"description": "Reads REDIS_URL"`.

## edges

The whole-system connection map (the overview), independent of any one flow.

| Field | Required | Notes |
|---|---|---|
| `source` | yes | Node id. |
| `target` | yes | Node id. |
| `kind` | yes | `http` `grpc` `call` `other` `db-query` `cache-read` `cache-write` `publishes` `subscribes` `middleware-next` `response` |
| `label` | yes | Short text, e.g. `POST /login`, `GET user:{email}`, `topic orders.created`. |
| `evidence` | yes | Evidence[]. |
| `endpoints` | client / external-caller edges only | `[{ "method": "POST", "path": "/login", "evidence": [...] }, ...]`: every endpoint behind this entry point. |

- **One edge per entry point.** The client (and each external caller) has ONE edge to its backend entry point (a gateway if there is one,
  otherwise the service that exposes the endpoints) with all its endpoints in `endpoints`, never one edge per endpoint. The overview
  draws one line with the endpoint count, and clicking it lists the endpoints with their evidence.
- `call`: an in-process call or event between components of one deployable (a controller calling a service in the same app).
  `other`: a protocol with no kind of its own (SMTP, WebSocket, FTP); name it in `label`.
- `middleware-next`: one hop in a middleware chain (request direction).
- `response`: only for a return path that is **not** simply the reverse of a
  request edge (e.g. a webhook callback, or an error handler that replies
  through the gateway). Normal returns are derived by the
  renderer from the `response` steps in flows; do not duplicate them as edges.
- `publishes`/`subscribes`: source is the producer/consumer, target is the queue.

## flows

One per important endpoint or journey (typically 2 to 5). Every flow starts with a request at the `client` node and ends with the response back at
the `client` node. The exception is a flow started by an `external-caller` (a webhook or a scheduled call): it starts and ends at that caller.

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
  nothing on that side (for example auth on the way back, or an audit interceptor on the way in) is accepted;
  do not add pass-through steps just to satisfy it.
- The main path covers the **full round trip**: request steps all the way to the
  deepest node, then response steps all the way back to the `client` node,
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
node where the flow started. Evidence is required on every branch step.

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
5. Each flow's first step is a request at the `client` node (or an `external-caller`) and its last step is
   the response at that same node (unless `notes` explains otherwise).
6. Strings contain no obvious secret patterns (private keys, `password=...`,
   bearer tokens, connection strings with credentials).
7. Every node has `domain`, `tier` and `importance` (1 to 3); middleware have an `owner` that is a `service`.
   There is at most one node of type `client`; its id is `client` and its name is `Client / User`. The types
   `user` and `frontend` no longer exist.
8. Edges from the client or an external caller target a `gateway` or `service`, carry `endpoints`
   (method, path, evidence each), and there is only one such edge per source and target.
   Warnings (not errors): a `tier` unusual for the `type`, an `importance` far from the real flow counts,
   inconsistent domain spelling, and a middleware chain that never reaches its owner.

## Complete example: POST /login

The file paths and line numbers below are illustrative. With a single flow every node on it is `importance` 3; with several flows the
scale spreads out (a node on one flow of five is 1).

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
      "id": "client", "name": "Client / User", "type": "client", "domain": "platform", "tier": "client", "importance": 3,
      "description": "Every browser and app that calls the API; the only node that stands for frontends.",
      "evidence": [{ "file": "services/user/src/app.ts", "line": 18, "note": "CORS allowed origin" }]
    },
    {
      "id": "gateway", "name": "nginx gateway", "type": "gateway", "tech": "nginx", "domain": "platform", "tier": "gateway", "importance": 3,
      "description": "Terminates traffic on port 80 and proxies /login to user-service.",
      "evidence": [{ "file": "infra/nginx.conf", "line": 22, "note": "location /login proxy_pass" }]
    },
    {
      "id": "cors-mw", "name": "cors()", "type": "middleware", "category": "cors", "tech": "cors",
      "owner": "user-service", "domain": "identity", "tier": "service", "importance": 3,
      "description": "Checks the Origin header against the allowed origin list and adds CORS headers.",
      "evidence": [{ "file": "services/user/src/app.ts", "line": 18 }]
    },
    {
      "id": "rate-limit-mw", "name": "loginLimiter", "type": "middleware", "category": "rate-limit", "tech": "express-rate-limit",
      "owner": "user-service", "domain": "identity", "tier": "service", "importance": 3,
      "description": "Limits login attempts per IP and adds X-RateLimit headers.",
      "evidence": [{ "file": "services/user/src/app.ts", "line": 19 }]
    },
    {
      "id": "auth-mw", "name": "authenticate", "type": "middleware", "category": "auth", "tech": "jwt",
      "owner": "user-service", "domain": "identity", "tier": "service", "importance": 3,
      "description": "Verifies a bearer JWT when present; /login is on the public allow-list, so a missing token passes.",
      "evidence": [{ "file": "services/user/src/middleware/auth.ts", "line": 9 }]
    },
    {
      "id": "user-service", "name": "user-service", "type": "service", "tech": "express", "domain": "identity", "tier": "service", "importance": 3,
      "description": "Handles POST /login: looks up the user, checks the password hash, signs a JWT.",
      "evidence": [{ "file": "services/user/src/routes/auth.routes.ts", "line": 7 }]
    },
    {
      "id": "redis", "name": "Redis", "type": "cache", "tech": "redis", "domain": "identity", "tier": "datastore", "importance": 3,
      "description": "Caches user records by email. Address comes from REDIS_URL.",
      "evidence": [{ "file": "docker-compose.yml", "line": 15 }]
    },
    {
      "id": "postgres", "name": "Postgres", "type": "database", "tech": "postgres", "domain": "identity", "tier": "datastore", "importance": 3,
      "description": "Primary store holding the users table. Address comes from DATABASE_URL.",
      "evidence": [{ "file": "docker-compose.yml", "line": 4 }]
    }
  ],
  "edges": [
    { "source": "client", "target": "gateway", "kind": "http", "label": "Public REST API (2 endpoints)",
      "endpoints": [
        { "method": "POST", "path": "/login", "evidence": [{ "file": "services/user/src/routes/auth.routes.ts", "line": 7 }] },
        { "method": "POST", "path": "/logout", "evidence": [{ "file": "services/user/src/routes/auth.routes.ts", "line": 21 }] }
      ],
      "evidence": [{ "file": "infra/nginx.conf", "line": 22, "note": "location / proxy_pass" }] },
    { "source": "gateway", "target": "user-service", "kind": "http", "label": "proxy_pass user-service",
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
        { "id": "s1", "node": "client", "direction": "request",
          "action": "The client sends credentials to the API.", "data": "email + password in JSON body",
          "evidence": [{ "file": "services/user/src/routes/auth.routes.ts", "line": 7 }] },
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
        { "id": "s17", "node": "client", "direction": "response",
          "action": "The client receives the token.", "data": "200 JSON body with the JWT",
          "evidence": [{ "file": "services/user/src/routes/auth.routes.ts", "line": 15 }] }
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
            { "id": "c5", "node": "client", "direction": "response",
              "action": "The client receives the 401 response.", "data": "401 Unauthorized",
              "evidence": [{ "file": "services/user/src/middleware/auth.ts", "line": 22 }] }
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

## External caller example: a payment webhook

Something other than a browser or app that calls the backend gets its own `external-caller` node. Add the node and
one edge to the entry point with its endpoints; the webhook's own flow starts and ends at that node.

```json
{
  "id": "razorpay-webhook", "name": "Razorpay (webhook)", "type": "external-caller", "tech": "razorpay",
  "domain": "payments", "tier": "caller", "importance": 1,
  "description": "Razorpay calls the backend when a payment is captured.",
  "evidence": [{ "file": "services/payments/src/routes/webhook.ts", "line": 9, "note": "verifies X-Razorpay-Signature" }]
}
```

```json
{ "source": "razorpay-webhook", "target": "gateway", "kind": "http", "label": "Payment webhook (1 endpoint)",
  "endpoints": [{ "method": "POST", "path": "/webhooks/razorpay", "evidence": [{ "file": "services/payments/src/routes/webhook.ts", "line": 9 }] }],
  "evidence": [{ "file": "infra/nginx.conf", "line": 40 }] }
```
