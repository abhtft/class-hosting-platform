# Backend wire: feasible plan

> **Historical record.** This document describes the design plan as it was when it was written. For current behaviour, limits and configuration see [docs/TECHNICAL_SPECIFICATION.md](docs/TECHNICAL_SPECIFICATION.md), the source of truth. Statements about "a limit of 10" concurrent Lambda executions describe the situation then: the limit is now 1,000 (granted 2026-10-22).

Status: **proposal with owner decisions recorded (2026-10-20), nothing built yet.** Written against the current `student-deploy` stack.
Items marked **(verify)** are facts I could not confirm from this account and must be checked before they are relied on.

---

## Decisions recorded (owner, 2026-10-20)

| # | Decision |
|---|---|
| Q1 | **Yes: platform-hosted student backends** (the design in this document). Phase 0 docs stay as the fallback for students who host elsewhere |
| Q2 | **Same AWS account.** Student code runs next to your other apps, so the safeguards in section 6 are mandatory |
| Q3 | **Quota stays at 10 for now** (scratch level); the owner will request the increase later. Consequence: no per-student reserved concurrency yet, so the plan uses an **in-flight limiter** instead (section 5.5, "Working within a limit of 10") |
| Teacher controls (owner proposal, adopted) | The teacher sets **how many runs each student may use** (a run allowance per day, month or total) and has one **Backend on/off toggle**. Details in 5.5b. The toggle doubles as the kill switch |
| Q4 to Q9 | Not answered, so **my defaults stand**: Node 22 + Python 3.12, same-origin `/api/*`, bring-your-own database (env vars), default limits as listed, switch off by default, nothing deleted automatically |

What "scratch level" means for scope: **a pilot with the teacher's own test student and a few trusted students, low traffic.** It is not a general launch. The documented rule stays: before broader use, raise the quota and switch the limiter's caps over to reserved concurrency.

---

## Implementation status (2026-10-21)

**Deployed to production and verified live (48 checks, temporary test student, nothing left behind).** The template default for `BackendEnabled` is still `false`; `samconfig.toml` turns it on for this stack. Pilot rules apply (quota still 10).

| Plan item | Status |
|---|---|
| Phase 0: docs for students (external backends, contract, limits) | Done: `student/BACKEND.md` |
| 1b: control plane (`lambda/backends.py`), gateway (`lambda/gateway.py`), run allowance, in-flight limiter, per-IP limit | Done, tested (exact counting under parallel calls, leak recovery, lifecycle, header hygiene, response shaping, real Python and Node handlers) |
| 1b: template (Backend API, gateway, sandbox role + boundary, scoped IAM, `/api/*` on the student distribution, route function) | Written behind `BackendEnabled`, `cfn-lint` clean, IAM scoping guarded by `tests/test_template_backend.py` (mutation-tested) |
| 1c: student Backend card, teacher on/off, **Runs…**, **+1,000**, usage on rows, starter templates, local runner | Done, checked in the browser against the dev server |
| 1c: CLI parity (`deploy.mjs --backend`) | **Not done.** Portal only. The CLI keys have no token record to attach settings to; needs a design decision |
| 1a / 1d: live verification in AWS | **Done 2026-10-21.** Deploy (Python and Node 22), calls through CloudFront, env vars, logs (7-day retention), exact allowance, OFF/ON, reserved concurrency 0 accepted at a limit of 10, in-flight limiter (8 parallel requests: 3 ran, 5 busy), removal, cleanup. Three defects found and fixed, see below |
| `/api/*` on custom domains (multi-tenant distribution) | **Done 2026-10-21**, after a student's first real use of a custom domain (`test.votebecho.com`) got an HTML 403 on every POST. Applied with `custom-domain/apply-tenant-api-route.py`; verified live (analyse, submit, health, SPA fallback, forged-header and direct-API checks) |
| Phase 2 rest: CORS allow-list, "Request more runs" | Pending |
| Quota increase (Lambda concurrent executions) | Owner to-do before enabling more than a few students |

Defects the live run found (all fixed, each with a regression test): (1) a **circular dependency** between the distribution, the Backend API and the gateway through the shared Globals environment, which `cfn-lint` did not catch and CloudFormation refused; (2) the backend uploader used the default S3 client, which signs **presigned URLs with SigV2**, so every upload failed with `SignatureDoesNotMatch` (fakes could not show it); (3) the gateway cached "no backend" for 10 s, so a student who had just deployed could see `no_backend` for seconds (now 2 s for sites without a ready backend).

Still open: the teacher toggle and **Runs…** through the real teacher page (the live test set the same fields directly in the table; the route itself is unit-tested), `/api/*` on custom domains, CLI deploys, and the Lambda quota increase.

---

## 0. In one page

**The idea.** A student's frontend usually needs a small backend. Today the portal hosts static files only, so students must bring their own HTTPS backend with CORS. "Backend wire" lets a student (when the teacher allows it) upload a small function and have the platform **wire it** to their site in one standard way:

```
Student site  https://<slug>.example.edu   (or their custom domain)
   frontend calls   fetch('/api/hello')          ← same origin, no CORS, no URL to configure
        │
   CloudFront  /api/*  →  Backend API (API Gateway HTTP API)  →  Gateway Lambda  →  student's Lambda  sd-<slug>
```

**What the student does:** drop a `backend` folder (or zip) next to their frontend, deploy from the portal or the CLI, call `/api/...`.
**What the teacher does:** per student, one **Backend on/off** toggle and a **run allowance** (how many times their function may run per day, month or in total), with usage visible on the dashboard.
**What the platform guarantees:** every student function runs in a sandbox with no access to anything of yours, hard limits on time, memory and concurrency, and a kill switch.

**My recommendation (updated for the decisions above):** build it as a **small pilot**. The two gates from the first draft are relaxed but not ignored:
1. **Lambda quota stays at 10 for now.** A student's traffic could otherwise starve your other apps, and reserved concurrency cannot be set at this limit. So the gateway enforces its own **in-flight limiter** (a hard cap on how many student requests run at once) and the Backend API gets a low throttle. This is weaker than reserved concurrency but keeps most of your 10 slots free. **Request the quota increase before enabling more than a few students.**
2. **Same account:** the permission boundary, name-prefix scoping and sandbox role in section 6 are mandatory, because a mistake here lands next to your other apps.

Estimated effort once the gates are clear: **about 6 to 8 working days** for a first usable version (Phase 1), plus 1 to 2 for same-origin on custom domains and CLI parity (Phase 2). Phase 0 (documentation only) can ship today.

---

## 1. What "backend wire" means here (my assumptions)

You wrote "backend wire standard (lambda and api gateway method)". I read that as:

> A **standard, platform-managed way to attach a Lambda function behind an API Gateway route to a student's site**, so a student can have a backend without running servers or configuring CORS, certificates or URLs.

If you meant something narrower (for example only a *convention* for how a frontend finds a backend the student already runs elsewhere), then **Phase 0 alone is the answer** and the rest of this document can be skipped. See open question Q1.

**In scope:** deploying and running a student's function; routing; limits; env vars; logs; teacher controls; tie-in to token expiry; CLI parity.
**Out of scope (v1):** databases hosted by the platform, WebSockets, scheduled jobs, container images, custom runtimes, private networking (VPC), per-student billing.

---

## 2. Findings that shape the plan

Read-only checks on the account (2026-10-20):

| Fact | Value | Why it matters |
|---|---|---|
| Lambda **concurrent executions limit** | **10** (all 10 unreserved) | The decisive finding. See below |
| Existing Lambda functions | 15 | Shared with your other projects |
| HTTP APIs in the account | 4 (`student-hosting`, `qpv1`, `prashnotri-api`, `bs_submit_link`) | Other live apps share the same Lambda concurrency pool |
| Max unzipped code size | 250 MB (account limit) | Fine; we will cap far lower |
| Region | us-east-1 | Same as the bucket and the stack |

**The concurrency problem, plainly.** Lambda concurrency is account-wide. With a limit of 10:
- If a student's function (or a loop calling it) uses all 10 slots, **your other applications get throttled** (`prashnotri-api`, `bs_submit_link`, the portal itself).
- The standard defence is **reserved concurrency per student function** (for example 2). AWS requires a minimum of unreserved concurrency to stay available, so with a limit of 10 **no reservation can be set at all (verify)**. We would have no hard ceiling per student.
- The gateway design doubles invocations (gateway + student function), which makes the pool tighter still.

**Gate 0 (owner decision: deferred; pilot only at a limit of 10, see "Working within a limit of 10" in 5.5).** The full fix is to request a quota increase for Lambda concurrent executions (Service Quotas, "Concurrent executions", us-east-1) to **at least 1000**. AWS often grants this to accounts with normal usage history but it can take a day or more; if it is refused or small, use a separate account (below).

**Gate 1 (owner decision: same account).** The alternative, a separate AWS account under AWS Organizations, stays documented below in case it is wanted later.

| | Same account | Separate "student-runtime" account |
|---|---|---|
| Blast radius of a bad student function | Your other apps (concurrency, logs, cost, IAM mistakes) | Contained to that account |
| Setup | None | Create account, cross-account invoke role, bucket access for code, billing alarm (about half a day) |
| Permission model | Prefix-scoped IAM + permission boundary (section 6) | Same, but a mistake costs far less |
| Cost visibility | Mixed into one bill | Separate bill by account |

My view: **a separate account is the right long-term design**; same account is acceptable only for a pilot with one or two trusted students and the safeguards in section 6.

Other facts I would verify early in a one-day spike (section 9, Phase 1a):
- HTTP API quotas: routes and integrations per API (default 300 each **(verify)**). The design below avoids per-student routes, so it is not affected, but confirm throttling settings behave as expected.
- That a CloudFront behavior `/api/*` with a viewer-request function can rewrite the path using the host (same pattern as the existing router function).
- Whether the hand-made **multi-tenant distribution** (custom domains) accepts an extra `/api/*` behavior **(verify)**.

---

## 3. Goals and non-goals

**Goals**
1. A student with a working frontend can add a backend in **under 10 minutes**, with no AWS knowledge.
2. **Same-origin calls** (`/api/...`): no CORS configuration, works on `<slug>.example.edu` and on custom domains.
3. **Nothing a student runs can touch anything else**: not your other apps, not other students, not platform data.
4. **Bounded cost** even under abuse.
5. **Teacher control**: per-student switch, limits, a kill switch, visible state.
6. Reuse what exists: tokens, sessions, the sweeper, the polling pattern from custom domains, the same fakes and dev server for tests.

**Non-goals (v1)**
- Running arbitrary long jobs, background workers, or anything over 10 to 15 seconds per request.
- A managed database (students use external services such as Supabase through environment variables).
- Fine-grained per-endpoint routing configuration. One function per student handles all `/api/*` paths itself.

---

## 4. Options considered

| # | Option | Pros | Cons | Verdict |
|---|---|---|---|---|
| A | **Docs only**: students host a backend elsewhere (Vercel, Railway, Supabase), set `VITE_API_URL`, allow CORS | No risk, no cost, ships today | Students need accounts elsewhere; every student has CORS pain | **Phase 0**, always useful as the fallback |
| B | **One Lambda + route per student** on a shared HTTP API | Familiar "API Gateway method per route" model; route-level throttling | Routes and integrations are limited per API (hundreds), IAM and route plumbing per student, more control-plane code | Rejected for v1 |
| C | **Gateway Lambda + one catch-all route** (`ANY /b/{slug}/{proxy+}`) that invokes the student's function | Unlimited students, one place for auth/limits/logging/kill switch, **no per-student API plumbing**, easy to test | One extra hop (about 10 to 30 ms, doubles invocations) | **Chosen** |
| D | Lambda **Function URLs** per student | No API Gateway, simplest | You asked for API Gateway; no central throttle, auth or kill switch; each URL is a public endpoint | Rejected |
| E | Run student code **inside** a platform Lambda (`vm`/`eval`) | Cheapest | Not a real sandbox; one escape owns everything | **Rejected: unsafe** |
| F | CloudFormation stack per student | Declarative | Minutes per change, stack limits, heavy | Rejected |

**Why C satisfies "Lambda and API Gateway method".** The API Gateway method is a single `ANY` route on a dedicated HTTP API; the Lambda is the student's own function, called through the gateway. Students never see or configure either; they just write a handler.

---

## 5. Proposed design

### 5.1 Request path

```
Browser ── https://alice.example.edu/api/hello?x=1
   │
CloudFront (student Distribution)
   ├─ default behavior  → S3 sites/alice/ ...                 (unchanged)
   └─ /api/*  behavior  → origin: Backend API (HTTP API)
        viewer-request function: host "alice.…" → slug "alice";  uri → /b/alice/hello
                                  sets x-viewer-ip;  origin adds x-origin-secret
   │
API Gateway HTTP API (dedicated "BackendApi", own throttle)   route:  ANY /b/{slug}/{proxy+}
   │
Gateway Lambda  (platform code, small role)
   1. verify x-origin-secret  (reject direct calls)
   2. look up slug → token → backend settings (DynamoDB, cached ~30 s)
   3. enforce: switch on, token active, function ready, per-student rate limit
   4. clean the event (strip platform headers, real client IP, 1 MB body cap)
   5. lambda:Invoke  sd-alice  (sync, timeout = student limit)
   6. validate the response shape, cap size, add safe headers, return
   │
Student function  sd-alice   (sandbox role: logs only; reserved concurrency; 256 MB; 10 s)
```

Why a separate **BackendApi** instead of the existing `DeployApi`: the existing API is throttled at 20 req/s for the portal and the CLI. Student traffic needs its own throttle and its own failure domain so a busy student site cannot make the portal unusable.

### 5.2 The standard contract (what a student writes)

One function, one entry point, standard HTTP in and out.

| | Node | Python |
|---|---|---|
| Runtime | `nodejs22.x` | `python3.12` |
| Entry | `index.handler` (`index.mjs` or `index.js`) | `app.handler` (`app.py`) |
| Input `event` | API Gateway HTTP API **payload v2** shape: `rawPath` (prefix removed, e.g. `/hello`), `rawQueryString`, `requestContext.http.method`, `headers`, `body`, `isBase64Encoded`, `requestContext.http.sourceIp` set to the real client IP | same |
| Output | `{ statusCode, headers, body, isBase64Encoded? }` | same |

Platform limits (editable by the teacher within caps; defaults are proposals):

| Limit | Default | Cap |
|---|---|---|
| Zip size (upload) | 5 MB | 20 MB |
| Unzipped size | 50 MB | 100 MB |
| Memory | 256 MB | 512 MB |
| Timeout | 10 s | 15 s |
| Reserved concurrency | **not available at a limit of 10**; replaced by the in-flight limiter | 2 per student once the quota is raised |
| In-flight student requests (limiter) | 3 globally, 2 per student | 5 globally after the quota is raised |
| Request body | 1 MB | 6 MB |
| Response body | 1 MB | 6 MB |
| Environment variables | 20 names, 4 KB total | n/a |
| Requests per minute per student | 600 | 3000 |
| **Run allowance** (teacher-set, per student) | 5,000 per month | 1,000,000 per period; 0 means none |
| Requests per minute per visitor IP (per student) | 120 | 600 |

Dependencies: students **bundle their own** (`npm install --omit=dev` before zipping, or esbuild); the platform never runs `npm install`. Starter templates (`student/backend-template/node` and `python`) and a local runner (`node backend-test.mjs`) make the contract concrete and testable before uploading.

### 5.3 Student experience

Portal card **Backend** (below Upload, above Custom domain), same pattern as the domain card:

1. Off: disabled with "Owner not enabled for you. Please contact site owner." If the teacher turns it off later, the card says so and the site's `/api` stops answering.
2. On, nothing deployed: drop a folder or zip, pick the runtime (auto-detected from `index.*` / `app.py`), **Upload and publish**.
3. Pre-flight checklist (client side, like the frontend checker): entry file present, size, no `.env`, no `node_modules` over the limit, handler export found.
4. States shown as a stepper: *Uploading, Creating function, Ready* (or *Failed* with the AWS message).
5. After ready: shows the URL pattern `/api/...`, a **Test** button (`GET /api/health`, counts as one run), environment variables (names only; values write-only), recent logs, **Remove backend**.
6. A **usage meter** in the same style as the expiry bar: `3,420 / 10,000 runs this month, resets 1 Nov`, amber at 80%, red at 100%. When the allowance is used up the card explains what visitors see and who to ask.

CLI parity: `node deploy.mjs --backend ./backend` zips, uploads and publishes it with the same key as the frontend.

### 5.4 Control plane (what manages the Lambdas)

A new module `lambda/backends.py` in the existing Deploy Lambda, plus routes in `lambda/portal.py`. It reuses the **upload then publish** pattern (presigned PUT to `uploads/<slug>-backend.zip`, then publish) and the **polling state machine** pattern from custom domains, because creating or updating a function takes seconds and the API call limit is 29 s.

Steps on publish:
1. Validate the zip (path safety, size, entry file, no symlinks) **without executing anything**. Reuse the existing zip validation helpers.
2. `CreateFunction` (first time) or `UpdateFunctionCode` (later) from the S3 object; name `sd-<slug>`; role = the shared **sandbox role**; runtime/handler from the detected files; memory/timeout from the limits; tags `managed-by=student-deploy`, `slug=<slug>`.
3. `PutFunctionConcurrency` (reserved) **only when the account quota allows it**; at a limit of 10 this step is skipped and the in-flight limiter (5.5) is the cap. Setting it to 0 for the kill switch does not reserve anything and should still work (verify in the spike).
4. Create the log group with **7-day retention** (otherwise logs never expire and cost grows).
5. Poll `GetFunction` until `State=Active` and `LastUpdateStatus=Successful`, advancing one step per status call; the student's UI polls like the domain card.
6. Smoke-invoke `GET /health` through the gateway path (optional but cheap) and report.

Data on the token item (no new table):

```
backend: {
  enabled: bool            # teacher toggle: ON serves and allows deploys, OFF serves nothing
  runs_limit: int          # teacher-set allowance (0 = none)
  runs_period: day | month | total
  state: none | creating | ready | failed | removing
  runtime, function_name, code_sha256, deployed_at
  memory, timeout, concurrency      # effective limits
  env_names: [..]                   # names only, never values
  error: "..."                      # last failure, shown to the student
}
```

### 5.5 Gateway Lambda (data plane)

A **separate function** with its own tiny role (`dynamodb:GetItem` on the table; `lambda:InvokeFunction` on `sd-*` only). It is stateless and safe to redeploy. It:
- authenticates the caller by `x-origin-secret` (direct hits on the API Gateway URL get 403);
- validates `slug` against the slug pattern before using it in any name;
- applies **per-student rate limiting** with a DynamoDB atomic counter per minute (cheap; HTTP APIs have no usage plans);
- applies the **in-flight limiter** (below) and the teacher's **run allowance** and per-IP limit (5.5b);
- enforces lifecycle (section 5.7);
- maps errors to safe, consistent JSON: function timeout, function error, throttled, body too large;
- never forwards the platform's own headers; always sets `x-content-type-options: nosniff`.

#### Working within a limit of 10 (in-flight limiter)

Every student request occupies **two** Lambda slots while it runs: the gateway waits while the student function executes. With 10 slots in the whole account, the plan is to let student traffic use **at most about 6 of them** and leave at least 4 for your other apps:

- **Global cap:** at most **3** student requests in flight at once (3 gateway + 3 student executions = 6 slots).
- **Per-student cap:** at most **2** in flight, so one student cannot use the whole student share.
- Implemented with **atomic DynamoDB counters** (`inflight#global`, `inflight#<slug>`): a conditional increment before invoking, a decrement in a `finally` block. A request over the cap gets an immediate `429` with `Retry-After: 1` (the gateway answers in a few milliseconds, so rejecting is cheap).
- **Leak recovery:** each counter stores `updated_at`; if a counter is above zero but has not changed for longer than the maximum function timeout plus a margin, the next request resets it. This covers a gateway crash between increment and decrement.
- **Edge throttle:** the Backend API stage is throttled low (for example 10 requests per second, burst 20) so floods are cut before they reach any Lambda.
- **Short timeout (10 s) and small memory (256 MB)** keep each slot occupied briefly.
- **Known trade-off:** a frontend that fires several parallel `fetch` calls on page load may see an occasional `429`. The starter template includes a tiny retry helper, and the docs say so.
- **When the quota is raised:** switch to per-student reserved concurrency (the original design) and raise the global cap; the limiter can stay as a second line of defence.

This protects your other apps *statistically and by construction* (student code can never hold more than about 6 of 10 slots), but it is **not** a hard AWS-enforced reservation: a bug in the limiter itself would remove the protection. That is why it is covered by invariant tests and why the pilot stays small.

### 5.5b Teacher controls: on/off and run allowance

Two controls per student, both set on the teacher page and both **enforced by the gateway on the server** (the student UI only explains them).

**1. Backend: on / off** (one toggle, default **off**)
- **ON:** the student can deploy, and the function serves requests.
- **OFF:** the function serves **nothing** (the gateway answers `503 {"error":"backend_off"}`), and deploys and environment-variable changes are refused. Code, variable names and counters are kept, so turning it back on restores service exactly as it was.
- This **replaces the separate "Disable" kill switch** from the first draft: the toggle *is* the kill switch. It differs on purpose from the custom-domain switch (a live domain keeps working when its switch is turned off); a backend runs code, so OFF means stop.
- Speed: the gateway re-reads a student's settings at most every **10 seconds**. For an instant stop, the toggle also sets the function's reserved concurrency to 0 where the account allows it (verify in the spike).

**2. Run allowance**
- The teacher picks a **number of runs** and a **period**: `day`, `month` (default) or `total` (for the life of the token). Default when switching on: **5,000 per month**. Editable any time through a **Runs…** dialog (number and period), with a quick **+1,000**. Cap 1,000,000; 0 means none.
- **What counts as a run:** one invocation of the student's function that passed every earlier check. Failures and timeouts count (AWS bills them); the portal **Test** button counts. Requests rejected earlier (toggle off, token expired, in-flight cap, bad origin) do **not** count.
- **How it is counted, with no overshoot:** one atomic DynamoDB update per run on an item named `runs#<slug>#<period-id>` (period id is `2026-10`, `2026-10-20` or `total`, in UTC):
  `ADD count 1` with the condition `attribute_not_exists(count) OR count < :limit`.
  Parallel requests cannot push the count past the limit, and old period items expire on their own (TTL).
- **Order of checks in the gateway:** origin secret, then token lifecycle and toggle, then per-IP limit, then acquire an in-flight slot (a cheap fast reject), then consume one run (if the allowance is exhausted, release the slot), then invoke, then release the slot.
- **When the allowance is used up:** `429 {"error":"allowance_exhausted","message":"This site's backend has used its allowed runs. Please contact the site owner.","resets_at":<epoch or null>}` with a `Retry-After` header. The student's frontend can show the message.
- **What people see:**
  - Student: the usage meter described in 5.3.
  - Teacher: each row shows `Backend: on · 3,420 / 10,000 (month)` with the buttons **Backend on/off**, **Runs…**, **+1,000**. The teacher list reads usage with one batched read of only the students whose backend is on, so it does not scan.
- **Optional (Phase 2): "Request more runs."** From the exhausted state the student sends a short note; it lands in the existing **Requests** inbox next to regeneration requests; approving sets the new number. It reuses the regeneration flow almost unchanged.
- **Cost ceiling this gives (approximate, verify prices):** runs x time x memory. The default 5,000 runs x 10 s x 0.25 GB is about 12,500 GB-seconds, roughly **$0.17 per student per month at the very worst**, instead of about $17 for the concurrency-only bound. The allowance is the real cost control; concurrency limits protect your other apps.
- **Limitation to accept and communicate:** the student's `/api` is public, so a stranger or a bot can burn a student's allowance. Mitigations: the per-IP limit (default 120 requests per minute per visitor, per student), the in-flight cap, teacher top-ups, and a CloudFront WAF rate rule if abuse ever appears. The allowance protects the *platform's cost*; it cannot tell a real visitor from a bot.

### 5.6 Environment variables (secrets) and logs

- `PUT /api/student/backend/env {name, value}`, `DELETE …/env/{name}`. Values go straight into the function's configuration (encrypted at rest by AWS), **never stored in DynamoDB and never returned**; the portal shows names only.
- Reserved names (`AWS_*`, `_HANDLER`, `LAMBDA_*`) are refused. Every change triggers `UpdateFunctionConfiguration`.
- Logs: `GET /api/student/backend/logs` returns the last N lines using `filter_log_events` on `/aws/lambda/sd-<slug>`, with a hard cap, so a student can debug without console access. Students are told not to log secrets.

### 5.7 Lifecycle integration (reuses the token model)

| Token state | `/api/*` behavior |
|---|---|
| active, backend ON, allowance left | works |
| active, backend ON, allowance used up | 429 `allowance_exhausted` with `resets_at` |
| backend turned **OFF** by the teacher | 503 `backend_off` within about 10 seconds; deploys and env changes refused; code kept (this toggle is the kill switch) |
| expired (grace) | 403 "Your token has expired" |
| offline / archived | 503 "Site offline" |
| revoked | 403 |
| teacher "Take offline" | same as offline; function and code are kept |

Functions and code are **never deleted automatically**, matching the existing "nothing is deleted without a human" policy. Removal is explicit (student or teacher). The sweeper adds one job: finish removals the student did not complete.

### 5.8 Same-origin `/api` (CloudFront)

- **Phase 1:** add the `/api/*` behavior to the CloudFormation-managed student `Distribution`: a second origin (Backend API) with `x-origin-secret`, `CachingDisabled`, `AllViewerExceptHostHeader`, all methods allowed, and a viewer-request function that maps `Host → slug` and rewrites the path. Same technique already used for the portal's viewer IP.
- **Phase 2:** the hand-made multi-tenant distribution used for custom domains needs the same behavior and function. It is outside CloudFormation, so this is a documented manual change **(verify tenant-only distributions accept extra behaviors)**. The tenant parameter `student` already carries the slug, so the rewrite can read it from the tenant instead of the host.
- Side effect to communicate: a student's SPA that previously relied on `/api/...` falling through to `index.html` will now reach the gateway, which returns a JSON 404 when no backend exists. This is rare and acceptable.

---

## 6. Security and abuse model

**Threats and mitigations**

| # | Threat | Mitigation |
|---|---|---|
| 1 | Student function reads platform data (S3, DynamoDB, SSM, other functions) | **Sandbox role** has only `logs:CreateLogStream/PutLogEvents`. A **permission boundary** caps it even if someone attaches more. No VPC, no shared secrets in env. Function names are prefix-scoped |
| 2 | Student starves **your other apps** of concurrency | At a limit of 10: the **in-flight limiter** (global 3, per student 2), a low Backend API throttle, short timeouts, pilot-scale only. After the quota is raised: **reserved concurrency per student** as well. **Do not enable more than a few students until the quota is raised** |
| 3 | Cost runaway (crypto mining, loops, high traffic) | Teacher-set **run allowance** (the hard cost ceiling), timeout 10 s, memory 256 MB, in-flight limiter (reserved concurrency once the quota is raised), per-IP and per-student rate limits, HTTP API throttle, AWS Budgets alarm; worst case per student is bounded (section 7) |
| 4 | Abuse: spam relay, scanning, phishing hosting | Teacher approval per student (default off); logs with 7-day retention; kill switch (below); token expiry disables the backend. **Residual risk:** Lambda outside a VPC has unrestricted internet egress. Blocking it needs a VPC with NAT and egress filtering (cost and complexity), so I would accept this risk for a class setting and state it |
| 5 | Privilege escalation through the control plane | The Deploy Lambda's new permissions are **scoped by resource**: `lambda:*` only on `function:sd-*`, `iam:PassRole` only for the sandbox role with `iam:PassedToService=lambda.amazonaws.com`, `logs:*` only on `/aws/lambda/sd-*`. Tag condition `managed-by=student-deploy` on update/delete |
| 6 | Header or path spoofing at the gateway | Gateway requires the secret origin header; overwrites `x-viewer-ip`; strips platform headers; slug re-validated; method allow-list |
| 7 | Secret leakage | Env values write-only, never in DynamoDB, never logged by the platform, never returned |
| 8 | One student breaks the gateway for all | Gateway is separate, stateless, tiny, with its own API throttle; per-student rate limit prevents one student consuming it all |
| 9 | Stale or poisoned code | Each deploy records `code_sha256`; the teacher UI shows last deploy time; "Remove" always available |
| 10 | Lost access after teacher misconfiguration | OFF never deletes anything; turning it back on restores service |
| 11 | A stranger or bot burns a student's run allowance | Per-IP limit, in-flight cap, teacher **+1,000** top-up, optional WAF rate rule; the allowance is a cost control, not an anti-abuse guarantee |

**Kill switch (teacher):** the **Backend: on/off** toggle. OFF makes the gateway refuse every request for that student within about 10 seconds and blocks deploys, without deleting anything; where the account allows it, the function's reserved concurrency is also set to 0 so AWS itself refuses invocations (a reservation of 0 holds no slots, so it should work even at a limit of 10; **verify in the spike**). ON restores service. One click, reversible.

**Account-level safety net (do regardless of the rest):** an AWS Budget with email alerts at, say, 50% and 100% of a monthly amount you choose, and a CloudWatch alarm on Lambda throttles and errors for the gateway.

---

## 7. Cost model (approximate, verify current AWS prices)

| Item | Rough price | Notes |
|---|---|---|
| API Gateway HTTP API | about $1 per million requests | Free tier first year |
| Lambda requests | about $0.20 per million | Gateway + student function = **2 invocations per request** |
| Lambda duration | about $0.0000133 per GB-second (arm64) | 256 MB = 0.25 GB |
| CloudWatch Logs ingestion | about $0.50 per GB | Retention capped at 7 days |
| DynamoDB for rate limiting | about $1.25 per million writes | One write per request, optional batching |

**Worst case per student, running flat out:** reserved concurrency 2 × 256 MB = 0.5 GB continuously = about 0.0000067 USD/s, roughly **$0.58 per day**, about **$17 per month** if one student truly saturated their limit all month. With 30 students all maxed out that is about $500/month, which is exactly why the rate limit, the budget alarm and the kill switch exist. Normal class usage (a few thousand requests a day) is **a few cents per month**, mostly inside the free tier.

**With the teacher's run allowance the bound is much tighter** (5.5b): a student can never cause more compute than their allowance times the per-run limits, about **$0.17 per month** at the default 5,000 runs, and the teacher chooses the number. The concurrency numbers above then only describe how fast a student could burn through that allowance.

---

## 8. Quotas and constraints to respect

| Constraint | Value | Impact |
|---|---|---|
| Lambda concurrency (account) | 10 now | **Gate 0** |
| Lambda sync invoke payload | 6 MB | Request/response caps |
| API Gateway HTTP API payload | 10 MB | Not the binding limit |
| API Gateway integration timeout | 30 s | Hence our 10 to 15 s function timeout |
| Deploy Lambda timeout | 29 s | Hence the polling state machine for create/update |
| CloudFront function size/time | small, sub-millisecond | Rewrite logic must stay trivial |
| Function name length | 64 chars | `sd-` + slug (max 30) fits |
| Code storage | 75 GB total (account) | Negligible at 5 MB per student |
| Existing `DeployApi` throttle | 20 req/s, burst 50 | Reason for a dedicated Backend API |

---

## 9. Phased delivery

### Phase 0: documentation only (about half a day, no risk, can ship now)
- Add a "Backend options" page to the student docs: use an external backend, read the URL from `import.meta.env.VITE_API_URL`, HTTPS only, allow the site's origin in CORS, and when to ask the teacher for Backend Wire.
- Add a tiny example to `student/README.md`.
**Exit:** a student can follow it with zero platform changes.

### Phase 1a: gates and spike (1 day)
- File the **Lambda concurrency quota request** (Gate 0) today; it runs in the background.
- Decide same-account vs separate-account (Gate 1).
- Spike: hand-create one sandboxed function, one HTTP API with the catch-all route, one gateway Lambda, and a CloudFront `/api/*` behavior on a **test** distribution; call it end to end; confirm header rewriting, the origin-secret check, timeout and error behavior, and that reserved concurrency can be set (after Gate 0).
**Exit:** one request travels browser → CloudFront → API → gateway → student function and back, and the safeguards demonstrably work.

### Phase 1b: control plane and gateway (2 to 3 days)
- `lambda/backends.py` (validate, create/update, env, logs, remove, toggle, allowance and usage, status polling), `lambda/gateway.py` (including the in-flight limiter and the atomic run counter), template additions (Backend API, gateway function and role, sandbox role + permission boundary, scoped IAM for the Deploy Lambda, student distribution `/api/*` behavior and function, log retention, budget alarm), routes and token-view fields in `portal.py`, sweeper step.
- Tests: fake Lambda/Logs clients (like `FakeTenantCloudFront`), gateway unit tests (routing, lifecycle states, header hygiene, size caps, rate limit, error mapping, **allowance: exact count under parallel calls, period rollover, exhaustion response, slot release when the allowance is exhausted**), zip validation, env handling never echoing values, IAM-shaped checks on the template (a test that reads `template.yaml` and asserts the resource scoping, like the existing router test).
**Exit:** all local tests green; a changeset reviewed row by row.

### Phase 1c: UI, teacher controls, CLI (1.5 days)
- Student **Backend** card (states, checklist, env vars, logs, test button, remove); teacher **Backend: on/off**, the **Runs…** allowance dialog with usage on each row and **+1,000**, and the student's usage meter; starter templates and `backend-test.mjs`; `deploy.mjs --backend`; extend `tests/dev_server.py` to execute Python handlers in-process so the whole flow is clickable locally.
**Exit:** a first-time student deploys the starter backend and calls it, with a teacher turning it on first.

### Phase 1d: live verification (0.5 day)
Same discipline as custom domains: a **temporary test student** created directly in DynamoDB, the full flow against production, then verification that nothing is left (functions, log groups, rows, routes). Include negative tests: switch off, expired token, over-limit body, timeout, direct hit on the API URL, kill switch.

### Phase 2: custom domains and polish (1 to 2 days)
- `/api/*` on the multi-tenant distribution (spike first), CORS allow-list option for cross-origin use, per-student usage counters in the teacher view, log viewer improvements.

### Phase 3 (optional, only if asked): platform data
- A shared DynamoDB table with **per-student isolation** via IAM `dynamodb:LeadingKeys` and a per-student role, exposed as environment variables. Only worth it if students keep asking for a database without bringing Supabase.

---

## 10. Testing strategy

1. **Unit and integration tests on fakes** (the established pattern): programmable `FakeLambda` (function states, errors, throttle), `FakeLogs`, plus the existing store and CloudFront fakes.
2. **Template tests**: parse `template.yaml` and assert the sandbox role has only logs, the Deploy Lambda's new actions are scoped to `sd-*`, the gateway role cannot do more, and the origin-secret header is configured. These guard against someone widening IAM by accident.
3. **Local click-through**: `tests/dev_server.py` runs the gateway logic and executes Python student handlers in-process; the browser tests cover the UI states.
4. **Live verification** with a temporary student and negative tests (Phase 1d), then a leftover scan.
5. **Allowance tests** (counter semantics are where bugs hide): the limit is never exceeded by parallel requests, a request rejected before the invoke does not consume a run, exhausted allowance releases the in-flight slot, `day`/`month`/`total` roll over at UTC boundaries, and a teacher edit takes effect on the next request.
6. **Invariants, not only happy paths** (lesson from the offline-bypass bug): an expired or revoked student can never reach their function; a student can never invoke another student's function; the sandbox role can never read the platform's table or bucket.

---

## 11. Rollout, rollback, kill switch

- **Dark launch:** deploy all infrastructure with every student's switch **off**; nothing changes for existing users except the extra `/api/*` behavior returning JSON 404.
- **Pilot:** enable for the teacher's own test student, then one or two trusted students, for a week while watching the budget alarm, throttles and logs.
- **Rollback:** deploy with the `/api/*` behavior and Backend API removed (a parameter `BackendEnabled=false`, mirroring `TenantDistributionId=""`); functions are left in place (no data loss).
- **Emergency:** per-student **Backend: off** (and reserved concurrency 0 where allowed); global: set the Backend API throttle to 0 or remove the CloudFront behavior.
- **Deploys:** always the reviewed-changeset routine, from this folder only (see `implement.md`).

---

## 12. Risks and open questions

**Top risks**

| Risk | Likelihood | Impact | Plan |
|---|---|---|---|
| Quota increase refused or slow | Medium | **Blocks launch** | Use a separate account, or keep the pilot to one student with strict limits; do not launch broadly on a limit of 10 |
| A student abuses egress (spam, scanning) | Low to medium | Reputation, AWS abuse report | Teacher approval, logs, kill switch, accept and document residual risk |
| IAM scoping mistake | Low | High | Template tests; separate account reduces blast radius |
| Students cannot bundle dependencies | Medium | Support load | Starter templates, esbuild one-liner, clear checklist errors |
| Tenant distribution cannot take `/api/*` | Unknown | Custom domains lack the backend | Spike first; fallback is the absolute URL `https://<slug>.example.edu/api` |
| Cost surprise | Low | Medium | Budget alarm, rate limits, reserved concurrency |

**Decisions I need from you**

| # | Question | My default |
|---|---|---|
| Q1 | ~~Platform-hosted student functions?~~ | **Answered: yes** |
| Q2 | ~~Same AWS account or separate?~~ | **Answered: same account** |
| Q3 | ~~Request the Lambda concurrency increase now?~~ | **Answered: later; limit stays 10 for the pilot** |
| Q4 | Runtimes: Node 22 and Python 3.12 only? | Yes |
| Q10 | Default allowance for a newly enabled student, and default period? | 5,000 runs per month |
| Q11 | Should a student be able to **request more runs** from the dashboard (Phase 2), or is teacher-only editing enough? | Add the request flow in Phase 2 |
| Q12 | Is **"runs" = invocations** what you meant (my reading)? If you meant simultaneous runs, that is the in-flight cap (platform-set, could be shown but not teacher-edited in v1) | Invocations per period |
| Q5 | Same-origin `/api/*` (recommended) or a separate API host with CORS? | Same-origin |
| Q6 | Database: students bring their own (Supabase and similar) via env vars, or platform data in Phase 3? | Bring your own for v1 |
| Q7 | Default limits (256 MB, 10 s, concurrency 2, 600 req/min) and a monthly budget alarm amount? | As listed; you choose the alarm amount |
| Q8 | Teacher switch default **off**, like custom domains? | Off |
| Q9 | Should functions ever be deleted automatically (for example 60 days after token expiry)? | No, keep the "nothing deleted automatically" rule |

---

## 13. Acceptance checklist (for "done")

- [ ] Pilot scope agreed (same account, quota still 10): the in-flight limiter is tested, including leak recovery, and student requests never hold more than 6 of the 10 slots. The quota increase is on the owner's to-do list before broader use.
- [ ] A student with the switch **off** sees the owner message and cannot deploy (server returns 403).
- [ ] With the switch on, the starter backend deploys from the portal **and** from the CLI, and `/api/health` returns 200 on `<slug>.example.edu`.
- [ ] A change to the function is live within seconds of publishing.
- [ ] An environment variable set in the portal is visible to the function and never returned by any API.
- [ ] Logs are viewable by the student and expire after 7 days.
- [ ] Expired, offline and revoked tokens get the documented responses; the function is never deleted automatically.
- [ ] Over-limit requests (body size, timeout, rate) return clear, consistent JSON errors.
- [ ] A student cannot call another student's function, and the sandbox role cannot read the platform's table or bucket (tested).
- [ ] Direct calls to the Backend API URL without the origin secret are rejected.
- [ ] **Backend: off** makes `/api` answer 503 within about 10 seconds, refuses deploys, and turning it on restores service with nothing lost.
- [ ] The **run allowance** is exact (parallel requests never exceed it), shows as a meter for the student and a count on the teacher row, can be edited and topped up, and an exhausted allowance returns the documented 429 with `resets_at`.
- [ ] Budget alarm exists and has been tested.
- [ ] Template tests assert IAM scoping; the changeset was reviewed row by row; production verification leaves no leftovers.
- [ ] Docs updated (`implement.md` Part 4, README section, student guide) and `learning.md` extended with what we learned.
