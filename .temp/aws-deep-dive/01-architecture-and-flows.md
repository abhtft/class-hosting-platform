# 01. Architecture and every request path

Goal: hold the whole system in your head, then be able to trace any single request from the browser to the data and back, naming the AWS service, the identity, the limits and the failure modes at every hop.

---

## 1. The system in one picture

```
                         INTERNET (untrusted: students, visitors, attackers)
                                          |
        +---------------------------------+----------------------------------+
        |                                 |                                  |
 *.apps.example.com               deploy.apps.example.com           student.example.org (custom domain)
        |                                 |                                  |
   CloudFront                        CloudFront                      CloudFront (multi-tenant,
   "Distribution"                    "PortalDistribution"            connection group)
   RouterFunction (viewer-request)   PortalFunction (viewer-request)  spa-fallback + tenant-api functions
        |            \                    |           \                        |             \
        | /*          \ /api/*            | /*         \ /api/*               | /*           \ /api/*
        v              v                  v             v                      v               v
   S3 sites/*     BackendApi          S3 portal/*     DeployApi           S3 sites/<student>   BackendApi
   (via OAC)      (HTTP API)          (via OAC)       (HTTP API)          (via OAC)            (origin path /b/<student>)
                      |                                   |
                GatewayFunction                     DeployFunction  <-- the control plane: login, tokens,
                  (9 checks)                          |  |  |            upload/publish, backends, AI, domains
                      |                               |  |  +--> CloudFront (invalidate, tenants), ACM
                      v                               |  +-----> Lambda (create/update/delete sd-* only)
              Lambda  sd-<slug>                       +--------> S3, DynamoDB, SSM, Secrets Manager (AI key)
              (runs as StudentSandboxRole:
               logs only, in a no-VPC sandbox)
                      |
          +-----------+---------------------+
          v                                 v
   StorageApi -> StorageFunction      AiProxyApi -> AiProxyFunction   (or straight to OpenRouter with the
   (DynamoDB StudentDataTable,        (Google Gemini; key in                student's own capped key)
    S3 userdata/ via presigned POST)   Secrets Manager)

   State:  TokensTable (DynamoDB)  StudentDataTable  SSM (teacher login)  Secrets Manager (AI keys)
   Ops:    CloudWatch alarms -> SNS -> your email      SweeperFunction (daily 03:00 UTC)
```

**Four planes** (a useful way to organise your thinking):

| Plane | What it does | Components |
|---|---|---|
| **Serving (data) plane** | Delivers student sites and answers `/api` calls | CloudFront, S3, BackendApi, GatewayFunction, `sd-*` functions |
| **Control plane** | Manages who may do what | PortalDistribution, DeployApi, DeployFunction, SweeperFunction |
| **State plane** | Remembers everything | TokensTable, StudentDataTable, SSM, Secrets Manager, S3 prefixes |
| **Operations plane** | Tells you when it breaks | CloudWatch alarms, SNS, logs, the installer |

## 2. The two roles and the one trust rule

- **Teacher**: authenticates with username + password; gets a signed **session** (8 h). Can do everything in the portal.
- **Student**: authenticates with a **token** (`mds_...`, 128-bit random); gets a signed session (12 h). The session carries the token's id; **the slug (site folder) is always derived from the session, never from the request**. That is the key trust rule: a student cannot name a different student's folder because the API never asks which folder (`lambda/app.py` `portal_deploy`, line "the session decides the site folder").
- **Everyone else**: the internet. Visitors of student sites are anonymous; nothing on the serving plane trusts them with anything.

## 3. Data model: DynamoDB single-table design

`TokensTable` has one key, `pk` (a string), and stores many kinds of items distinguished by **prefix**. This is called *single-table design*: you design the keys around your access patterns instead of normalising into many tables.

| `pk` prefix | What it is | Written by | TTL? |
|---|---|---|---|
| `tok#<sha256(token)>` | A student token: slug, name, expiry, revoked/archived flags, settings for backend/AI/storage/domain | portal | no |
| `slug#<slug>` | A **lock** that reserves a site name and points to the token id (so two students can't get one name) | portal (conditional put: only if absent) | no |
| `domain#<domain>` | A lock for a custom domain name | domains | no |
| `fail#<ip>` | Failed teacher logins from an IP (count, `locked_until`) | portal | yes (30 min) |
| `ip#<slug>#<hash>#<minute>` | Per-visitor-IP request counter for a student's backend, per minute | gateway | yes (2 min) |
| `inflight#global`, `inflight#<slug>` | How many backend invocations are running right now | gateway | self-heals |
| `runs#<slug>#<period>` | A student's backend run allowance counter | gateway | yes (period end + 7 days) |
| `sops#<slug>#<period>` | Class storage operations counter | storage_api | yes |
| `scoll#...` / `sbytes#...` | A student's storage collection names / byte counters | storage | no |

`StudentDataTable` has a composite key: `s` (partition) = `<slug>#<collection>`, `id` (sort). Class storage data lives only here, and only the storage API can reach it.

Why one table? On-demand billing, one set of IAM rules, atomic counters next to the data they guard, and one backup setting (PITR). The cost: you must keep the prefix discipline in code (see `04` for how the counters stay correct).

## 4. Flows (follow each one slowly)

Each flow lists: **hop -> service -> identity -> limits -> failure**. Learn to draw these from memory.

### Flow A. Teacher signs in

1. Browser `POST https://deploy.<domain>/api/teacher/login` `{username, password}`.
2. **DNS** `deploy.<domain>` CNAME -> `PortalDistribution` domain. **TLS** terminated at CloudFront (ACM cert, SNI, `TLSv1.2_2021`).
3. **CloudFront** behavior `/api/*`: `PortalFunction` (viewer-request) sets header `x-viewer-ip` = the real client IP (overwriting anything the client sent). CloudFront adds `x-origin-secret`. Caching disabled. Forwards `Authorization` and other viewer headers (policy `AllViewerExceptHostHeader`).
4. **API Gateway HTTP API** `DeployApi`, route `ANY /api/{proxy+}` (throttle 20 rps, burst 50) -> **DeployFunction** (Lambda, role = its own SAM-generated role).
5. `portal.teacher_login`: reads `fail#<ip>` (is this IP locked out?), computes **scrypt** of the password (always, even for a wrong username, so timing does not reveal valid usernames), compares in constant time. After 5 failures it locks the IP for 15 minutes (`429`). On success: returns an HMAC-signed session.
6. **Failure modes**: wrong password `401`; locked `429`; teacher not set up `503`; throttled `429` from API Gateway.
7. **Look at it in the console:** CloudWatch -> Log groups -> `/aws/lambda/<stack>-DeployFunction-...`.

### Flow B. Student deploys a website (the upload path)

1. `npm run deploy` (or the student page) signs in: `POST /api/student/login {token}` -> `tok#sha256(token)` lookup -> signed session.
2. `POST /api/student/upload-url` (session) -> `portal_deploy` -> `upload_url(slug)` -> a **presigned S3 PUT URL** for `uploads/<slug>.zip`, valid **600 s**, plus `maxBytes` (50 MB).
3. The client `PUT`s the zip **directly to S3** (not through Lambda). Why: Lambda/API Gateway payloads are limited (API Gateway 10 MB, Lambda 6 MB sync) and you would pay for the bytes twice. A presigned URL lets the browser/CLI talk to S3 with a temporary permission. (Browser uploads need **CORS** on the bucket: see `03`.)
4. `POST /api/student/publish` -> `publish(slug)`: `HeadObject` size check, read the zip, **validate** (`site_entries`: no absolute paths, no `..`, count <= 2000, unzipped <= 200 MB, must have `index.html`), upload each file to `sites/<slug>/<path>` with a **Content-Type** and **Cache-Control**, delete files that disappeared, **invalidate** CloudFront (`/sites/<slug>/*`, `/<slug>/*`, and the custom-domain tenant), delete the zip, stamp `last_deploy_at`.
5. **Why this is safe:** the folder name comes from the session; the zip is parsed in memory (never extracted to a filesystem, so zip-slip cannot write outside S3 keys, and the path checks reject traversal anyway); every key is built as `sites/<slug>/...`.
6. **Failure modes:** expired token `403`; zip too big/too many files `400`; no `index.html` `400`; CloudFront invalidation failure (logged, for tenants it does not fail the deploy).

### Flow C. A visitor opens `https://alice.apps.example.com/cart`

1. DNS `*.apps.example.com` CNAME -> `Distribution`. TLS with the wildcard ACM cert.
2. **Viewer-request** `RouterFunction`: reads the `Host` header, extracts `alice`, checks it matches `^[a-z0-9][a-z0-9-]{0,29}$`, applies the **SPA fallback** (last path segment with no dot -> `/index.html`), rewrites the URI to `/sites/alice/index.html`.
3. **Cache** (CachingOptimized): hit -> done (the common case). Miss -> go to origin.
4. **Origin S3** via **OAC** (CloudFront signs the request with SigV4; the bucket policy allows `s3:GetObject` on `sites/*` only for this distribution's ARN).
5. Response cached: `s-maxage=31536000` (one year at the edge), `max-age=0` for HTML (browsers revalidate) or 300 s for other files. Freshness comes from **invalidation on publish**, not short TTLs.
6. **Failure modes:** invalid name `404` (returned by the function). A site that does not exist gives **403** from S3, not 404: the bucket policy grants CloudFront only `s3:GetObject`, and S3 answers 403 for a missing key when the caller has no `s3:ListBucket`. If *every* site gives 403, suspect the OAC or the bucket policy.

### Flow D. A visitor calls a student's backend: `POST https://alice.apps.example.com/api/ask`

1. Same DNS/TLS. CloudFront behavior `/api/*` (only exists when `BackendEnabled`): `BackendRouteFunction` rewrites `/api/ask` -> `/b/alice/ask`, sets `x-viewer-ip`; CloudFront adds `x-origin-secret`; no caching.
2. **BackendApi** (HTTP API; throttle 10 rps, burst 20), route `ANY /b/{slug}/{proxy+}` -> **GatewayFunction**.
3. **The gateway's 9 checks** (`lambda/gateway.py`, in order): (1) origin secret, (2) path + slug shape, (3) body <= 1 MB, (4) backend exists and the token's lifecycle allows it, (5) teacher toggle ON, (6) per-visitor-IP rate limit (default 120/min), (7) **in-flight limiter** (3 global, 2 per student), (8) **run allowance** (atomic counter, default 5,000 per month), (9) invoke.
4. `lambda:Invoke` of `sd-alice` (**RequestResponse**). The student's function runs as **StudentSandboxRole** (logs only), memory 256 MB, timeout 10 s (25 s if class AI is on).
5. The gateway **validates and shapes** the result (status 100-599, string headers, body <= 1 MB, adds `nosniff` and `no-store`).
6. **Failure modes** (each a small JSON error): `403 forbidden` (no origin secret), `404 no_backend`, `503 backend_off`/`site_offline`, `403 token_expired`, `429 too_many_requests`/`busy`/`allowance_exhausted`, `502 function_error`/`bad_response`, `504 function_timeout`.

### Flow E. A student's backend uses class AI

- **OpenRouter student:** the function reads `OPENAI_API_KEY` (a per-student, **capped, revocable** OpenRouter key created by the portal with the platform's *management key*), `OPENAI_BASE_URL`, `AI_MODEL` from its environment and calls OpenRouter **directly** over the internet. The spending cap is enforced by OpenRouter, not by AWS.
- **Gemini student:** Google has no per-student keys, so the function gets `OPENAI_BASE_URL` = your `AiProxyApi` and a private key `ga_<slug>.<secret>`. `AiProxyFunction` checks the key (constant time), token active, teacher switch, forces the model, caps output, calls Google with the **platform's** key from Secrets Manager, and adds the cost to the student's counter.

### Flow F. A student's backend uses class storage

`sd-alice` -> HTTPS to `StorageApi` with `Authorization: Bearer cs_alice.<secret>` -> `StorageFunction.authenticate`: the key **names the slug** and the secret must match (constant-time) -> token active -> teacher switch on -> `charge` (one operation off the allowance, atomic) -> operation. **Isolation by construction:** the data key is built from the caller's own slug (`<slug>#<collection>`), the caller never supplies the slug. Files: a **presigned POST** (with a policy limiting size and content type) to `userdata/<slug>/...`.

### Flow G. A student adds a custom domain `www.theirname.com`

1. Teacher switches the feature on. Student adds a CNAME to the **routing endpoint** of the connection group, then types the domain in the portal.
2. `PUT /api/student/domain` -> `domains.set_domain`: validates the name, writes the `domain#<name>` lock, calls CloudFront `CreateDistributionTenant` with a **managed certificate request**. CloudFront refuses until the CNAME is visible (ownership), so the first call just retries: state `waiting_dns`.
3. The page polls `GET /api/student/domain/status` (every 8 s); each call advances **one step**: `issuing_certificate` -> `finishing` -> `live`. (New in 2.0: while waiting, the portal also asks a public DNS-over-HTTPS resolver what the CNAME points at and shows a hint.)
4. Traffic: browser -> CloudFront **tenant** (origin path `/sites/{{student}}`) -> S3; `/api/*` -> gateway with origin path `/b/{{student}}`.

### Flow H. The daily sweeper

EventBridge schedule `cron(0 3 * * ? *)` -> `SweeperFunction` -> for each token past `expires_at + grace (3 days)`: `archive_site` copies `sites/<slug>/` to `archive/<slug>/` and deletes the original (CloudFront only serves `sites/*`, so the site goes offline; **nothing is deleted** permanently), invalidates, finishes pending domain removals. Errors raise an exception -> the `SweeperErrorsAlarm`.

### Flow I. Install, update, destroy (the control plane for *you*)

`npx ... install` -> pre-flight (Node, `sts get-caller-identity`, `cloudfront list-distributions`) -> ACM certificate -> artifacts bucket -> `cloudformation package` -> `cloudformation deploy` (create stack) -> publish the portal pages to S3 `portal/` -> custom-domain pieces -> smoke test. **Update** = a **change set** (the *plan*), reviewed (no replacement of data resources), then `execute-change-set` (the *apply*). **Destroy** = tenants and the multi-tenant distribution first, empty the bucket (all versions), delete the stack. Details in `08`.

## 5. Trust boundaries (where an attacker must cross a line)

| # | Boundary | What enforces it |
|---|---|---|
| 1 | Internet -> CloudFront | TLS, HTTPS redirect, (no WAF) |
| 2 | CloudFront -> S3 | OAC signing + bucket policy (`AWS:SourceArn`) + Block Public Access |
| 3 | CloudFront -> API Gateway | `x-origin-secret` header (only guards the *forged IP* header and the gateway; see `09`), and each handler's own auth |
| 4 | Visitor -> student function | The 9 checks of the gateway (a function is **never** directly invokable by a visitor) |
| 5 | Student function -> AWS | StudentSandboxRole + permission boundary (logs only) |
| 6 | Student function -> platform APIs | Per-student keys (`cs_`, `ga_`) checked in constant time |
| 7 | Student -> other students | Session-derived slug; slug-prefixed keys; locks |
| 8 | Portal -> your AWS account | DeployFunction's IAM policy (broad but resource-scoped: `sd-*` functions only, one PassRole) |
| 9 | You -> AWS | Your own IAM user (AdministratorAccess for the install) |

## 6. A time budget you should be able to recite

`Browser` -> CloudFront (no hard limit) -> **API Gateway HTTP API: 30 s hard limit** -> **GatewayFunction timeout 29 s** -> student function **10 s** (25 s with AI). Each layer is slightly shorter than the one in front of it, so the inner layer times out first and returns a clean JSON error instead of a gateway 504.

Sizes: HTTP API request/response up to 10 MB; the **gateway** refuses bodies over 1 MB and responses over 1 MB; Lambda synchronous payload limit is 6 MB.

## 7. Console tour (Mode B): see the picture in AWS

Do this once, slowly. It takes 20 minutes and makes everything else easier.

1. **CloudFormation** -> Stacks -> `student-hosting` -> **Resources** tab: this is the inventory in section 4.1 of `00`. Click **Template** -> *View in Infrastructure Composer* to see a diagram.
2. **CloudFront** -> Distributions: two of them (`Student frontends (student-hosting)` and `Deploy portal (student-hosting)`). Open each: **Origins**, **Behaviors**, **Functions** (association), **General** (alias, certificate, price class).
3. **S3** -> your bucket (`student-hosting-sitesbucket-...`) -> **Objects**: see the five prefixes.
4. **API Gateway** -> APIs: four HTTP APIs. Open one -> **Routes**, **Integrations**, **Stages** (default stage, throttling).
5. **Lambda** -> Functions: `...-DeployFunction-`, `-GatewayFunction-`, `-StorageFunction-`, `-AiProxyFunction-`, `-SweeperFunction-`, plus `sd-<slug>` ones. Open one -> **Configuration** -> *Permissions*, *Environment variables*, *Triggers*.
6. **DynamoDB** -> Tables -> `...TokensTable...` -> **Explore table items**.
7. **Systems Manager** -> Parameter Store: `/student-deploy/teacher`, `/student-deploy/keys`.
8. **Secrets Manager** -> `openrouter` (metadata only: do not reveal the value).
9. **CloudWatch** -> Alarms: the 7 alarms and their state.
10. **IAM** -> Roles: search `student-hosting`.
