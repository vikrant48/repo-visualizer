# Detection patterns

How to recognize components and trace request order in the user's project.
Use the section for the framework you detected (the inventory output and
manifests tell you which). A project may mix several (for example a Node
gateway in front of Spring services), so read every matching section.

Each framework section answers the same seven questions, in the same order:
**Routes, Middleware order, Auth & security, Data & cache, Service calls,
Errors, Config.**

## Contents

1. [How to use this file](#1-how-to-use-this-file)
2. [Express](#2-express)
3. [NestJS](#3-nestjs)
4. [FastAPI](#4-fastapi)
5. [Django (and DRF)](#5-django-and-drf)
6. [Flask](#6-flask)
7. [Spring Boot](#7-spring-boot)
8. [Go (net/http, gin, echo, chi)](#8-go-nethttp-gin-echo-chi)
9. [Laravel](#9-laravel)
10. [Rails](#10-rails)
11. [Infrastructure and gateway files](#11-infrastructure-and-gateway-files)
12. [Cross-framework patterns](#12-cross-framework-patterns)
13. [Proven vs uncertain](#13-proven-vs-uncertain)
14. [Client, entry points, and callers](#14-client-entry-points-and-callers)

---

## 1. How to use this file

- Start from the manifest to confirm the framework, then find the **app
  bootstrap** (where middleware and routers are registered). Order lives there.
- Route prefixes combine from the outside in: gateway/proxy path, then global
  prefix, then router/group prefix, then the method's own path. Compute the
  full public path before naming a flow.
- Middleware order is part of the story: record it from the registration code,
  never from convention.
- Request vs response order differs by framework. The notes below say which
  end of the stack touches the response first. Most stacks are an onion:
  request goes in top to bottom, response comes out bottom to top.
- Map findings to schema `type`/`category` (see graph-schema.md): CORS is
  `middleware` + `cors`, helmet/secure headers is `middleware` + `security`,
  auth guards are `middleware` + `auth`, and so on.
- Library names below are signals, not proof. Cite the line that registers or
  calls the thing.

---

## 2. Express

**Routes**
- `app.get/post/put/delete('/path', ...handlers)`, `app.route('/x')`.
- Routers: `const r = express.Router()`, mounted with `app.use('/api', r)` and
  nested `r.use('/users', usersRouter)`. Full path = all mount prefixes + the
  route path. Also look for `app.use('/v1', ...)` and files under `routes/`.
- Entry: `app.js`, `server.js`, `index.js`, `src/main.ts`.

**Middleware order**
- Order is the **order of `app.use` / route registration calls**, top to bottom.
- A middleware calls `next()` to continue. Code **after** `next()` (or in
  `res.on('finish')`, or a wrapped `res.send/res.json`) runs on the response
  side, in reverse nesting.
- Error handlers have four args `(err, req, res, next)` and only catch errors
  from middleware/routes registered **before** them.
- Router-level `router.use(mw)` and per-route `app.get('/x', mw, handler)` run
  after global middleware.

**Auth & security**
- JWT: `jsonwebtoken` `jwt.verify`, `express-jwt` / `expressjwt`,
  `passport-jwt`. Session: `express-session` (+ `connect-redis`,
  `connect-mongo` reveal the store). OAuth: `passport` strategies,
  `openid-client`, `express-openid-connect`. API keys: custom middleware
  reading `x-api-key`.
- CORS: `cors()`. Security headers: `helmet()`. Rate limiting:
  `express-rate-limit`, `rate-limiter-flexible`, `express-slow-down` (note a
  Redis store means a cache dependency). Validation: `joi`, `zod`,
  `express-validator`, `celebrate`, `ajv`. CSRF: `csurf` (deprecated),
  `csrf-csrf`, `lusca`.

**Data & cache**
- DB: `prisma`, `typeorm`, `sequelize`, `mongoose`, `knex`, `pg`, `mysql2`,
  `drizzle-orm`. Cache: `ioredis`, `redis`, `node-cache`, `lru-cache`,
  `cache-manager`, `apicache`.
- Hit/miss: `const v = await redis.get(key); if (v) return ...;` then DB read,
  then `redis.set(key, ..., 'EX', ttl)`. The `set` after the DB read is the
  cache write step; `del` marks invalidation.

**Service calls**
- HTTP: `axios`, `node-fetch`/`fetch`, `got`, `undici`, `superagent`.
  gRPC: `@grpc/grpc-js`, `.proto` files. Queues: `bullmq`/`bull`, `amqplib`,
  `kafkajs`, `@aws-sdk/client-sqs`, `nats`.
- Proxies: `http-proxy-middleware`, `express-http-proxy` (this app is a gateway).

**Errors**
- The four-argument handler. See what it sends (`res.status(500).json(...)`).
  Also `express-async-errors`, `process.on('unhandledRejection')`, and a final
  404 handler `app.use((req,res) => res.status(404)...)`.

**Config**
- `package.json`, `.env.example`, `config/`, `docker-compose.yml`, `Dockerfile`,
  `ecosystem.config.js` (PM2), `nginx.conf`.

---

## 3. NestJS

**Routes**
- `@Controller('users')` + `@Get(':id')` / `@Post()`. Full path = global prefix
  (`app.setGlobalPrefix('api')` in `main.ts`) + versioning
  (`app.enableVersioning`, `@Version`) + controller prefix + method path.
- Modules wire things up: `*.module.ts` (`imports`, `controllers`,
  `providers`). `RouterModule.register([...])` adds more prefixes.

**Middleware order** (fixed pipeline, request direction)
1. Middleware (global `app.use`, then module `configure(consumer)`:
   `consumer.apply(Mw).forRoutes(...)`)
2. Guards (global, controller, route)
3. Interceptors, pre-handler part
4. Pipes (validation/transform)
5. **Route handler**
6. Interceptors, post-handler part (response side, reverse order)
7. Exception filters (on any thrown error)

Global providers registered via `APP_GUARD`, `APP_INTERCEPTOR`, `APP_PIPE`,
`APP_FILTER` in a module also apply; read their registration order.

**Auth & security**
- `@UseGuards(AuthGuard('jwt'))`, `JwtAuthGuard`, `@nestjs/jwt`,
  `@nestjs/passport` strategies, `RolesGuard` / `@Roles()`, session via
  `express-session`. API keys: custom guard.
- CORS: `app.enableCors()` / `NestFactory.create(App, { cors })`. Headers:
  `helmet` in `main.ts`. Rate limit: `@nestjs/throttler` (`ThrottlerGuard`,
  `@Throttle`). Validation: `ValidationPipe` + `class-validator` DTOs
  (`whitelist`, `transform`). CSRF: `csurf`/`csrf-csrf` in `main.ts`.

**Data & cache**
- `@nestjs/typeorm` (`@InjectRepository`), `@nestjs/mongoose`, Prisma service,
  `@nestjs/sequelize`. Cache: `CacheModule`, `CACHE_MANAGER` injected
  (`cache.get/set`), `@UseInterceptors(CacheInterceptor)` + `@CacheKey`/
  `@CacheTTL` (automatic hit/miss at the interceptor), `@nestjs/bull`.

**Service calls**
- `HttpService` (`@nestjs/axios`), microservices: `ClientProxy.send/emit`,
  `@MessagePattern`, `@EventPattern`, `Transport.RMQ/KAFKA/NATS/REDIS/GRPC`
  in `main.ts` (`connectMicroservice`) or `ClientsModule.register`.

**Errors**
- `HttpException` subclasses, `@Catch()` exception filters (`APP_FILTER` or
  `app.useGlobalFilters`). Default handler returns `{statusCode, message}`.

**Config**
- `main.ts`, `app.module.ts`, `ConfigModule.forRoot`, `nest-cli.json`,
  `.env.example`, `docker-compose.yml`.

---

## 4. FastAPI

**Routes**
- `@app.get('/x')`, `@router.get('/x')`. `APIRouter(prefix='/users')` and
  `app.include_router(router, prefix='/api/v1')`: **prefixes concatenate**
  (include prefix + router prefix + path). Sub-apps: `app.mount('/sub', ...)`.
- Entry: `main.py`, `app/main.py`; started by `uvicorn`/`gunicorn` (see
  Dockerfile or Procfile).

**Middleware order**
- `app.add_middleware(X)` and `@app.middleware('http')` build a stack where the
  **last one added is outermost**: it runs first on the request and last on the
  response. Order in code is the reverse of execution order, so read bottom up.
- After middleware: routing, then **dependencies** (`Depends(...)`, router- and
  app-level `dependencies=[...]`), then the handler. Code after
  `await call_next(request)` is the response side.

**Auth & security**
- `OAuth2PasswordBearer`, `HTTPBearer`, `APIKeyHeader`, `Security(...)` as
  dependencies; JWT via `python-jose` / `PyJWT`; sessions via
  `SessionMiddleware`; OAuth via `authlib`, `fastapi-users`.
- CORS: `CORSMiddleware`. Headers: custom middleware or `secure` package;
  `TrustedHostMiddleware`, `HTTPSRedirectMiddleware`. Rate limit: `slowapi`
  (`Limiter`, `@limiter.limit`), `fastapi-limiter` (Redis). Validation:
  Pydantic models / `Query`/`Body` params, automatic 422.

**Data & cache**
- SQLAlchemy `Session` from a `get_db` dependency, `SQLModel`, `Tortoise`,
  `databases`, `asyncpg`, `motor`/`beanie` (Mongo). Cache: `redis.asyncio`,
  `aioredis`, `fastapi-cache2` (`@cache(expire=60)`), `cachetools`.
- Hit/miss: `val = await redis.get(k); if val: return ...` then DB, then
  `await redis.set(k, ..., ex=ttl)`.

**Service calls**
- `httpx` (async), `requests`, `aiohttp`; gRPC `grpc`/`grpclib`; queues:
  `celery` (`.delay`/`.apply_async`), `arq`, `aio-pika`, `aiokafka`,
  `BackgroundTasks` (in-process async work).

**Errors**
- `@app.exception_handler(...)`, `HTTPException`, `RequestValidationError`
  handler. Without custom handlers: JSON `{"detail": ...}`.

**Config**
- `pyproject.toml`, `requirements.txt`, `.env.example`, `pydantic-settings`
  `BaseSettings` classes (variable names), `alembic.ini`, `Dockerfile`,
  `docker-compose.yml`.

---

## 5. Django (and DRF)

**Routes**
- `ROOT_URLCONF` in `settings.py` points to the root `urls.py`.
  `path('api/', include('app.urls'))`: **prefixes concatenate** through each
  `include`. `re_path` for regex routes. DRF: `DefaultRouter().register(
  'users', UserViewSet)` yields `/users/` and `/users/{pk}/`.
- Views: function views, class-based views, DRF `APIView`/`ViewSet`.

**Middleware order**
- The `MIDDLEWARE` list in `settings.py`. Request goes **top to bottom**,
  response comes **bottom to top**. Hooks: `process_view` (request side),
  `process_exception`, `process_template_response`.
- Cache middleware pair: `UpdateCacheMiddleware` first,
  `FetchFromCacheMiddleware` last (per-site cache).
- DRF layers inside the view: authentication, permission, throttle classes,
  then the serializer/handler.

**Auth & security**
- `AuthenticationMiddleware` + `SessionMiddleware` (sessions),
  `CsrfViewMiddleware` (CSRF), `SecurityMiddleware` (HSTS, SSL redirect,
  security headers), `XFrameOptionsMiddleware`, `django-cors-headers`
  (`CorsMiddleware`, must sit high in the list). DRF: `DEFAULT_AUTHENTICATION_
  CLASSES`, `DEFAULT_PERMISSION_CLASSES`, `DEFAULT_THROTTLE_CLASSES` in
  `REST_FRAMEWORK`; JWT via `djangorestframework-simplejwt`; OAuth via
  `django-allauth`, `django-oauth-toolkit`, `social-auth`. Validation: DRF
  serializers, Django forms, model validators.

**Data & cache**
- ORM: `Model.objects.filter/get/create`, `select_related`, raw SQL, multiple
  DBs in `DATABASES`. Cache: `CACHES` setting (backend reveals Redis/
  Memcached), `django.core.cache.cache.get/set/delete`, `@cache_page`,
  `cache.get_or_set`, template `{% cache %}`.
- Hit/miss: `cache.get(key)` is `None` then query then `cache.set(key, v, ttl)`.

**Service calls**
- `requests`/`httpx`, gRPC clients; Celery tasks `.delay()` /
  `.apply_async()` with `CELERY_BROKER_URL`; Channels (`CHANNEL_LAYERS`).

**Errors**
- `handler404`/`handler500` in root `urls.py`, `process_exception`, DRF
  `EXCEPTION_HANDLER`, `ValidationError` -> 400.

**Config**
- `settings.py` (+ `settings/` package), `manage.py`, `wsgi.py`/`asgi.py`,
  `.env.example`, `celery.py`, `docker-compose.yml`.

---

## 6. Flask

**Routes**
- `@app.route('/x', methods=[...])`, `@bp.get('/x')`. Blueprints:
  `Blueprint('users', __name__, url_prefix='/users')` registered with
  `app.register_blueprint(bp, url_prefix='/api')`. A prefix given at
  registration **overrides** the one in the Blueprint constructor; nested
  blueprints combine. Flask-RESTful/Flask-RESTX: `api.add_resource(R, '/x')`.

**Middleware order**
- Hooks: `before_request` functions run in **registration order**;
  `after_request` and `teardown_request` run in **reverse registration order**.
  App-level hooks run before blueprint-level `before_request` of the matched
  blueprint. `before_request` returning a value short-circuits the request.
- WSGI middleware: `app.wsgi_app = Middleware(app.wsgi_app)` wraps the whole
  app (outermost).

**Auth & security**
- `flask-login` (`@login_required`), `flask-jwt-extended`
  (`@jwt_required`), `flask-security`, `authlib`, `flask-httpauth`,
  `flask-session`. CORS: `flask-cors` (`CORS(app)`). Headers: `flask-talisman`.
  Rate limit: `flask-limiter` (`@limiter.limit`). Validation: `marshmallow`,
  `pydantic`, `flask-wtf`/WTForms. CSRF: `CSRFProtect(app)`.

**Data & cache**
- `Flask-SQLAlchemy` (`db.session`, `Model.query`), `SQLAlchemy`, `pymongo`,
  `psycopg2`. Cache: `flask-caching` (`@cache.cached`, `@cache.memoize`,
  `cache.get/set`), `redis`.

**Service calls**
- `requests`, `httpx`, `grpc`; `celery`, `rq`, `pika`, `kafka-python`.

**Errors**
- `@app.errorhandler(404 | Exception)`, `register_error_handler`.
  Blueprint-level handlers apply to that blueprint only.

**Config**
- `config.py`, `app.config.from_object/from_envvar/from_prefixed_env`,
  `.flaskenv`, `wsgi.py`, `gunicorn.conf.py`, `docker-compose.yml`.

---

## 7. Spring Boot

**Routes**
- `@RestController`/`@Controller` with class-level `@RequestMapping('/users')`
  and method-level `@GetMapping('/{id}')`: **class prefix + method path**.
  Also `server.servlet.context-path` and `spring.mvc.servlet.path` in
  application properties, and Spring Cloud Gateway routes (see section 11).
  WebFlux: `RouterFunction` beans.

**Middleware order** (request direction, response in reverse)
1. Servlet **Filters** (`OncePerRequestFilter`, `@Component` + `@Order`,
   `FilterRegistrationBean#setOrder`). Spring Security is a filter chain here
   (`SecurityFilterChain`), early because of its low order value.
   `http.addFilterBefore/After(f, Anchor.class)` positions `f` relative to the anchor; several filters added
   with the same anchor run in the order they were added, and a `CorsFilter` bean or `http.cors()` runs
   before the security filters. Record the order you can prove and note the rest in `meta.limitations`.
2. `DispatcherServlet`
3. `HandlerInterceptor.preHandle` (registered in `WebMvcConfigurer.
   addInterceptors`, in registration order)
4. Argument resolution and validation (`@Valid`, `@Validated`)
5. **Controller method**
6. `ResponseBodyAdvice`, interceptor `postHandle` (reverse order),
   `afterCompletion` (reverse order); filters unwind on the way out.
- Lower `@Order` number runs earlier. If no order is given, read bean
  declaration order cautiously and mark it uncertain.

**Auth & security**
- `@EnableWebSecurity` + `SecurityFilterChain` bean (`http.authorizeHttp
  Requests`, `.oauth2ResourceServer(jwt)`, `.oauth2Login`, `.httpBasic`,
  `.formLogin`, `.sessionManagement`, `.csrf`, `.cors`, `.headers`),
  `@PreAuthorize`/`@Secured`, custom `JwtAuthenticationFilter`, API key filter.
  CORS: `CorsConfigurationSource` or `@CrossOrigin`. CSRF: `http.csrf(...)`
  (look for it being disabled). Security headers: `http.headers(...)`.
  Rate limit: Bucket4j, `Resilience4j @RateLimiter`, gateway
  `RequestRateLimiter`. Validation: `jakarta.validation` annotations.
  Entry/denied: `AuthenticationEntryPoint` (401), `AccessDeniedHandler` (403).

**Data & cache**
- Spring Data (`JpaRepository`, `CrudRepository`, `@Query`), `EntityManager`,
  `JdbcTemplate`, MyBatis mappers, `MongoRepository`, R2DBC. Cache:
  `@Cacheable` (hit/miss is implicit: method body runs only on a miss),
  `@CachePut`, `@CacheEvict` (invalidation), `CacheManager`, `RedisTemplate`/
  `StringRedisTemplate`, Caffeine/Ehcache. `@EnableCaching` must be present.
  `@Cacheable` has no explicit get or set line: cite the annotation line for both the `cache-read` and
  `cache-write` edges.

**Service calls**
- `RestTemplate`, `WebClient`, `RestClient`, OpenFeign `@FeignClient`, gRPC
  stubs; queues: `KafkaTemplate` / `@KafkaListener`, `RabbitTemplate` /
  `@RabbitListener`, `JmsTemplate`, SQS `@SqsListener`, Spring Cloud Stream
  bindings. Discovery: Eureka (`@EnableDiscoveryClient`), Consul.

**Errors**
- `@RestControllerAdvice` / `@ControllerAdvice` with `@ExceptionHandler`,
  `ResponseStatusException`, `ErrorController` for `/error`,
  `ProblemDetail` responses.

**Config**
- `application.yml` / `.properties` (+ profiles), `bootstrap.yml`,
  `pom.xml` / `build.gradle`, `Dockerfile`, Helm/k8s, config server.

---

## 8. Go (net/http, gin, echo, chi)

**Routes**
- `net/http`: `http.HandleFunc`, `mux.Handle`; Go 1.22 patterns like
  `mux.HandleFunc('GET /users/{id}', h)`; prefixes via `http.StripPrefix`.
- **gin**: `r := gin.New()/Default()`, `r.GET`, `g := r.Group('/api', mw)`:
  group prefixes concatenate. **echo**: `e.GET`, `g := e.Group('/api', mw)`.
  **chi**: `r.Route('/api', func(r chi.Router){...})`, `r.Mount('/x', sub)`.
- Entry: `main.go`, `cmd/*/main.go`; handlers in `internal/`, `handlers/`.

**Middleware order**
- Plain handlers wrap: `func(next http.Handler) http.Handler`. The
  **outermost wrapper runs first**; with `alice.New(a, b, c).Then(h)` it is
  a -> b -> c -> h. Code after `next.ServeHTTP` is the response side.
- gin: `r.Use(...)` order = execution order for routes registered after;
  `gin.Default()` already includes Logger and Recovery. `c.Next()` splits
  before/after; `c.Abort()` stops the chain.
- echo: `e.Pre(...)` runs before routing, `e.Use(...)` after routing; group
  middleware follows. Code after `next(c)` is the response side.
- chi: `r.Use(...)` order = execution order, per router scope.

**Auth & security**
- `golang-jwt/jwt` (`jwt.Parse`), `gin-jwt`, `echo-jwt`, `go-chi/jwtauth`,
  `coreos/go-oidc`, `golang.org/x/oauth2`, `gorilla/sessions`, API key checks.
- CORS: `rs/cors`, `gin-contrib/cors`, echo `middleware.CORS`. Headers:
  `unrolled/secure`, `gin-contrib/secure`, echo `middleware.Secure`.
  Rate limit: `golang.org/x/time/rate`, `ulule/limiter`, `tollbooth`, echo
  `middleware.RateLimiter`. Validation: `go-playground/validator` (struct tags
  `binding:"required"`, `validate:"..."`). CSRF: `gorilla/csrf`, `justinas/nosurf`,
  echo `middleware.CSRF`.

**Data & cache**
- `database/sql`, `sqlx`, `pgx`, `gorm`, `ent`, `sqlc`-generated code,
  `mongo-driver`. Cache: `go-redis` (`rdb.Get(ctx,k).Result()` returning
  `redis.Nil` means a **miss**), `bigcache`, `ristretto`, `groupcache`.
  A `Set` after the DB load is the cache write.

**Service calls**
- `http.Client`/`resty`, gRPC (`grpc.Dial`/`NewClient`, generated
  `pb.NewXClient`, `.proto` files); queues: `sarama`, `kafka-go`,
  `amqp091-go`, `nats.go`, AWS SDK SQS/SNS.

**Errors**
- gin: `gin.Recovery()`, `c.Error`, custom error middleware reading
  `c.Errors`. echo: `e.HTTPErrorHandler`. chi/net/http: recover middleware
  and helper functions writing JSON errors. Note what status/body is returned.

**Config**
- `go.mod`, `config.yaml`, `viper`/`envconfig` struct tags (variable names),
  `Makefile`, `Dockerfile`, `docker-compose.yml`, `.env.example`.

---

## 9. Laravel

**Routes**
- `routes/web.php` (web group: sessions, CSRF) and `routes/api.php` (api group,
  automatic `/api` prefix, throttle). `Route::prefix('v1')->group(...)`,
  `Route::middleware([...])->group(...)`, `Route::resource`, `Route::apiResource`,
  `Route::controller(X::class)`. Prefixes nest from outer groups inward.
  Laravel 11+ registers routing in `bootstrap/app.php` (`withRouting`);
  older versions use `RouteServiceProvider`.

**Middleware order**
- Global middleware, then the route-group middleware (`web` / `api`), then
  route middleware. Global/group lists live in `bootstrap/app.php`
  (`withMiddleware`) in Laravel 11+, or `app/Http/Kernel.php` before that.
- Inside a middleware class: code **before** `$next($request)` is the request
  side; code **after** it is the response side (reverse order). `terminate()`
  runs after the response is sent.

**Auth & security**
- Sanctum (`auth:sanctum`), Passport (OAuth2), `tymon/jwt-auth`, Socialite
  (OAuth login), session guards in `config/auth.php`, policies/gates
  (`->can()`, `Gate::`, `authorize`). CORS: `config/cors.php` + `HandleCors`.
  Headers: custom middleware or `secure-headers` package. Rate limit:
  `throttle:60,1`, `RateLimiter::for` (in a provider or `bootstrap/app.php`).
  Validation: `FormRequest::rules()`, `$request->validate()` (422 on failure).
  CSRF: `VerifyCsrfToken` / `ValidateCsrfToken` and its `$except` list.

**Data & cache**
- Eloquent (`Model::where`, relations), `DB::table`, query builder, multiple
  connections in `config/database.php`. Cache: `Cache::get/put/forget`,
  `Cache::remember(key, ttl, fn)` (the closure runs only on a **miss**),
  `Cache::rememberForever`, `config/cache.php` driver (redis, memcached),
  `Cache::tags`.

**Service calls**
- `Http::get/post` (Guzzle), gRPC packages; queues: `dispatch(new Job)`,
  `Job::dispatch`, `config/queue.php`, Horizon, listeners/events, broadcasting.

**Errors**
- `app/Exceptions/Handler.php` (`render`, `report`) or
  `bootstrap/app.php` `withExceptions` (Laravel 11+). Validation errors ->
  422 JSON; unauthenticated -> 401 (JSON) or redirect to login.

**Config**
- `.env.example`, `config/*.php`, `composer.json`, `docker-compose.yml`,
  Sail, `supervisor` configs for queue workers.

---

## 10. Rails

**Routes**
- `config/routes.rb`: `resources`, `namespace :api do ... end`,
  `scope '/v1'`, `mount Sidekiq::Web => '/sidekiq'`, `get 'x', to:
  'c#a'`. Nested `namespace`/`scope` prefixes combine. `bin/rails routes`
  lists the result (do not run it; read the file).

**Middleware order**
- **Rack stack**: `bin/rails middleware` lists it; read
  `config/application.rb` / `config/environments/*.rb` for
  `config.middleware.use / insert_before / insert_after / delete`. Request
  goes top to bottom, response bottom to top.
- **Controller callbacks**: `before_action` and `around_action` run in
  definition order (parent controller first); `after_action` runs in
  **reverse** order. `skip_before_action` removes inherited ones.

**Auth & security**
- Devise (`authenticate_user!`), `has_secure_password`, `jwt`/`knock`,
  Doorkeeper (OAuth provider), OmniAuth (OAuth login), Pundit / CanCanCan
  (authorization). CORS: `rack-cors` in `config/initializers/cors.rb`.
  Headers: `config.action_dispatch.default_headers`, `secure_headers` gem,
  `content_security_policy.rb`, `config.force_ssl`. Rate limit: `Rack::Attack`
  (`config/initializers/rack_attack.rb`), Rails 7.2+ `rate_limit` in
  controllers. Validation: strong params (`params.require.permit`), Active
  Record validations. CSRF: `protect_from_forgery`,
  `verify_authenticity_token`.

**Data & cache**
- Active Record (`Model.find/where/create`, scopes, `includes`), multiple DBs
  in `config/database.yml`. Cache: `Rails.cache.fetch(key) { ... }` (block runs
  only on a **miss**), `Rails.cache.read/write/delete`, fragment caching
  (`cache do`), `config.cache_store` (`:redis_cache_store`, `:mem_cache_store`),
  HTTP caching (`stale?`, `fresh_when`).

**Service calls**
- `Faraday`, `HTTParty`, `Net::HTTP`, gRPC; queues: Active Job
  (`perform_later`), Sidekiq (`perform_async`), Resque, `bunny`, `karafka`,
  `config/sidekiq.yml`, `Procfile`.

**Errors**
- `rescue_from` in `ApplicationController`, `config.exceptions_app`,
  `public/404.html` / `500.html`, `ShowExceptions` / `DebugExceptions`
  middleware.

**Config**
- `config/database.yml`, `config/environments/*`, `config/initializers/*`,
  `Gemfile`, `Procfile`, `docker-compose.yml`. **Never read or decrypt**
  `config/credentials*.yml.enc` or `master.key`.

---

## 11. Infrastructure and gateway files

These reveal services, ports, upstreams, datastores, and routing. Read them
**first**. Record variable names only, never values.

| File | What to extract |
|---|---|
| `docker-compose*.yml` | Service names (they are hostnames), `image` (postgres, redis, rabbitmq, kafka, mongo, minio), `ports`, `depends_on`, `environment` variable names (`*_URL`, `*_HOST`), `networks` |
| `Dockerfile`, `Procfile` | Entry command, ports, process types (web, worker) |
| Kubernetes: `Deployment`, `Service`, `Ingress`, `ConfigMap`, `HPA` | One service per Deployment, Service ports, Ingress host/path to Service routing, env var names from `env`/`envFrom` (never Secret values) |
| Helm `values.yaml`, kustomize | Same as above, per environment |
| `nginx.conf` / `conf.d/*` | `location` blocks, `proxy_pass` / `upstream` (routing), `limit_req_zone`/`limit_req` (rate limit), `add_header` (security headers, CORS), `auth_request` (auth gate), `proxy_cache` (cache) |
| Traefik (`traefik.yml`, labels) | `routers`, `services`, `middlewares` (ratelimit, headers, basicauth, stripprefix) |
| Kong, Envoy, HAProxy | Routes, services, plugins/filters (jwt, cors, rate-limiting) |
| Spring Cloud Gateway | `spring.cloud.gateway.routes` (`id`, `uri`, `predicates`, `filters` such as `StripPrefix`, `RequestRateLimiter`) |
| API Gateway / serverless: `serverless.yml`, SAM `template.yaml`, `*.tf` | Routes -> Lambda, authorizers, throttling, stages, queues, tables, buckets |
| `.env.example`, `.env.sample` | Names of config variables only: `DATABASE_URL`, `REDIS_URL`, `KAFKA_BROKERS`, `*_API_KEY`, `*_SERVICE_URL` |
| CI/CD (`.github/workflows`, `.gitlab-ci.yml`) | Service build matrix and deploy targets; low priority |

How to turn these into nodes and edges:
- Each deployable service becomes a `service` node; each datastore image a
  `database`/`cache`/`queue` node (`tech` from the image name).
- `depends_on` or a shared network is **not** proof of a call. A call needs a
  URL variable plus client code, or an explicit upstream/route.
- A gateway `location`/route to an upstream is proof of a `http` edge from the
  gateway to that service (cite the config line).

---

## 12. Cross-framework patterns

**Cache hit/miss (any language)**
1. Compute a key. 2. `get(key)`; non-null/non-`nil` is a **hit** and returns
early. 3. Miss: load from the source of truth (DB or service). 4. `set(key,
value, ttl)` is the **cache write** step. 5. Writes/updates call `del`/`evict`
or `set` (invalidation). Model hit as a branch (`rejoin` after the lookup),
and the write-back as its own response-direction step on the cache node.

**Queue producer/consumer**
- Producer: `publish`, `send`, `emit`, `dispatch`, `delay`, `perform_async`,
  `KafkaTemplate.send`. Consumer: `@KafkaListener`, `@RabbitListener`,
  `@EventPattern`, `consume`, workers/`Job` classes, `subscribe`.
- Topic/queue/exchange names are the join key between producer and consumer;
  both sides need evidence before you draw `publishes` and `subscribes` edges.

**Gateway-like apps**
- Reverse proxy libraries (`http-proxy-middleware`, Spring Cloud Gateway,
  Zuul, `httputil.ReverseProxy`, nginx) mean this component is a `gateway`
  node, and its route table gives the edges.

**Where auth really is**
- Check both the gateway (e.g. nginx `auth_request`, Kong jwt plugin) and the
  service. The same check can exist in both; show each where evidenced.
- Public allow-lists (paths skipped by auth) matter for the flow you trace
  (for example `/login`, `/health`).

**Frontends**
- SPA/mobile code in the repo (`src/api/*`, `axios.create({ baseURL })`,
  `fetch('/api/...')`, `proxy` in `vite.config`/`package.json`) shows which
  endpoints the `frontend`/`user` node calls and the base URL variable name.

---

## 13. Proven vs uncertain

| Evidence you have | Treat as |
|---|---|
| Line that registers middleware, mounts a router, or defines a route | **Proven** |
| Client call with a target you can resolve (URL var + gateway/compose name, or `FeignClient(name)` + discovered service) | **Proven** |
| Config route/upstream (nginx, gateway routes, Ingress) | **Proven** |
| URL variable read by client code, host not given in the repo (e.g. `USER_SERVICE_URL`) | **Proven** only if the variable name and the called route both match exactly one service (cite both lines); otherwise `uncertain`. Never both an edge and an `uncertain` entry |
| Dependency in manifest only, no usage found | `uncertain` |
| Env var name exists (`PAYMENTS_URL`) but no code reads it | `uncertain` |
| `depends_on`, shared network, same namespace | `uncertain` as a call; fine as a deployment fact |
| Middleware order that depends on unordered beans/auto-config | order `uncertain`; say so in `meta.limitations` |
| Dynamic dispatch (reflection, service-locator, string-built URLs) | `uncertain`, with `would_confirm` |
| Code in comments, disabled blocks, tests, or docs only | Not evidence |

If a pattern here is absent in the project, omit that component. Never add a
component because a typical architecture would have one.

---

## 14. Client, entry points, and callers

This tool maps the **backend only**.

**Frontends are not analyzed.** Never read Angular, React, Vue, Svelte, Next/Nuxt, mobile (React Native, Flutter,
Android, iOS), JSP/JSF, or server-side template code (Thymeleaf, Razor, Blade, ERB, ...). `inventory.py` lists those
folders under "Ignored frontends". Every browser, app, and page is ONE node:
`{ "id": "client", "name": "Client / User", "type": "client", "tier": "client" }`. A compose/k8s service that only
serves static frontend files (an nginx or CDN container) is part of the client, not a node. The client's evidence is
backend code: the gateway `listen`/`location` config, route definitions, CORS allowed origins, the security config.

**Where the client connects (the backend entry point):**
- A gateway or proxy exists (nginx, Spring Cloud Gateway, Kong, Traefik, Envoy, API Gateway, Ingress):
  `client -> gateway`.
- No gateway: `client -> the service that exposes the endpoint` (the controller, router, or handler component).
  If several services expose endpoints, there is one edge to each of them.
- A monolith with many controllers: connect the client to the module `service` nodes that own the controllers, with each
  controller's endpoints on its module's edge. If the modules are not separate in code, use ONE `service` node for the
  controller layer (for example "REST controllers") as the entry service. Never one edge per endpoint, never one per class.

**One edge per entry point.** List every public endpoint behind it in `endpoints` (method, full path including
router and gateway prefixes, evidence); never one edge per endpoint. Enumerate endpoints from the route
definitions in the framework sections above (controller annotations, routers, `urls.py`, `routes/*.php`,
`routes.rb`, gateway `location` blocks and routes). Endpoints that only answer callers (below) go on the
caller's edge instead.

**External callers** are anything other than a browser or app that calls the backend. Give each its own
`external-caller` node and one edge with its endpoints, so it is not hidden:
- Webhooks from providers (Stripe, Razorpay, GitHub, Twilio): paths like `/webhook`, `/webhooks/*`, `/callback`,
  `/ipn`, and handlers that verify a signature header (`Stripe-Signature`, `X-Razorpay-Signature`, `X-Hub-Signature`).
- Scheduled triggers outside the app: a Kubernetes `CronJob`, Cloud Scheduler, a CI schedule, or crontab/Jenkins
  calling an endpoint (look for `curl` or an HTTP step in the manifest).
- Other systems: partner or internal systems using API keys or mTLS against `/internal/**` or `/partner/**` routes.
- Not callers: in-process schedulers (`@Scheduled`, node-cron, Celery beat inside the app) and queue consumers
  (the consumer is a `service` and the queue is a datastore).
- A provider that is both called and calls back (Razorpay API out, Razorpay webhook in) is two nodes: an
  `external-api` for the outbound calls and an `external-caller` for the webhook.
