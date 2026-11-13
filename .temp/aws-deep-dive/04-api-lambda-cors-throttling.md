# 04. API Gateway, Lambda, CORS and throttling

Goal: explain how an HTTP request becomes a Lambda invocation, what protects each layer, how the **gateway function's nine checks** keep untrusted student code from hurting the account, and why the shared "origin secret" is both useful and limited.

---

## Part 1. Theory

### 1.1 API Gateway: HTTP API vs REST API

You use **HTTP APIs** (API Gateway v2) via SAM `AWS::Serverless::HttpApi`. Know the difference because it explains several design choices.

| | HTTP API (what you use) | REST API |
|---|---|---|
| Price | cheaper (about $1.00 per million requests) | about $3.50 per million (**verify** current prices) |
| Latency | lower | higher |
| Payload format to Lambda | **2.0** (simpler, lowercase headers) | 1.0 |
| Built-in features | routes, JWT authorizers, CORS config, throttling | API keys, usage plans, request validation, **WAF**, **resource policies**, caching |
| Consequence for you | **No WAF and no resource policy on the API** -> every handler must authenticate for itself, and protection comes from throttles + app checks | richer, costlier |

An HTTP API has **routes** (`ANY /b/{slug}/{proxy+}`), **integrations** (a Lambda proxy integration), and a **stage** (SAM creates the `$default` stage with auto-deploy). `DefaultRouteSettings` set **throttling** for every route.

**Payload format 2.0 event** (what your handlers receive): `version`, `routeKey`, `rawPath`, `rawQueryString`, `headers` (lower-cased), `queryStringParameters`, `pathParameters`, `body`, `isBase64Encoded`, and `requestContext.http.{method,path,sourceIp}`. Your handlers read exactly these (`event["requestContext"]["http"]["method"]`, `event["rawPath"]`).

**Hard limits to know:** integration timeout **30 s** (cannot be raised), payload up to **10 MB**.

### 1.2 Throttling: the token bucket

API Gateway throttles with a **token bucket**: the bucket holds up to **burst** tokens and refills at **rate** tokens per second; each request takes one. Empty bucket = `429 Too Many Requests` returned **by API Gateway itself, before your Lambda runs** (so it costs you nothing in Lambda).

| API | Rate (req/s) | Burst | Purpose |
|---|---|---|---|
| `DeployApi` (portal + upload) | 20 | 50 | login, tokens, publish |
| `BackendApi` | 10 | 20 | all student backends together |
| `StorageApi` | 20 | 40 | class storage |
| `AiProxyApi` | 10 | 20 | Gemini proxy |

**Why four separate APIs?** *Bulkheads.* If every student backend shared the portal's API, one student's busy loop could exhaust the portal's throttle and lock the teacher out. Separate APIs mean separate buckets: student traffic can saturate `BackendApi` and the portal stays up (template comment: "its own API so student traffic has its own throttle"). Account-level default limits (about 10,000 rps, burst 5,000 per region: **verify**) sit above all of them.

### 1.3 Lambda: the execution model you must understand

- **Execution environment lifecycle:** *Init* (cold start: runtime starts, your module imports, code outside the handler runs) -> *Invoke* (your handler runs) -> *Freeze* (the environment is kept for reuse) -> later *Shutdown*. A **warm** invocation reuses the frozen environment.
- **Global scope survives warm invocations.** Your code uses that on purpose: `portal._teacher` caches the teacher config, `gateway._cache` caches a student's settings for 10 s. Consequence: *caches must have a short TTL*, because you cannot know which environment serves the next request, and each environment has its own cache. (That is why a settings change can take up to ~10 s to reach every gateway instance: the gateway cache TTL.)
- **Memory <-> CPU:** CPU share scales with memory. `DeployFunction` has 1024 MB (scrypt hashing and zip handling are CPU-heavy: more memory = faster login), the gateway 256 MB.
- **Architecture:** `arm64` (Graviton): about 20% cheaper per GB-second than x86.
- **Timeout:** DeployFunction/Gateway/AiProxy **29 s** (just under the API's 30 s), Storage 10 s, Sweeper 300 s.
- **Synchronous invocation:** API Gateway -> Lambda is `RequestResponse`: **no automatic retries** on failure (asynchronous invocations are retried twice). The gateway -> student function call is also `RequestResponse`.
- **Concurrency:** the number of function instances running at once. There is an **account-level quota per region** (default **1,000**, but **new accounts often start at 10**: your template comments say "the account limit is 10"). All functions in the account share it. **Reserved concurrency** carves out a guaranteed slice for one function, but AWS requires **at least 100 unreserved** concurrency to remain, so on a quota of 10 you *cannot* reserve any. That is why `BACKEND_RESERVED_CONCURRENCY` is `'0'` (no reservation) and the **gateway's own in-flight limiter** (global cap 3) is what protects the rest of the account (section 2.3). Check yours: `aws lambda get-account-settings`.
- **Logging:** each function writes to a CloudWatch log group `/aws/lambda/<function-name>`. Retention defaults to **never expire** unless set: **check this** (see `08`).

### 1.4 CORS for the APIs (and why there is none)

Recap from `03`: CORS is a **browser-only** opt-in for cross-origin reads. The portal page at `https://deploy.<domain>` calls `https://deploy.<domain>/api/...`: **same origin**, so the browser never asks, and **no API in the template has a `CorsConfiguration`**. The same holds for a student page calling its own `/api` (same host).

What happens if someone *does* call a student's API from another origin (say `https://other.site` calls `https://alice.apps.example.com/api/x`)?

1. For a simple request the browser sends it, but the page cannot read the response without `Access-Control-Allow-Origin`.
2. For a non-simple request (JSON `POST` with `Content-Type: application/json` is *not* simple; `Authorization` header is not simple) the browser sends an `OPTIONS` **preflight**. CloudFront allows `OPTIONS` on `/api/*` and forwards it; the HTTP API route is `ANY`, so it reaches the gateway and then **the student's function**, which must answer the preflight itself. The gateway adds **no** CORS headers. So *cross-origin use is the student's responsibility*, and same-origin use needs nothing (the student guide says "No CORS needed").

If you ever wanted API-level CORS, an HTTP API supports `CorsConfiguration` (allow origins/methods/headers/max age, `AllowCredentials`), and API Gateway would answer preflights itself without invoking Lambda. Remember: `Access-Control-Allow-Origin: *` cannot be combined with credentials.

---

## Part 2. What we built

### 2.1 The "origin secret" trick

Problem: an HTTP API's `execute-api` URL is **public** (anyone who knows it can call it, bypassing CloudFront). And behind CloudFront the API sees CloudFront's IP, not the visitor's.

Design:

1. CloudFront adds a fixed header `x-origin-secret: <value>` to every request it sends to the API origin (`OriginCustomHeaders`, template.yaml:394 and 681).
2. The CloudFront function sets `x-viewer-ip` to the real client IP, overwriting anything the client sent.
3. The handlers **trust `x-viewer-ip` only if `x-origin-secret` matches** (`portal.client_ip`, constant-time `hmac.compare_digest`); the **gateway refuses everything without the secret** (`403 forbidden`).

The value is `!Select [2, !Split ['/', !Ref 'AWS::StackId']]`: the **UUID part of the stack id** (`arn:aws:cloudformation:us-east-1:123456789012:stack/student-hosting/**4a6f8fe0-c27a-...**`). It is also injected as the `ORIGIN_SECRET` environment variable of every function (Globals).

**Be honest about its strength.**

| Property | Reality |
|---|---|
| Who can learn it? | Anyone with `cloudformation:DescribeStacks`, `cloudfront:GetDistribution` (origin custom headers), or `lambda:GetFunctionConfiguration` (the env var) |
| Does it rotate? | No: it is fixed for the life of the stack |
| What does it protect? | (1) Direct calls to the **BackendApi** (the gateway refuses them), (2) the *forged client IP* attack on login lockout and per-IP rate limits |
| What does it **not** protect? | It is **not authentication.** DeployApi/StorageApi/AiProxyApi are directly reachable and are protected by sessions/keys; the secret only gates the trust in the IP header |
| If leaked | An attacker could call APIs directly **with a forged `x-viewer-ip`** to dodge the per-IP limits (including the 5-failure teacher-login lockout), still bounded by the API throttle (20 rps) and scrypt cost. See `09` for ratings and a better design (a random secret from Secrets Manager, rotated). |

### 2.2 The portal API (`DeployFunction`)

One Lambda (`app.handler`) serves three API routes: `POST /upload-url`, `POST /publish` (the legacy deploy-key CLI) and `ANY /api/{proxy+}` (the portal). `app.handler` first checks the path: `/api/...` -> `portal.handle` (the router). Everything is one codebase (`lambda/portal.py`) with an in-memory store for tests and a DynamoDB store in production.

### 2.3 The gateway: nine checks, in order (`lambda/gateway.py`)

Every call to `https://<student>.<domain>/api/...` goes through this. **Order matters**: cheap, safe checks first; expensive and counting checks last; a request is only **counted** if it passes everything before the counters.

| # | Check | Code | Response | Why |
|---|---|---|---|---|
| 1 | Origin secret (constant-time) | `compare_digest` | `403 forbidden` | Only CloudFront may call; no info leak |
| 2 | Path matches `/b/<slug>/...` | `SLUG_PATH` regex | `404 not_found` | Reject malformed names early |
| 3 | Body <= 1 MB | `MAX_BODY_CHARS` | `413 payload_too_large` | Protect memory and cost |
| 4 | Backend exists, state `ready`, token lifecycle allows (`revoked` 403, `offline` 503, `expired` 403) | `load_token` + `status_of` | `404 no_backend` / `403` / `503` | A dead site must not run code |
| 5 | Teacher toggle ON | `b.get("enabled")` | `503 backend_off` | The teacher's kill switch |
| 6 | **Per-visitor-IP rate limit**: 120/min per student | `counter_add("ip#<slug>#<sha1(ip)[:12]>#<minute>", limit=120)` | `429 too_many_requests` | One visitor cannot flood one student's backend |
| 7 | **In-flight limiter**: 3 global and 2 per student running at once | `acquire("inflight#global" / "inflight#<slug>")` | `429 busy` | Protects the account's Lambda concurrency (10!) |
| 8 | **Run allowance** (default 5,000 per month) | `counter_add("runs#<slug>#<period>", limit=...)` | `429 allowance_exhausted` (with `retry-after`) | A student's monthly budget of runs |
| 9 | Invoke `sd-<slug>` (`RequestResponse`), validate and shape the result | `invoke`, `shape` | `504 function_timeout`, `502 function_error` / `bad_response` / `response_too_large`, `429 busy` | Student code is untrusted |

**The slots are released in `finally`** (`counter_sub`), so a crash cannot leak a slot forever, and `acquire` also **heals a leaked counter**: if the count is above zero and untouched for 60 s (`LEAK_SECONDS`), it is reset.

**What the student function receives** (`student_event`): a standard payload-2.0 event **with `x-origin-secret` and `x-viewer-ip` removed** from the headers and `requestContext.http.sourceIp` set to the real visitor IP. So student code can see the visitor's IP but can **never learn the origin secret**.

**What the visitor receives** (`shape`): the function's return value is validated (status 100-599, string headers, body <= 1 MB), `x-content-type-options: nosniff` and `cache-control: no-store` are added when missing, and hop-by-hop headers (`transfer-encoding`, `connection`, `content-length`, `keep-alive`) are dropped. A function **error message is never returned** to the visitor (it may contain the student's secrets); it stays in the student's own function logs.

### 2.4 Atomic counters: why they never overshoot

Rate limits and allowances are implemented with one DynamoDB operation (`portal.DynamoStore.counter_add`):

```
UpdateItem  Key={pk}
  UpdateExpression:    SET updated_at=:u, ttl=:t  ADD #count :delta
  ConditionExpression: attribute_not_exists(#count) OR #count <= :max     (max = limit - delta)
```

This is a **single atomic conditional update**: DynamoDB applies the increment **only if** the condition holds, otherwise fails with `ConditionalCheckFailedException` (the code returns `None` = "limit reached"). Compare the **wrong** way, *read the count, then write count+1*: two parallel Lambdas can both read 99, both write 100, and the limit of 100 is exceeded (a **race condition**). With a conditional `ADD`, DynamoDB serialises writes to one item, so even with parallel callers the count can **never exceed the limit**. Learn this pattern: it is the cheapest correct way to build counters, locks and quotas on DynamoDB.

Fixed windows: the per-IP counter key includes `now // 60` (the current minute) with a 2-minute TTL, so each minute is a fresh counter (a **fixed-window** limiter: bursts across a minute boundary can briefly reach up to twice the limit; acceptable here).

### 2.5 The student function itself (`lambda/backends.py`)

Created by `DeployFunction` with `lambda.create_function(...)`:

| Property | Value | Notes |
|---|---|---|
| Name | `sd-<slug>` | the only name pattern DeployFunction may manage |
| Role | `SANDBOX_ROLE_ARN` (StudentSandboxRole) | logs only |
| Memory / timeout | 256 MB / **10 s** (25 s if class AI is on) | `MEMORY_MB`, `TIMEOUT_SECONDS`, `ai.timeout_for` |
| Runtime | `python3.12` or `nodejs22.x` | chosen from the zip's entry file |
| Code | `S3Bucket`/`S3Key` (the uploaded zip) | limits: 5 MB zipped, 50 MB unzipped, 500 files |
| VPC | **none** | the function has **outbound internet** (needed for OpenRouter, public APIs); inbound only via the gateway |
| Env vars | only the platform's AI/storage names (when on) and the student's own (max 20 vars, 4 KB, names must not start with `AWS_`/`LAMBDA_`/`_`, and the platform's names are **reserved**) | the origin secret is **not** among them |
| Tags | `managed-by=student-deploy`, `slug=<slug>` | helps Cost Explorer and cleanup |
| OFF switch | `put_function_concurrency(ReservedConcurrentExecutions=0)` where possible, plus the gateway check | "belt and braces" |

---

## Part 3. Console steps

### 3.1 API Gateway

1. **API Gateway** -> **APIs**: four **HTTP** APIs (they have an *API ID* like `a1b2c3d4e5`; names come from the stack: `student-hosting-BackendApi...` or similar).
2. Open one: **Routes** (see `ANY /b/{slug}/{proxy+}` and `ANY /b/{slug}`), **Integrations** (Lambda), **Stages** -> `$default` -> **Throttling**: *Rate*/*Burst* (10/20 for BackendApi). **CORS**: *not configured* (this is the point).
3. **Monitor**: Count, 4xx, 5xx, Latency, IntegrationLatency graphs. (Detailed *access logging* is **not** enabled in the template: a gap to know about, see `08`/`09`.)

### 3.2 Lambda

1. **Lambda** -> **Functions**: `...GatewayFunction...` -> **Configuration**:
   - **General configuration**: memory 256 MB, timeout 29 s, arm64.
   - **Permissions**: the execution role (click it to open IAM), and **Resource-based policy statements** (which service may invoke: API Gateway, scoped to the API).
   - **Environment variables**: the names (`ORIGIN_SECRET`, `INFLIGHT_GLOBAL`, `PER_IP_PER_MINUTE`, `TOKENS_TABLE`, ...). **The values include the origin secret: do not screenshot or share.**
   - **Triggers**: the API routes.
2. **Monitor** tab: Invocations, Duration, Errors, Throttles, ConcurrentExecutions.
3. **Lambda** -> **Dashboard** (left menu) shows **Account-level concurrency**.
4. A student function `sd-<slug>`: **Configuration** -> *Permissions* shows `StudentSandboxRole`; *Code* shows the zip the student uploaded.

---

## Part 4. CLI verification and experiments

```bash
BACKEND_API=$(aws cloudformation describe-stacks --stack-name $STACK --query "Stacks[0].Outputs[?OutputKey=='BackendApiUrl'].OutputValue" --output text)
APIID=$(echo $BACKEND_API | sed 's#https://##; s#\..*##')

# API Gateway posture  [READ]
aws apigatewayv2 get-apis --query 'Items[].[Name,ApiId,ProtocolType,CorsConfiguration]' --output table      # CorsConfiguration is None everywhere
aws apigatewayv2 get-stages --api-id $APIID --query 'Items[].[StageName,AutoDeploy,DefaultRouteSettings]'
aws apigatewayv2 get-routes --api-id $APIID --query 'Items[].RouteKey'

# Lambda posture  [READ]  (the second command prints variable NAMES only, never values)
aws lambda list-functions --query "Functions[?contains(FunctionName,'$STACK')].[FunctionName,Runtime,MemorySize,Timeout,Architectures[0]]" --output table
aws lambda get-function-configuration --function-name <GatewayFunction name> --query 'Environment.Variables | keys(@)'
aws lambda get-policy --function-name <GatewayFunction name> --query Policy --output text | python -m json.tool
aws lambda get-account-settings --query 'AccountLimit.[ConcurrentExecutions,UnreservedConcurrentExecutions]'   # likely 10 on a new account
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/$STACK --query 'logGroups[].[logGroupName,retentionInDays]' --output table
```

### 4.1 Experiments

```bash
# E1. The gateway refuses a direct call (no origin secret).                       EXPECT 403 {"error": "forbidden"}
curl -i $BACKEND_API/b/alice/health

# E2. The same with the secret: this shows the secret is the stack UUID (and why it is "weak").   [READ the secret; do not share]
SECRET=$(aws cloudformation describe-stacks --stack-name $STACK --query 'Stacks[0].StackId' --output text | awk -F/ '{print $3}')
curl -i -H "x-origin-secret: $SECRET" $BACKEND_API/b/alice/health      # EXPECT 404 {"error": "no_backend"} (or a real answer if alice has a backend)

# E3. Throttling: 60 rapid calls to BackendApi. The first ~20 reach the gateway (403/404), the rest get API Gateway's own 429.
for i in $(seq 1 60); do curl -s -o /dev/null -w "%{http_code} " $BACKEND_API/b/alice/health; done; echo

# E4. The portal API needs a session.                                              EXPECT 401 {"error": "Please sign in again."}
curl -i https://deploy.<your-domain>/api/admin/tokens

# E5. Bad body: not JSON.                                                          EXPECT 400
curl -i -X POST -H "content-type: application/json" -d 'not json' https://deploy.<your-domain>/api/student/login
```

**Reading E3:** the API Gateway 429 body is `{"message":"Too Many Requests"}` (different from the gateway Lambda's JSON errors, which contain an `error` key). That difference tells you **which layer** answered: a great debugging habit.

### 4.3 Observe the gateway in CloudWatch Logs Insights `[READ]`

**CloudWatch** -> **Logs Insights** -> select the group `/aws/lambda/<GatewayFunction>`:

```
fields @timestamp, @message
| parse @message /"slug": "(?<slug>[^"]+)", "status": (?<status>\d+), "ms": (?<ms>\d+)/
| filter ispresent(slug)
| stats count() as calls, avg(ms) as avg_ms, max(ms) as max_ms by slug, status
| sort calls desc
```

The gateway prints one JSON line per successful invocation: `{"slug": "alice", "status": 200, "ms": 53}`. This gives you per-student traffic and latency without any extra service.

---

## Part 5. What you should now be able to explain

1. Why is there no CORS configuration on any API? (One hostname: same origin.)
2. Why four APIs instead of one? (Bulkheads: each has its own throttle so a student flood cannot starve the portal.)
3. What is the origin secret protecting, what is it **not**, and what is its weakness? (Direct gateway calls and forged IPs; not authentication; derived from the stack UUID, readable by AWS readers, never rotated.)
4. Why can the run allowance never be exceeded under parallel load? (One atomic conditional `ADD`.)
5. Why is the gateway's in-flight cap 3 on a 10-concurrency account? (To leave capacity for the portal and other apps, since reserved concurrency is impossible below 100 unreserved.)
6. What does the student function never get to see? (The origin secret, the AWS secrets, any other student's data.)
7. A visitor reports `429`. Name two layers that could have produced it and how you tell them apart. (API Gateway: `{"message":"Too Many Requests"}`; gateway Lambda: JSON with `error` = `too_many_requests`, `busy` or `allowance_exhausted`.)
8. Why does a settings change (toggle OFF) take a few seconds to take effect? (The gateway caches settings for 10 s per instance: `GATEWAY_CACHE_SECONDS`.)
