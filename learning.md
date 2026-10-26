# Learnings: student hosting portal and custom domains

> **Historical record.** This document describes the design plan as it was when it was written. For current behaviour, limits and configuration see [docs/TECHNICAL_SPECIFICATION.md](docs/TECHNICAL_SPECIFICATION.md), the source of truth. Statements about "a limit of 10" concurrent Lambda executions describe the situation then: the limit is now 1,000 (granted 2026-10-22).

A record of what this project taught us, written so someone new (or you in six months) can understand not only *what* was built but *why*, what went wrong, and what to do differently. Everything here comes from the real work on `student-deploy`; nothing is theoretical. It contains no passwords or tokens.

Contents
1. [What was built](#1-what-was-built)
2. [Architecture and why it looks this way](#2-architecture-and-why-it-looks-this-way)
3. [Design decisions, and what we rejected](#3-design-decisions-and-what-we-rejected)
4. [AWS and CloudFront lessons](#4-aws-and-cloudfront-lessons)
5. [Security lessons](#5-security-lessons)
6. [Frontend lessons](#6-frontend-lessons)
7. [Testing lessons](#7-testing-lessons)
8. [Mistakes and recoveries](#8-mistakes-and-recoveries)
9. [Operating it](#9-operating-it)
10. [Process lessons](#10-process-lessons)
11. [Known limits and next steps](#11-known-limits-and-next-steps)
12. [Quick reference](#12-quick-reference)

---

## 1. What was built

Starting point: a CLI-only system. A teacher ran `admin.mjs` to hand out permanent deploy keys; a student ran `node deploy.mjs`, which uploaded a zip through a small API into a private S3 bucket served by CloudFront at `name.example.edu`.

What it became, in four layers added one after another:

| Layer | What it adds |
|---|---|
| **Token backend** | Expiring tokens in DynamoDB, a teacher login, a student login (the token *is* the login), a regeneration request flow (request, approve with a new duration, deny), and a daily sweeper that takes expired sites offline |
| **Web portal** at `deploy.example.edu` | A teacher page (generate, extend, revoke, take offline, approve requests) and a student page (expiry bar, folder or zip upload with a pre-flight checklist) |
| **Hosting** | A second CloudFront distribution for the portal, clean URLs, security headers, an `/api/*` route to the same Lambda |
| **Custom domains** | A per-student teacher switch; students bring their own subdomain, add one CNAME, and get automatic HTTPS (CloudFront multi-tenant "distribution tenants") |

The old CLI keys still work in parallel; nothing was broken to add the portal.

---

## 2. Architecture and why it looks this way

```
Browser ── deploy.example.edu ── CloudFront (PortalDistribution)
              ├─ /*      → S3  portal/            static pages (web/)
              └─ /api/*  → API Gateway → Lambda ──┬─ DynamoDB  tokens, slug locks, domain locks, login failures
                                                  ├─ S3        uploads/, sites/<slug>/, archive/<slug>/
                                                  └─ CloudFront  cache invalidation, tenant API

name.example.edu ── CloudFront (Distribution) → S3 sites/<name>/        (router function picks the folder)
www.theirdomain.com    ── CloudFront multi-tenant → S3 sites/<student>/       (tenant parameter picks the folder)

Daily 03:00 UTC: Sweeper Lambda → archive expired sites, finish stuck domain removals
```

Principles that shaped it:

- **One bucket, folders as the boundary.** `sites/<slug>/` is public through CloudFront only; `uploads/` is temporary; `archive/` and `portal/` are separate. A student's session decides the folder, so a student can only ever write to their own.
- **Private bucket + origin access control.** Nothing in S3 is public. CloudFront reads it with OAC and the bucket policy names exactly which distributions may read which prefix.
- **Static front end, thin Lambda back end.** The portal is plain HTML/JS with no build step, so it can be edited and published with one command (`node admin.mjs portal-publish`) and has nothing to install or break.
- **Status is derived from time, not stored.** A token is `active`, `expired` (read-only grace), or `offline` purely from `expires_at`, the grace period and a couple of flags. That removed a whole class of "state got out of sync" bugs and meant the 3-day rule needed no cron job to take effect (the sweeper only moves *files*).
- **Cheap by design.** Pay-per-request DynamoDB, Lambda, CloudFront: a class costs roughly nothing until used heavily.

---

## 3. Design decisions, and what we rejected

### Tokens: login *is* the token
The student has no account. They paste a token; the server hashes it (SHA-256) and looks it up. Benefits: nothing to remember, nothing to reset, a leaked database can't deploy sites (only hashes are stored). Trade-off: a lost token can't be recovered, only replaced. We accepted that and said so in the docs.

### Two different session keys
Teacher and student sessions are signed with different keys derived from one secret (`HMAC(secret, "teacher-session")` vs `"student-session"`). A student session therefore can never pass an admin check, even though both are the same format. Verified by tests that try each session on the other role's routes.

### Bearer header, not cookies
Cookies would have needed CSRF protection and same-site care. A bearer token in `sessionStorage` sent as a header avoids that class of problem entirely and behaves identically on the `cloudfront.net` address and the real domain. The cost (a session lost on tab close) is fine for this use.

### Expiry semantics: active → expired (3 days, read-only) → offline
Decided with the owner: after expiry the student can still *sign in* (so they can ask for regeneration) but cannot upload; after the grace period the site is moved to `archive/` (never deleted). Approving a request or extending the token restores it. "No deletion, the teacher visits archives from time to time" was an explicit requirement.

### Regeneration: new duration chosen by the teacher
On approval the teacher picks the duration (default: the original), counted from *now*. Denial carries an optional reason shown to the student. A student may ask again after a denial.

### Slug precedence: teacher-set → from student name → random
Slugs are unique via a lock item (`slug#<name>`) written with a conditional put, which is atomic. Reserved names (`deploy`, `www`, `api`, `admin`, `bs`, `lenden`, …) are refused. Names already used by CLI students are refused too, because both systems share `sites/<name>/`.

### Custom domains: three options were weighed
| Option | Verdict |
|---|---|
| Student's registrar forwards to `name.example.edu` | Zero work, but the address bar shows our name. Kept as the fallback to mention to students |
| One CloudFront distribution per domain, created at runtime | Rejected: wide IAM permissions, slow, leftover resources outside CloudFormation, hard to test |
| **CloudFront multi-tenant (distribution tenants)** | Chosen: one shared distribution, one tenant per domain, certificate issued and managed by CloudFront over HTTP, no per-domain distributions to clean up |

A request for a student to "paste the Elastic IP" was corrected: CloudFront has no fixed IP and Elastic IPs belong to EC2. The right primitive is a **CNAME** to a CloudFront name, plus a certificate.

### The teacher switch is enforced on the server
The student page shows "Owner not enabled for you…" when the switch is off, but the real check is in the Lambda (`403`). UI-only checks are decoration; the server is the rule.

### What a live custom domain does when the switch is turned off later
Nothing: an existing domain keeps working; only *new* setup is blocked. This avoids a teacher accidentally breaking a student's live site by toggling a permission.

---

## 4. AWS and CloudFront lessons

### 4.1 A wildcard DNS record blocks a more specific CloudFront alias
The first production deploy **failed and rolled back**: CloudFront refused to attach `deploy.example.edu` to the new portal distribution because the existing `*.example.edu` CNAME already made that name resolve to the *student* distribution. CloudFront refuses an alias whose DNS points at a different distribution.

Fix: a **two-phase rollout**.
1. Deploy the portal distribution *without* the alias (`PortalAlias=false`).
2. Add the DNS record `deploy → <portal distribution domain>` (an explicit record always beats `*`).
3. Redeploy with `PortalAlias=true`.

Takeaway: when you add a name that falls under an existing wildcard, plan the DNS record *before* the alias, and make the alias a switchable parameter so the stack can be created first.

### 4.2 Browsers need CORS to PUT directly to S3
The student page uploads the zip straight to a presigned S3 URL. S3 must allow the page's **origin**. The bucket rule allows only `https://deploy.example.edu`, so uploads from the plain `cloudfront.net` address failed ("The upload failed. Check your connection…") even though everything before that step worked. Diagnosis was a one-line preflight check:

```
curl -X OPTIONS <bucket-url> -H "Origin: <page origin>" -H "Access-Control-Request-Method: PUT"
```
200 with `Access-Control-Allow-Origin` means allowed; 403 means the origin is not on the list. Lesson: test uploads from the *real* origin, and make CORS errors easy to recognise.

### 4.3 Behind CloudFront the Lambda sees CloudFront's IP, not the user's
The teacher login lockout is per IP. Through CloudFront every request would look like it came from the edge. Solution:
- A CloudFront function on `/api/*` writes the real viewer IP (`event.viewer.ip`) into `x-viewer-ip`, overwriting anything the client sent.
- CloudFront adds a secret **origin custom header** (`x-origin-secret`) to API calls.
- The Lambda trusts `x-viewer-ip` **only if** the secret header matches.

Without the secret check, anyone calling the API directly could forge the header to dodge the lockout. Lesson: a header is only trustworthy if something the attacker can't set vouches for it.

### 4.4 Origin request policies: forward Authorization, not Host
For an API Gateway origin you must **not** forward the `Host` header (API Gateway would reject the mismatched name) but you **must** forward `Authorization`. The managed policy `AllViewerExceptHostHeader` does exactly that; the "AllViewer…" variants forward Host and break. Pair it with `CachingDisabled` for the API behavior. We confirmed the managed policy IDs with read-only CLI calls rather than trusting memory.

### 4.5 Static files need explicit content types on Windows
`aws s3 cp` guesses MIME types from the Windows registry, which can label `.js` as `text/plain` and make modules fail to load. `portal-publish` sets `--content-type` explicitly per extension. The same applies to anything published from Windows.

### 4.6 Cache invalidation is per distribution, and tenants are separate
Objects are cached for up to a year (`s-maxage=31536000`) and cleared on publish by invalidation. The handoff for custom domains did **not** mention that a tenant has its *own* cache. Without a second call (`create_invalidation_for_distribution_tenant`), a student's redeploy would never appear on their custom domain. In production the fix made a redeploy visible on the custom domain within seconds. Lesson: every cache layer needs its own invalidation; when you add a CDN surface, ask "who clears *this* cache?"

### 4.7 Multi-tenant distribution specifics (verified by a spike and live)
- `create-distribution-tenant` **fails until the CNAME already points at the routing endpoint** ("Could not verify Domain Name ownership"). So the order is: student adds CNAME, *then* the tenant is created. Our code treats that specific error as "waiting for DNS" and retries on each poll; any *other* refusal is shown to the user.
- A managed certificate is created as *pending*; once issued it must be **applied** to the tenant with `update-distribution-tenant --customizations Certificate={Arn=...}`. Only then does the domain become `active`.
- Deleting a tenant: **disable first, wait until `Deployed`, then delete.**
- The bucket policy's `SourceArn` for tenant traffic: allowing `distribution/<template id>` and `distribution-tenant/*` made the previously-403 test tenant serve 200.
- Apex domains can't hold a CNAME at most DNS providers; we require a subdomain (`www.`).
- Cloudflare records for CloudFront must be **DNS only** (grey cloud).
- Pricing was **not verified** (a community report said the first 10 tenants are free). Treat as unknown and check the billing console.

### 4.8 Does the Lambda runtime's boto3 know about brand-new APIs?
A real risk with a new AWS feature: the Lambda's bundled SDK might predate it. We guarded for it in code (a clear error rather than a crash) and then **proved it live** with a harmless call (a made-up domain that CloudFront rejects as "not verified", which shows the API call itself worked and IAM allowed it). Always verify new-API access in the real runtime, not just on your laptop.

### 4.9 Review CloudFormation changesets row by row
We deployed with `--no-execute-changeset`, read every row, and only then executed. Examples of what a careful read caught or explained:
- `Distribution` showing as *Modify* purely because it references the bucket's domain attribute (value unchanged).
- A `Replacement: Conditional` on the sweeper's invoke permission that was only a reference to the rule's ARN, with the rule itself not replaced.
- Confirming there were **no Remove or Replace** rows, and that `StudentKeys` / `TeacherAccount` were untouched (updating those resets them to `{}`).

### 4.10 Things created outside the template are a liability
The multi-tenant distribution, its SPA-fallback function and the routing endpoint were created by hand with the CLI. They work, but `sam delete` won't remove them and `sam deploy` won't recreate them. They are now documented (IDs and config files) and referenced by two parameters. Preferred long-term direction: bring them into the template once CloudFormation support is comfortable.

---

## 5. Security lessons

- **Store hashes, not secrets.** Tokens: SHA-256 only. Teacher password: salted scrypt (`N=16384, r=8, p=1`); the Node `admin.mjs` and the Python Lambda produce identical hashes, which was verified directly.
- **Rate-limit the one thing that can be guessed.** Five wrong teacher passwords from one address lock it for 15 minutes. Tokens are 128-bit random, so guessing them isn't feasible and needs no lockout.
- **Always do the expensive hash, even for a wrong username**, so response time doesn't reveal whether the username exists.
- **Constant-time comparisons** (`hmac.compare_digest`) for every secret comparison.
- **Give the server, not the UI, the last word.** Domain switch, token status, role separation: all enforced in Lambda.
- **Revocation must kill live sessions.** Student routes re-load the token on every request, so a revoked token's existing session dies immediately (tested).
- **Least privilege, and widen deliberately.** The Lambda started able to touch only the bucket, invalidations and its table. Custom domains added tenant actions scoped to the template distribution and `distribution-tenant/*`, plus a few ACM actions. Each widening was a conscious, reviewed line.
- **Text is never HTML.** All dynamic content is inserted with DOM text APIs through a small `h()` helper; nothing is built from strings. This is why the student note or a student name can't inject script.
- **Don't put secrets in chat or files.** The teacher credential was handled through a hashed SSM value; the live test used temporary DynamoDB rows that were deleted afterwards, rather than reading a stored password file.

---

## 6. Frontend lessons

- **No build step is a feature.** Plain ES modules (`common.js`, `check.js`, `zip.js`) are served as-is. Fewer moving parts, trivial to publish.
- **A tiny `h()` helper beats string templates** for safety and readability.
- **`replaceChildren(null)` writes the text "null".** A real bug found by looking at the screen. Fixed with a `fill()` helper that drops null/false children.
- **Don't rebuild a card that the user is typing into.** The student page tracks a "signature" of what each card shows and only rebuilds when it changes, so the minute-by-minute refresh doesn't wipe a half-typed domain or the upload result.
- **Folder upload realities.**
  - `<input webkitdirectory>` gives `webkitRelativePath`; drag-and-drop gives `FileSystemEntry` objects that must be read *synchronously inside the drop event* and traversed asynchronously afterwards, in batches.
  - `node_modules` and `.git` are never read (skipped with a marker) so dropping a whole project is fast.
  - The checker auto-detects `dist/`, `build/` or `out/` inside a dropped project, and rejects a source folder (an `index.html` that loads `/src/…` is not a build).
- **Zipping in the browser without a library.** A small writer using `CompressionStream('deflate-raw')` with a store-fallback, CRC-32, and UTF-8 file names. It is validated by feeding its output to the *real Lambda zip validator*, so the client and server can't silently disagree. Performance check: 1,805 files / 25 MB zipped in about 2.7 s.
- **Fail early with a clear message.** Warn when the raw size is already over the zip limit (images barely compress), instead of letting the student wait through zipping to be refused.
- **Polling state machines in the UI.** The custom-domain card advances one server step per poll (8 s), stops at `live`, and slows to 15 s on errors. A subtle point: when the state changes the card is rebuilt and schedules its own next poll; when it doesn't, the poller must schedule it, otherwise you get duplicate timers or none.
- **Accessibility basics paid off:** `role="alert"` for errors, `aria-label`s on bars, dialogs via `<dialog>`, focus-visible outlines, light and dark themes from design tokens, and a layout that works at phone width without horizontal scroll.

---

## 7. Testing lessons

### 7.1 Build a fake AWS, and run the real code against it
All Lambda logic runs in tests against in-memory stand-ins: `MemoryStore` (DynamoDB), `FakeS3`, `FakeCloudFront`, and `FakeTenantCloudFront` (the tenant API). The same fakes power a **local dev server** (`tests/dev_server.py`) that serves the real pages and real Lambda code, so the entire product can be clicked through with no AWS account.

### 7.2 Make the fake *programmable*
`FakeTenantCloudFront` exposes flags (`dns_ok`, `cert_status`, `deployed`, `refuse_with`). Tests flip them to walk the state machine: waiting_dns → issuing_certificate → finishing → live; removal while still deploying; refusal; etc. The dev server subclasses it with **timers** so a human can *watch* the stepper advance in about 20 seconds.

### 7.3 Test the clock, not the calendar
Everything time-related takes `now` as a parameter, so "expires in 3 days", "grace ends", "offline" are plain function calls in tests with no sleeping.

### 7.4 Browser testing finds what unit tests can't
Bugs that only the browser (or a realistic run) exposed:
- **A real logic bug:** after the teacher took a site offline, the student could *upload again and bring it back online*. Unit tests passed because nothing asserted it; a hard manual round did. Fix: an archived token counts as `offline` everywhere (uploads blocked, regeneration allowed). Lesson: assert *invariants* ("an offline site can't be re-published by the student"), not only happy paths.
- The `"null"` text rendering bug.
- The dev server treating every `PUT` as a fake S3 upload, which swallowed the API's `PUT /api/student/domain`. A tooling bug, found because the UI showed an odd error.
- The CORS failure above.

### 7.5 Know what local tests cannot prove, and then prove it live
Local testing cannot show: CloudFront header forwarding, real S3 CORS, IAM sufficiency, the Lambda's SDK version, certificate issuance, or edge cache behavior. We closed each gap with a targeted live check:
1. A harmless real API call to prove SDK + IAM (made-up domain, expected "waiting for DNS").
2. A full end-to-end run against production with a **temporary test student** created directly in DynamoDB and removed afterwards, against a real domain: claim, certificate, HTTPS 200, redeploy visible in ~6 s, removal, cleanup. Then a scan proved nothing was left behind.

### 7.6 Pitfalls hit while writing tests
- `crypto.getRandomValues` accepts at most 64 KB per call: fill big buffers in chunks.
- Test ordering mistakes (a session that had already expired, "deployed" flipped after the delete instead of before) look like product bugs; read the assertion before the code.
- Assertions on exact dict equality (`{"archived": [...], "failed": []}`) break when a new key is added; fine, but update them deliberately.
- Python `-I` (isolated mode) hides user-installed packages such as `boto3`; don't use it where imports are needed.

---

## 8. Mistakes and recoveries

| What happened | Cause | Recovery / lesson |
|---|---|---|
| **First production deploy rolled back** | Wildcard DNS made CloudFront refuse the portal alias | CloudFormation restored production automatically; made the alias a switchable parameter and deployed in two phases |
| **Student upload failed on `cloudfront.net`** | CORS allowed only the real domain | Confirmed with a preflight `curl`; finished DNS + alias so uploads ran on the real origin |
| **A script deleted part of `tests/run.mjs`** | A search for a line matched an *earlier* occurrence, so a "replace everything between" removed too much | Caught immediately; restored the file from `git show HEAD:` and re-applied only the additions. Lesson: when scripted-editing, anchor on something unique, and diff afterwards |
| **Backslashes mangled in heredocs** | Escape sequences (`\n`, `\r`) passed through shell heredocs changed meaning | Write patch scripts to files with the file tool, then run them. Also keeps risky text out of shell parsing |
| **Shell heredoc parse failures** | Quotes inside the heredoc body | Same fix: files, not heredocs, for anything non-trivial |
| **The offline-bypass bug** | `status_of` ignored the archived flag | Treat `archived` as `offline`; added tests |
| **The handoff said another copy of the project once deployed a different template and deleted the portal, tokens table and sweeper** | Deploying from the wrong folder | Everything was restored, and the lesson was written into the handoff: deploy only from this folder, always with a reviewed changeset |
| **Background deploy "stopped by the user"** | The local client was killed while CloudFormation kept working | A CloudFormation update continues server-side; check the real stack status instead of trusting the local process |

---

## 9. Operating it

### Everyday tasks
| Task | How |
|---|---|
| Create, extend, revoke, take offline, approve | The teacher page |
| Allow a custom domain for a student | **Domain: on** on their row |
| Change pages | Edit `web/`, then `node admin.mjs portal-publish` (also clears the cache) |
| Change the teacher password | `node admin.mjs teacher-setup <username>` (writes a salted hash to SSM) |
| Look at archived (offline) sites | `aws s3 ls s3://<bucket>/archive/`; they are never deleted automatically |
| Watch the daily sweep | `sam logs --stack-name student-hosting --name SweeperFunction` |

### The safe deploy routine
```
node tests/run.mjs                                   # all green first
sam deploy --no-execute-changeset --no-confirm-changeset
aws cloudformation describe-change-set ...           # read every row; no surprise Remove/Replace
aws cloudformation execute-change-set ...
# then verify: pages 200, API 401 without a session, existing sites unaffected
```
Deploy **only from this folder**.

### Things that must never be edited casually
`StudentKeys` and `TeacherAccount` in `template.yaml`: a CloudFormation update to either resets it to `{}`.

### Debug pointers
| Symptom | First check |
|---|---|
| Portal says teacher account not set up | The SSM parameter value is still `{}` |
| Upload fails with a network error | CORS: is the page on the exact allowed origin? Preflight with `curl` |
| Old content after a deploy | Which cache? Main distribution, portal distribution, or the tenant; each has its own invalidation |
| Custom domain stuck "waiting for DNS" | `nslookup -type=CNAME <domain>` must show the routing endpoint; Cloudflare must be DNS only |
| Domain stuck "removing" | The tenant is still deploying; it finishes on the next poll or daily sweep |

---

## 10. Process lessons

- **Plan first, then execute in small, testable steps.** The work was delivered in eight steps (backend core, deploy via token, regeneration, sweeper, teacher UI, student UI, hosting, live checks), each ending with a green test run. When something broke, the blast radius was one step.
- **Ask only the questions whose answers change the work.** Early questions (expiry meaning, slug precedence, regeneration duration, notifications) were turned into defaults with one-line overrides, so the owner could say "go with defaults".
- **Verify, don't assume.** Managed policy IDs were read from AWS rather than remembered; the real runtime was probed rather than assumed; a claimed cause (CORS) was confirmed with a one-line request before proposing a fix.
- **Read-only first, then change.** Pre-flight checks (stack state, that hand-made resources exist, SDK operation names) all came before any write.
- **Confirm before destructive steps.** Deleting the hand-made test tenant was put to the owner as a choice, with an alternative that avoided deleting it. Test data in production was created with a clear name and removed in a `finally` block, and then a scan proved it was gone.
- **Read a handoff critically.** The custom-domain handoff was valuable but incomplete (no cache invalidation, an IAM guess, a template reference to a draft-only table). Treat reference code as a starting point and test the seams.
- **Say what you did and what you did not verify.** Pricing, native OS drag-and-drop, and CSP were explicitly marked "not verified/not done" rather than implied to be fine.
- **Honest correction beats quiet fixes.** When a script damaged a file, it was reported and restored from git rather than patched around.
- **Explain surprises in plain language.** "Your wildcard DNS already points that name at the other distribution" turned a scary rollback into a one-sentence cause and a clear next step.

---

## 11. Known limits and next steps

**Known limits (by design or by time)**
- One teacher account. Several teachers would need a list in the SSM entry plus `teacher-setup` / `teacher-remove` commands (designed, not built).
- One custom domain per student, subdomains only, active token required to start.
- No teacher-side "remove the student's domain" button; a teacher can switch the permission off, take the site offline, or revoke.
- The custom-domain AWS pieces live outside CloudFormation (section 4.10).
- Custom-domain **pricing is unverified**.
- The native OS folder picker and real drag-and-drop were simulated with the same `File` objects, not driven by a human in automation. A quick manual try is still worthwhile after UI changes.

**Reasonable next steps**
1. Add a Content-Security-Policy header to the portal (script-src `'self'`, connect-src for the bucket host). Hold until it can be tested against real S3, since a wrong policy breaks uploads.
2. Multi-teacher accounts, with every teacher seeing all students.
3. Move the multi-tenant distribution, function and routing endpoint into the template.
4. Check tenant pricing and add a visible count of active custom domains for the teacher.
5. A teacher action to remove a student's custom domain.
6. Commit the work on a feature branch and tag the first release.

---

## 12. Quick reference

**Repo map**
| Path | Purpose |
|---|---|
| `template.yaml` | All AWS resources (bucket, two distributions, router and portal functions, API, Lambdas, DynamoDB, schedule) |
| `lambda/app.py` | Entry point: CLI deploy routes, portal deploy, invalidation, sweeper entry |
| `lambda/portal.py` | Tokens, logins, sessions, regeneration, sweep logic, store abstraction |
| `lambda/domains.py` | Custom-domain state machine, removal, uniqueness, teacher switch |
| `web/` | The portal pages and shared modules (`common.js`, `check.js`, `zip.js`, `shared.css`) |
| `admin.mjs` | `add`, `remove`, `list`, `teacher-setup`, `portal-publish` |
| `tests/` | Fakes, unit tests, end-to-end tests, and `dev_server.py` for local click-through |
| `custom-domain/` | The handoff, reference code, and config of the hand-made tenant resources |
| `implement.md`, `README.md` | Setup steps (Parts 1 to 3), operations, troubleshooting |

**Custom-domain state machine**
```
(none) ─PUT─▶ waiting_dns ─CNAME visible─▶ issuing_certificate ─cert issued, applied─▶ finishing ─deployed─▶ live
   ▲                                                                                                   │
   └──────────── removed ◀── removing (tenant disabled, then deleted once deployed) ◀──── DELETE ──────┘
```

**Token life**
```
active ──(expires_at)──▶ expired (read-only, 3 days) ──▶ offline (files moved to archive/)
   ▲                                │
   └──── approve / extend ◀─────────┘   (restores archived files, clears caches)
```

**One-line lessons to remember**
1. A wildcard DNS record can block a more specific CloudFront alias: make the alias switchable.
2. Browser uploads to S3 need CORS for the exact origin.
3. Behind CloudFront, trust a viewer-IP header only when a secret origin header vouches for it.
4. Every cache layer needs its own invalidation; tenants have their own.
5. Derive status from time; store as little state as possible.
6. The server enforces; the UI explains.
7. Fake the cloud, drive the fake with flags and timers, then prove the seams live.
8. Test invariants, not just happy paths: the offline bypass hid behind green tests.
9. Review every changeset row, and deploy only from the right folder.
10. Anchor scripted edits on unique text, and keep risky text out of shell heredocs.


---

## 13. Student backends (local build, 2026-10-20)

What the work taught us, written before the first live run. Items marked *to verify live* are assumptions that only AWS can confirm.

**Design**
- **Check the account before designing the safety net.** One read-only call (`aws lambda get-account-settings`) showed the Lambda limit is **10 concurrent executions**, shared with other production apps. That killed the textbook defence (reserved concurrency per student) and forced a different one: an in-flight limiter in the gateway. Always read the real quotas first.
- **A gateway beats per-student routes.** One catch-all route plus one gateway Lambda gives unlimited students and a single place for status checks, limits, counting, error mapping and the kill switch. The cost is one extra hop and two Lambda slots per request, which the limiter accounts for (3 requests in flight means at most 6 slots).
- **Make the cost control the teacher's, and exact.** A teacher-set run allowance is a much tighter ceiling than any concurrency bound (about $0.17 per student per month at the default, versus about $17), and it is simple to explain: "5,000 runs a month".
- **One toggle that means stop.** A backend executes code, so OFF must stop serving (unlike a custom domain, which keeps working when its switch is turned off). The toggle replaced a separate kill switch.
- **Order of checks matters.** Cheap rejections first (secret, path, size, lifecycle, toggle, per-IP, in-flight cap), the atomic run counter last, and the slot released in a `finally`. Only a run that reaches the function is counted; a request rejected earlier never costs the student anything.

**Counters**
- **One conditional atomic update gives "no overshoot".** `ADD count 1` with the condition `attribute_not_exists(count) OR count <= limit - 1` can never exceed the limit, even with parallel callers (a 60-thread test proves it). Do not read-then-write.
- **Limit 0 needs its own case.** With `attribute_not_exists` in the condition, a limit of 0 would let the first call through. Handle `limit <= 0` before the update.
- **Counters leak.** A crash between increment and decrement leaves an in-flight count stuck above zero. Store `updated_at` and let the next request reset a counter that is above zero but untouched for longer than the longest possible request.
- **Never treat a negative age as fresh.** A cache check written as `now - loaded < ttl` accepts entries from the future; use `0 <= age < ttl`.

**Security**
- **Failure details are secrets too.** A student's exception message can contain their own secrets. The visitor gets a generic error, and the platform's own logs do not record the message either (the student can read it in their function's logs).
- **Env values are write-only.** Stored only in the function's configuration, never in DynamoDB, never returned by any API. The test asserts the value appears nowhere in any response.
- **Scope IAM by name and guard it with a test.** The Deploy Lambda may manage only `function:sd-*`, may pass only the sandbox role and only to Lambda, and may never create roles. `tests/test_template_backend.py` asserts this against the template text, and mutation testing (widening each rule on purpose) confirmed the test catches it.

**Testing**
- **Run the student's real code in the fake.** `FakeLambda` executes uploaded Python and Node handlers, so the contract (event in, response out) is tested for real, including env vars and captured logs. The starter templates are deployed through the real gateway in the tests, so they cannot drift from the contract.
- **A weak assertion is worse than none.** I wrote a few `... or True` lines while moving fast; they tested nothing and would have kept passing forever. Review for them before committing.
- **Polling needs a re-schedule path.** The same bug appeared twice (custom domains, then backends): when a poll returns an unchanged state, the card is not rebuilt, so nothing schedules the next poll. The poller itself must re-arm when state is unchanged and the card did not rebuild.

**Tooling trap**
- **An unfocused browser pane never fires `<dialog>` close events.** Even a bare native dialog failed (`document.hasFocus()` was false), which looked like a bug in the app for half an hour. Diagnosis in three steps: bisect the fields, test a minimal native dialog, check focus. After fronting the tab everything worked. When a UI test "fails" in an embedded browser, test the simplest possible case before debugging your code.

**What the first live run found (2026-10-21)** — everything the fakes could not know. 48 live checks now pass.
- **Presigned URLs from a default boto3 S3 client are SigV2 in us-east-1.** S3 then rejects the upload with `SignatureDoesNotMatch` the moment a Content-Type header is added (every browser does). The site uploader already configured SigV4 + virtual addressing; the new backend code copied the call but not the client configuration. Fakes cannot show this. Fix: configure the client, and test the *real* client's settings offline (the test now fails if the fix is removed).
- **`cfn-lint` does not catch every circular dependency.** CloudFormation refused the changeset: `Distribution -> BackendApi -> GatewayFunction -> Distribution`, closed by the shared `Globals` environment (every function referenced the distribution). Creating a changeset costs nothing and found it; lint did not. Fix: override those two variables for the one function that does not need them, plus a template test.
- **Cache negatives for much less than positives.** A 10 s cache made a freshly deployed backend look missing for 10 s. Now 2 s for sites without a ready backend, 10 s for ready ones.
- **AWS facts confirmed:** reserved concurrency 0 is accepted at an account limit of 10 (so the OFF belt-and-braces works); a new Python/Node function becomes Active in about 3 to 4 seconds; 8 parallel requests against a 3-request cap gave exactly 3 successes and 5 `busy`, and the slots were free again afterwards.
- **A live test with a throwaway fixture is cheap and thorough.** The student, function, log group and counters were created by script and removed in a `finally`; a final scan proved nothing was left behind. Run the whole thing again after every fix: the Node failure only appeared after the first fixes unblocked the later steps.

**A student's first real use found a gap the tests could not (2026-10-21).** A student opened their *custom domain* and every Analyze/Submit got `403`. Lessons:
- **Every distribution that can serve a site needs every route.** The `/api/*` route was added to the distribution behind `<slug>.example.edu` but not to the separate multi-tenant distribution behind custom domains. It was on the plan as "Phase 2, pending", and the pending item became a user-visible failure within a day. When a feature has a "later" for one surface, say so loudly where users will meet it, or finish it first.
- **Who answered is the diagnosis.** The platform's gateway always answers in JSON. An **HTML** 403 with `X-Cache: Error from cloudfront` means CloudFront itself refused the request (here: POST on a GET/HEAD-only behavior) and the backend was never reached. Reading the response headers in DevTools took one look; start there.
- **Reproduce before fixing.** Three read-only calls confirmed it: the custom domain failed, the same student's normal address worked, and the distribution config showed one origin and no `/api/*` behavior.
- **Multi-tenant distributions are stricter.** `update-distribution` rejected `SmoothStreaming` (not allowed on multi-tenant) and wanted `FieldLevelEncryptionId`; the fix was found one error at a time, with nothing changed by the failed calls.
- **Dry run by default, apply on request.** The change script prints the planned change and only acts with `--apply`, saves the current configuration first, never prints or stores the secret, and is idempotent. The safety check that blocked an inline "patch and apply" in one command was right: splitting the edit from the apply, showing the plan, and asking made the production change reviewable.
- **Guard the documented config.** `tests/test_tenant_config.py` asserts the `/api/*` behavior, POST allowed, caching disabled, and no real secret (mutation-tested). Config that lives outside CloudFormation needs tests even more, because nothing else will notice drift.

## 14. Class AI connector (2026-10-22)

- **Measure the model before you design around it.** The free Gemma model answered HTTP 429 (rate-limited upstream, a pool shared with everyone on OpenRouter) in 3 of 3 calls, so the free tier cannot back a class. The paid Haiku 5.5 answered in 1.3 to 2.1 s at under $0.0001 per short answer. A $0 model also cannot be limited by a dollar cap, which is what per-student keys use.
- **Per-student keys beat a proxy here.** A proxy holding one key would need our own metering and costs three concurrent Lambdas per AI call at an account limit of 10 (gateway, student function, proxy). Capped sub-keys need no extra Lambda and the provider enforces the cap. The price: the cap is in dollars and the provider's usage counting lags by seconds, so a cap can be overshot slightly (7 small calls passed a cap worth about 3 in the live spike).
- **Spike the provider API before building.** A 10-minute spike with a $0.00002 key showed what the docs did not: `expires_at` cannot be patched (so we never set it and disable keys instead), a normal key gets "Only management keys can perform this operation", a capped key answers 403 "Key limit exceeded", a disabled key answers 401, and responses may start with whitespace (parse with `strip()`).
- **Never hand a student the platform's key.** Their code can read its own environment. Give each student their own capped, revocable key, and disable it first when AI goes off.
- **DynamoDB has no floats.** Store money as whole micro-dollars. The in-memory test store accepts floats, so the AI tests use a strict store that fails on any float, which is how this class of bug is caught before production.
- **Order operations so the safe one cannot be skipped.** Switch-off disables the key before touching the function; switch-on stores the new key before updating the function, so a failure in between is retried with the same key and never creates a second one.
- **Provider outages must not block the teacher.** Revoke and extend call a best-effort sync; a failure is logged and the daily sweeper retries.
- **Scope the secret.** The management key lives in Secrets Manager, readable only by the Deploy and Sweeper functions through one named-secret statement that a test pins (and a test fails if any `sk-or-v1-` key is committed).
- **A long wait needs the whole chain to agree.** AI answers take seconds, so the student function timeout (25 s) must stay below the gateway's (29 s), which must stay below the HTTP API's limit (30 s). A template test pins all three.
- **Quote what you saw.** The usage the portal reads from OpenRouter took up to about 25 s to show a first non-zero number in the live run, so the pages say "up to half a minute behind" instead of promising real time.
