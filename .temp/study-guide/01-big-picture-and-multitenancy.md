# Chapter 1: The big picture, multi-tenancy and how the pieces fit

> Tags: **[B]** basics (start here) · **[I]** intermediate · **[D]** deep (come back to these later).
> "Repo" lines tell you which file to open. Answers are short on purpose: use them to check yourself, then open the file.
> Facts about limits come from `config/settings.json` (the shipped defaults). A live platform may differ because the teacher can change them.

## A. What the product is

**Q1 [B] In one paragraph, what is this platform and who are its three kinds of user?**
A teacher installs it once into their own AWS account. Students (each with a token) publish a website, and optionally a small backend, from their AI editor. Visitors then open the student's site. Teacher = owner and controller (features, limits, cost). Student = builder with a token, no AWS credentials. Visitor = anonymous person using a student's app.
Repo: `docs/STATUS.md` section 1, `docs/TECHNICAL_SPECIFICATION.md` section 1.4.

**Q2 [B] Why is it "one install per teacher, in the teacher's own AWS account" rather than one big shared SaaS?**
Ownership, cost and privacy stay with the teacher; nobody else's bill or data is mixed in; the open-source author never holds anyone's data or credentials. The cost is that every teacher must do an install and an AWS account setup.
Repo: `docs/STATUS.md` section 6 ("decided on purpose").

**Q3 [B] What is "multi-tenancy"? Which tenants does this project have?**
One running system serving many separate customers ("tenants") whose data and behaviour must stay isolated. Here, *inside one teacher's install*, each **student** is a tenant (their own site, function, storage, AI key, books). Separately, each **visitor** of a student app is a sub-user. The *teachers* are not tenants of each other, because each has a separate stack.
Study: [AWS SaaS Lens](https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/saas-lens.html).

**Q4 [I] Name the three isolation models for multi-tenant systems. Which does this project use, and where?**
(1) *Silo*: separate stack per tenant. (2) *Pool*: shared resources, isolation by keys/logic. (3) *Bridge/mixed*. This platform is a **mixed model**: pooled for the CDN, bucket, DynamoDB tables and gateway (isolation by key prefix and checks), but *siloed* for compute: every student backend is its own Lambda function `sd-<slug>`. Across teachers it is fully siloed (separate AWS account/stack).
Study: [SaaS tenant isolation strategies (AWS whitepaper)](https://docs.aws.amazon.com/whitepapers/latest/saas-architecture-fundamentals/tenant-isolation.html).

**Q5 [B] What are the "three paths" and why does keeping them apart matter?**
(1) **Pages**: browser → CloudFront → S3 (no code runs). (2) **Student code**: CloudFront → API Gateway → gateway Lambda → the student's Lambda. (3) **Control**: portal → API Gateway → deploy Lambda (creates/changes things). Separate paths = separate permissions and failure domains. Student code can never get the control path's powers.
Repo: `docs/AWS_LEARNINGS.md` section 0, `docs/TECHNICAL_SPECIFICATION.md` section 2.2.

**Q6 [I] What is the difference between a *control plane* and a *data plane*? Which Lambda is which?**
Control plane = managing resources (create a student, a function, switch features). Data plane = serving the real traffic. `DeployFunction` (`lambda/app.py`, `portal.py`) is control plane. `GatewayFunction` (`lambda/gateway.py`) and the student functions are data plane. They are separate so that a flood of visitor traffic cannot starve the teacher's portal and vice versa.
Study: [Control planes vs data planes (AWS Builders' Library)](https://aws.amazon.com/builders-library/avoiding-fallback-in-distributed-systems/) and the AWS whitepaper [Static stability using availability zones](https://aws.amazon.com/builders-library/static-stability-using-availability-zones/) for the vocabulary.

**Q7 [B] Why does a visitor's page request never run any Lambda?**
Static files are served from S3 via CloudFront (cached at the edge). Only requests starting with `/api/` go to compute. This is the cheapest and fastest way to serve a website and the reason a class can host many sites for a few dollars.
Repo: `template.yaml` (`Distribution`, `RouterFunction`).

**Q8 [B] What does `template.yaml` contain, in one sentence?**
Every AWS resource of the platform (CloudFront, S3, API Gateway, Lambda, DynamoDB, KMS, alarms, optional EC2) as a CloudFormation/SAM description, so the whole platform can be created, diffed and deleted as a unit.
Repo: `template.yaml` (about 100 resources).

**Q9 [I] Which AWS resource types appear most in the template and what does each do?**
`AWS::Serverless::Function` (Lambda), `AWS::Serverless::HttpApi` (HTTP API Gateway), `AWS::CloudWatch::Alarm` (alerts), `AWS::DynamoDB::Table` (3 tables), `AWS::CloudFront::Function` (edge routing), `AWS::CloudFront::Distribution` (2), `AWS::S3::Bucket`, `AWS::KMS::Key` (kit signing), `AWS::SSM::Parameter`, `AWS::SNS::Topic` (alerts), `AWS::EC2::Instance` (optional worker).
Repo: `grep "Type: AWS::" template.yaml`.

**Q10 [I] Why is the application code plain Python with *no third-party dependencies*?**
Fewer things to package, patch and audit; Lambda starts faster; no build step is needed (`cloudformation package` just zips the folder). The one exception, `pypdf`, is vendored in `lambda/vendor/` with a hash manifest and a test.
Repo: `lambda/vendor/pypdf.manifest.json`, `tests/test_knowledge_ingest.py`.

## B. The request journey

**Q11 [B] Follow a visitor opening `alice.example.edu/cart`. What happens?**
DNS wildcard → CloudFront. A CloudFront Function (`RouterFunction`) reads the host header, extracts `alice`, validates the name with a regex, and rewrites the URI to `/sites/alice/cart` (or `/index.html` if no file extension: single-page-app fallback). CloudFront fetches that key from the private S3 bucket using Origin Access Control, caches it, and returns it.
Repo: `template.yaml` `RouterFunction`.

**Q12 [I] Follow an `/api/hello` call from alice's page.**
CloudFront has a second behaviour for `/api/*` (cache disabled). A `BackendRouteFunction` rewrites host to `/b/alice/hello` and sets the real viewer IP header; CloudFront adds `x-origin-secret` and forwards to the BackendApi HTTP API → `GatewayFunction`. The gateway runs ordered checks, then invokes Lambda `sd-alice`, shapes the response and returns it.
Repo: `lambda/gateway.py` header comment.

**Q13 [I] List the gateway checks in order and say why the order matters.**
(1) origin secret, (2) path/slug regex, (3) body size, (4) backend exists and token lifecycle OK, (5) teacher's on/off switch, (6) per-visitor-IP rate limit, (7) in-flight (concurrency) slots, (8) run allowance (atomic counter), (9) invoke. Cheap checks come first so abusers are rejected before they consume anything costly; the allowance counter is incremented only after the slots are held so a "busy" rejection never wastes a run.
Repo: `lambda/gateway.py` `handler`.

**Q14 [I] Why does the gateway compare the origin secret with `hmac.compare_digest`?**
A normal `==` can return as soon as one byte differs, leaking how many leading bytes were right through timing. `compare_digest` takes constant time. It is cheap protection for any secret comparison.
Study: [A Lesson in Timing Attacks](https://codahale.com/a-lesson-in-timing-attacks/).

**Q15 [I] The API Gateway URL is public on the internet. How does the gateway know a request came through CloudFront?**
CloudFront injects a secret header (`x-origin-secret`) that only the platform knows; the gateway Lambda refuses requests without the right value. It also strips `x-origin-secret` and `x-viewer-ip` before passing the request to student code so students never see them.
Repo: `lambda/gateway.py` `STRIP_REQUEST_HEADERS`.

**Q16 [I] Why does the gateway rebuild the event for the student's function instead of forwarding the original?**
To give student code a *standard, predictable* shape (API Gateway HTTP API payload v2) regardless of the path through the platform, to hide internal headers, and to rewrite the path (`/b/alice/hello` → `/hello`). The student can therefore also develop locally with the same event shape.
Repo: `lambda/gateway.py` `student_event`.

**Q17 [I] What is "payload format 2.0" and why does `shape()` imitate API Gateway's behaviour?**
Lambda integrations with HTTP APIs use a JSON event (v2) and return `{statusCode, headers, body, isBase64Encoded, cookies}`; API Gateway also accepts a bare value and wraps it with a 200. The gateway Lambda invokes the student function directly (not through API Gateway), so it re-implements that return-value convention.
Study: [HTTP API Lambda integration payload format](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html).

**Q18 [I] Which student-function errors become which HTTP errors?**
Throttled invoke → 429 `busy`; function missing → 404 `no_backend`; function error with "timed out" → 504 `function_timeout`; other function error → 502 `function_error` (the message is *not* leaked because it may contain student secrets); invalid return → 502 `bad_response`; response too large → 502 `response_too_large`.
Repo: `lambda/gateway.py` `invoke`, `shape`; `docs/TECHNICAL_SPECIFICATION.md` Appendix A.

**Q19 [D] Why are errors like "function error" logged without the message?**
Student exceptions can include secrets (e.g. an env value in a traceback). Platform logs are the teacher's, not the student's, so the details stay only in the student's own function log group.

**Q20 [B] Where does the student's code run and what can it reach?**
In its own Lambda `sd-<slug>` with an IAM role (`StudentSandboxRole`) that can only write logs, under a permission boundary. It has no AWS credentials worth having. Internet egress is open (accepted for a class). Optional connectors (AI, storage, books) are reached over HTTPS with scoped keys, not AWS permissions.
Repo: `docs/TECHNICAL_SPECIFICATION.md` section 8.2.

## C. State, naming and lifecycle

**Q21 [B] How does a site name (slug) get reserved, and why a lock item?**
A `slug#<slug>` item is written with `if_absent=True` (a DynamoDB conditional put). Two students racing for the same name: exactly one write succeeds. This is the standard way to get uniqueness in a key-value store with no unique index.
Repo: `lambda/portal.py` `DynamoStore.put`.

**Q22 [B] What are the token lifecycle states?**
active → "expired" (read-only for a grace period of days) → "offline" (archived, restorable) ; or "revoked". The teacher can extend, take offline, bring online, approve regeneration, or revoke.
Repo: `lambda/portal.py` `status_of`, `GRACE_DAYS`.

**Q23 [I] What does the daily Sweeper do and why scheduled instead of event-driven?**
Archives expired sites, finishes domain removals, syncs AI keys with token status, plus other housekeeping. Scheduled sweeping is simple and idempotent; expiry has no natural event in AWS, so a clock is the trigger.
Repo: `lambda/app.py` `sweep`; `template.yaml` `SweeperFunctionDaily`.

**Q24 [I] Why are "archived" sites never auto-deleted?**
A policy decision: deleting a student's work automatically is irreversible and a teacher can almost always recover it by extending the token; storage is cheap. S3 versioning adds a second undo layer.

**Q25 [B] Why can't a student pick the slug `admin`, `api` or `www`?**
They are in `RESERVED` (also `sites`, `uploads`, `archive`, `deploy`...). Those names would collide with real hostnames, S3 prefixes or routes. Slugs are also limited to `[a-z0-9-]` and 30 characters, which makes them safe in DNS names, S3 keys and Lambda function names.
Repo: `lambda/portal.py` `RESERVED`, `SLUG_RE`.

**Q26 [I] What is "idempotent" and where does this project rely on it?**
Doing something twice has the same effect as once. Examples: `enable_app` in `auth.py` ("asking again changes nothing"), the installer's resumable steps, `importData` ("never deletes, safe to repeat"), content-addressed file uploads in project sync.
Study: [Stripe: Designing robust APIs with idempotency](https://stripe.com/blog/idempotency).

**Q27 [D] What is "eventual consistency" and where could it bite here?**
A read right after a write may not see the write (S3 is now strongly consistent for objects; CloudFront caches; teacher-login config is cached 60 s in the Lambda; gateway settings are cached 10 s). Visible effects: a new teacher login "takes up to a minute", a brand-new backend may show "no backend" for a couple of seconds (hence the shorter *negative* cache).
Repo: `lambda/portal.py` `teacher_config`, `lambda/gateway.py` `load_token`.

**Q28 [I] Why does the gateway cache the token record in a module-level dict?**
Lambda reuses the same container for many invocations; module globals survive between them. Caching for `gateway.cacheSeconds` (10 s) saves two DynamoDB reads per request. Trade-off: a revoked token may still be served for up to ~10 s per warm container.
Study: [Lambda execution environment lifecycle](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html).

**Q29 [I] What is the "negative cache" and what bug did it prevent?**
A site *without* a ready backend is cached for only 2 s, not 10. Without that, a student who had just finished deploying was told "no backend" for ~10 s afterward. Found in the first live run, not in tests.

**Q30 [B] What did "single DynamoDB table with string key `pk`" mean for the data design?**
All record kinds live in one table, distinguished by a key prefix: `tok#<sha256>` (token), `slug#<slug>`, `ip#...` (rate counters), `inflight#...`, `runs#...`, `authpool#platform`, `worker#...`, and others. It's a deliberately simple key-value use of DynamoDB. See Chapter 4 for the trade-offs.

## Study materials for this chapter

- AWS Well-Architected **SaaS Lens**: <https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/saas-lens.html>
- AWS Builders' Library, **Fairness in multi-tenant systems**: <https://aws.amazon.com/builders-library/fairness-in-multi-tenant-systems/>
- Martin Fowler, **Microservices** (to see what this project is *not*): <https://martinfowler.com/articles/microservices.html>
- Book: *Designing Data-Intensive Applications*, Martin Kleppmann (chapters 1 and 11 first).
- In this repo, in this order: `docs/STATUS.md` → `docs/AWS_LEARNINGS.md` section 0 and 13 → `docs/TECHNICAL_SPECIFICATION.md` sections 1–3.
