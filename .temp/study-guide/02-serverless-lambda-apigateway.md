# Chapter 2: Serverless compute: Lambda, API Gateway, concurrency and the gateway design

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep. Numbers are shipped defaults from `config/settings.json`.

## A. Lambda fundamentals

**Q31 [B] What is AWS Lambda in one sentence, and what do you *not* manage?**
You upload code; AWS runs it on demand in short-lived sandboxes and bills per invocation and per GB-second. You do not manage servers, operating system patching, or scaling.
Study: [Lambda developer guide](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html).

**Q32 [B] What is a "cold start" and where do you see the cost in this project?**
The first request to a new execution environment must start the runtime and import your code. `docs/CRITICAL_ISSUES.md` item 10 notes about 10 s on the first request for some functions. Mitigations: small code, no heavy imports, arm64, caching at module level (warm starts reuse).
Study: [Lambda execution environment lifecycle](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html).

**Q33 [I] Why do all functions use `arm64` and `python3.12`?**
Graviton (arm64) is cheaper per GB-second and often faster than x86 for Python. Python 3.12 is a currently supported runtime. Pure-Python code with no native wheels is architecture-neutral, which is another reason for "no dependencies".
Repo: `template.yaml` `Architectures: [arm64]`.

**Q34 [I] What are Lambda's hard limits that shaped this design?**
Synchronous invoke payload 6 MB; request timeout up to 15 minutes (but API Gateway HTTP API integrations time out at 30 s); deployment zip 50 MB zipped / 250 MB unzipped; memory up to 10 GB (this platform's student backends default to 3008 MB because new accounts are capped lower); concurrency per account/region (default 1000, new accounts often 10).
Repo: `config/settings.json` `backend.*`; `docs/CRITICAL_ISSUES.md` item 3.
Study: [Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html).

**Q35 [I] Why can a student web request run only about 27 s but a background job 5 minutes?**
API Gateway's HTTP API has a hard 30 s integration timeout, so web requests must finish before it (27 s leaves headroom). Jobs avoid the HTTP chain: the gateway starts a *second* function `sd-<slug>_job` asynchronously, which can run up to 15 minutes (the teacher sets `jobs.minutes`, default 5).
Repo: `lambda/jobs.py`, `docs/BACKGROUND_JOBS.md`.

**Q36 [I] What is the difference between synchronous (`RequestResponse`) and asynchronous (`Event`) invocation?**
Sync: caller waits and gets the result or error. Async: Lambda queues the event, returns immediately, and *retries twice on failure by default*. The job runner is invoked async with retries **turned off**, because a retried job would run the student's code twice (and bill twice).
Study: [Asynchronous invocation](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async.html).

**Q37 [I] What is "reserved concurrency" versus "account concurrency"? Which does this project use?**
Account concurrency is the shared pool for all functions in the region (default 1000). Reserved concurrency carves out a guaranteed (and capped) slice for one function. This project mostly relies on its *own* in-flight counters (Q41) because per-student reserved concurrency would not fit a small account; `backend.reservedConcurrency` is 0 (off).
Study: [Lambda concurrency](https://docs.aws.amazon.com/lambda/latest/dg/lambda-concurrency.html).

**Q38 [B] Why does a Lambda handler normally return a dict, and what is the handler string `app.handler`?**
`Handler: portal.handler` means "file `portal.py`, function `handler`". Lambda calls `handler(event, context)`; for an HTTP API v2 integration the event describes the HTTP request and the return dict describes the response.
Repo: `template.yaml` `Handler:` lines.

**Q39 [I] What belongs in module scope (outside `handler`) and what does not?**
Expensive reusable things (boto3 clients, loaded settings, small caches) belong in module scope so warm invocations reuse them. Anything request-specific must stay inside the handler. Beware stale module-level caches (Q27/Q28).

**Q40 [D] How does the code make `handler(event, context=None, now=None)` testable?**
`now` is a parameter, so tests can pass a fixed time instead of mocking `time.time`. This "inject the clock" pattern is used throughout (`gateway.handler`, `status_of`, engine `ctx.now`).
Study: [Dependency injection (Fowler)](https://martinfowler.com/articles/injection.html).

## B. The in-flight limiter and the run allowance

**Q41 [I] Why does the gateway need its own in-flight limiter if Lambda already has concurrency limits?**
Because the account's Lambda concurrency pool is shared by *everything*, including the teacher's portal. One busy student could use all of it and lock the teacher out. The limiter caps requests currently running: globally (`inflight#global`, default 3 on a new account; follows the account limit when it is raised) and per student (`inflight#<slug>`, default 2).
Repo: `lambda/gateway.py`, `config/settings.json` `gateway.*`.

**Q42 [I] How does the limiter count in-flight requests atomically across many gateway containers?**
A DynamoDB item per counter with `ADD count :1` guarded by a ConditionExpression (`attribute_not_exists(count) OR count <= :max`). The condition and update are one atomic operation, so parallel callers can never overshoot the cap. On exit, `counter_sub` decrements but never below zero.
Repo: `lambda/portal.py` `DynamoStore.counter_add/counter_sub`.
Study: [DynamoDB condition expressions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.ConditionExpressions.html).

**Q43 [D] What happens if a container crashes between increment and decrement?**
The counter leaks upward. `acquire` heals it: if the count is >0 and `updated_at` is older than `gateway.leakSeconds` (60 s), the counter is reset to 0 and re-acquired. A safety valve for the lack of distributed transactions.
Repo: `lambda/gateway.py` `acquire`.

**Q44 [I] Why `try/finally` around the acquired slots?**
So slots are released on every exit path, including exceptions and early returns. Without it a bug would permanently shrink capacity.

**Q45 [I] What does `acquire_waiting` add, with its sleeping and jitter?**
Instead of failing instantly with 429 when no slot is free, the gateway can wait up to `gateway.waitMs` for a slot, sleeping a random 0.12–0.22 s between tries. Jitter prevents all waiters retrying at the same moment (a "thundering herd"). Default wait is 0 (off) until the account has enough concurrency.
Study: [Timeouts, retries and backoff with jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/).

**Q46 [I] How is the "run allowance" (e.g. 5000 runs per month) enforced without a race?**
`counter_add(f"runs#{slug}#{period_id}", 1, limit=...)` with a TTL a week after the period resets. The item is created on first use; the atomic condition stops the count at the limit. A new period means a new key, so no reset job is needed.
Repo: `lambda/gateway.py`, `lambda/backends.py` `period_info`.

**Q47 [I] Why do counter items have a `ttl` attribute?**
DynamoDB's Time To Live deletes expired items for free in the background, so per-minute and per-period counters clean themselves up. TTL deletions can lag by up to ~48 hours, which is fine because keys include the time window.
Study: [DynamoDB TTL](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html).

**Q48 [I] How is the per-IP rate limit keyed, and why hash the IP?**
`ip#<slug>#<sha1(ip)[:12]>#<minute>`; the count allowed is `gateway.perIpPerMinute` (120). Hashing avoids storing raw IPs (a privacy habit) and keeps keys short. The minute bucket (`now // 60`) is a fixed window counter, simple but allows bursts at window edges.
Study: [Rate limiting algorithms (explained)](https://blog.cloudflare.com/counting-things-a-lot-of-different-things/).

**Q49 [D] Where does the "real viewer IP" come from and why can't the student spoof it?**
A CloudFront Function copies the viewer address into `x-viewer-ip`; the gateway only trusts it after the origin-secret check passes, and it is stripped before the student sees the request. A direct call without the secret is refused, so an attacker can't inject their own `x-viewer-ip`.
Repo: `lambda/portal.py` `client_ip` (the gateway reads `x-viewer-ip` only after its own secret check).

**Q50 [I] Which limit protects *money* rather than capacity?**
The run allowance (runs per period), AI spending caps per student (micro-dollars), storage caps, knowledge monthly cap, jobs per day. The in-flight limiter protects capacity; these protect the teacher's bill.

## C. API Gateway

**Q51 [B] What is an API Gateway HTTP API and how does it differ from a REST API?**
HTTP APIs are the cheaper, faster, simpler product (JWT authorizers, payload v2, automatic CORS config). REST APIs have more features (API keys, usage plans, request validation, WAF integration). This platform uses HTTP APIs: `DeployApi`, `BackendApi`, `StorageApi`, `KnowledgeApi` and `AiProxyApi`.
Study: [Choosing between HTTP APIs and REST APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-vs-rest.html).

**Q52 [I] Why are there several API Gateways rather than one?**
Separate failure domains, separate throttles and separate permissions: the portal, student backends, storage connector and Gemini proxy have different callers and risk. Each API has its own `ThrottlingBurstLimit/RateLimit` (settings group `apiThrottles`).
Repo: `template.yaml`, `docs/SETTINGS.md` "API request throttles".

**Q53 [I] What is throttling at API Gateway vs the gateway Lambda's own 429?**
API Gateway throttles by request rate before Lambda runs (cheap protection). The Lambda's 429s are *application-level* (per visitor, per student, per allowance) and carry friendly JSON messages and `retry-after`.

**Q54 [I] What is CORS and why does this project mostly avoid it?**
Browsers block cross-origin calls unless the server says it's allowed. Sites call `/api/...` on *their own host*, so the request is same-origin and CORS never applies. The portal API (different host) is where CORS configuration matters.
Study: [MDN: CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS). Repo: `docs/AWS_LEARNINGS.md` section 3.

**Q55 [I] Why does `cache-control: no-store` appear on gateway responses?**
Per-visitor API answers must never be shared by CloudFront or the browser. The reply helper and `shape` set `no-store` by default (the student can override).
Repo: `lambda/gateway.py` `reply`, `shape`.

**Q56 [I] What does `x-content-type-options: nosniff` do?**
It stops browsers from guessing a MIME type different from what the server says, which blocks a class of attacks where an uploaded or returned file is "sniffed" as HTML/JS. Set by default on student responses.

**Q57 [D] Why are the response headers `transfer-encoding`, `connection`, `content-length`, `keep-alive` dropped?**
They are hop-by-hop or length-related headers that the front layers (API Gateway/CloudFront) compute themselves; passing a student's wrong value could produce a malformed response.

**Q58 [I] Why is there a maximum response size (1 MB default)?**
Lambda returns through API Gateway which limits payloads (10 MB for HTTP API, 6 MB for Lambda sync), and big dynamic responses are expensive. Large assets belong in static S3 files, not API responses.

## D. Student functions and their sandbox

**Q59 [I] How is a student's function created at runtime?**
The deploy Lambda (control plane) calls `lambda:CreateFunction` with the uploaded zip, the sandbox role, memory/timeout from settings and environment variables. Creation is a *state machine advanced by polling* (`none → creating → ready/failed`), one step per `GET /api/student/backend` call.
Repo: `lambda/backends.py`.

**Q60 [I] Why "one step per call" and polling instead of one long request?**
Creating a function can take longer than an API request is allowed to, and Lambda has no "wait" built into our synchronous handler. The student's CLI polls and each call performs the next small step. It's a simple alternative to Step Functions and survives browser/CLI disconnects.

**Q61 [I] How does the platform limit what the control-plane Lambda may do to student functions?**
`DeployFunction`'s IAM permissions allow managing only functions named `sd-*`, passing only the sandbox role and only to Lambda, writing only student log groups. A compromised or buggy deploy function therefore cannot touch other platform functions.
Repo: `template.yaml` `DeployFunction.Policies`, `docs/TECHNICAL_SPECIFICATION.md` 8.3.

**Q62 [I] What is a permission boundary and how does it apply here?**
A managed policy attached to a role that sets the *maximum* permissions it can ever have, regardless of other attached policies. The student sandbox role has a boundary so even if someone adds a policy later, it still can't exceed "write logs".
Study: [Permissions boundaries](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html).

**Q63 [I] How do student environment variables stay secret?**
They are write-only: `PUT /backend/env` sets values, the API only ever returns *names*. `npm run logs` masks values (min length 6) so a leaked traceback doesn't reveal them. The student's secret is stored in the Lambda's configuration, not in the repo.
Repo: `lambda/backends.py` (`env_names`), `config/settings.json` `backend.logMinSecretChars`.

**Q64 [I] How are student logs read, and why is "errors only" tricky?**
CloudWatch Logs `FilterLogEvents` with a filter pattern. Python tracebacks don't contain the word ERROR, and a Lambda timeout is logged only as `REPORT ... Status: timeout`. The pattern therefore includes `Traceback`, `Task timed out`, `Status: timeout` etc., and drops START/END/INIT noise.
Repo: `lambda/backends.py` `LOG_ERRORS_PATTERN`.
Study: [CloudWatch filter pattern syntax](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html).

**Q65 [I] Why is the log retention 7 days for student functions?**
Cost and privacy. CloudWatch Logs bills for storage; student logs may contain personal data; 7 days is enough to debug.
Repo: `config/settings.json` `backend.logRetentionDays`.

**Q66 [D] Presigned URL region/signature bug: what happened and why does it matter?**
In `us-east-1` an older boto3 default could create a SigV2 presigned URL; S3 rejected it with `SignatureDoesNotMatch` once the browser added a `Content-Type`. Fix: `Config(signature_version="s3v4", s3={"addressing_style": "virtual"})`. Lesson: real-AWS testing catches what mocks can't.
Repo: `lambda/backends.py`, `docs/AWS_LEARNINGS.md` section 2 & 14.

**Q67 [I] How does a student upload a 40 MB zip if Lambda accepts only 6 MB?**
The Lambda returns a *presigned PUT URL* (valid 10 minutes). The client uploads straight to S3 `uploads/<slug>.zip`; then calls `/publish`, and the Lambda reads the zip from S3 and unpacks it into `sites/<slug>/` (or creates the function from it).
Study: [Presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).

**Q68 [I] What protects the platform from a malicious zip (zip bomb, path traversal)?**
Limits for zipped size, unzipped size and file count (`frontend.maxZipMB 50`, `maxUnzippedMB 200`, `maxFiles 2000`), and path sanitising when extracting: `posixpath.normpath` is applied to every entry name, and an absolute path or one that starts with `../` is rejected (`lambda/app.py` around line 274).
Study: [OWASP: Path traversal](https://owasp.org/www-community/attacks/Path_Traversal) and [Zip slip](https://security.snyk.io/research/zip-slip-vulnerability).

**Q69 [I] After publish, why invalidate CloudFront for `/sites/<slug>/*`?**
Cached files (`frontend.cdnCacheSeconds` 86400) would otherwise show the old site for up to a day. Invalidation purges edge copies of that student's prefix only.

**Q70 [B] Who owns the code in `student/` vs `starter/`?**
`student/` holds the helper libraries and scripts students' projects use (class_ai, class_storage, class_knowledge, class_agent, class_quality, portal-deploy). `starter/` builds the *kit*: project template, AI-editor rules, the MCP server, and the build scripts that package them.

## Study materials for this chapter

- AWS Lambda **Operator guide / best practices**: <https://docs.aws.amazon.com/lambda/latest/operatorguide/intro.html>
- **Serverless Land** patterns and learning: <https://serverlessland.com/>
- Yan Cui, *Production-Ready Serverless* (book) and his blog <https://theburningmonk.com/>
- AWS Builders' Library: **Using load shedding to avoid overload** <https://aws.amazon.com/builders-library/using-load-shedding-to-avoid-overload/>
- YouTube (search these titles): "AWS re:Invent Serverless Lambda under the hood", "Best practices for AWS Lambda concurrency", "Advanced serverless architectural patterns".
- Hands-on in this repo: read `lambda/gateway.py` (210 lines) twice, then `tests/test_lambda.py` to see how each check is tested.
