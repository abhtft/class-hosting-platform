# 10. Labs, verification checklist and self-test

Everything here is a way to turn *reading* into *knowing*. Do the labs in order on your **test** platform (`teacher-test`). Each lab says **what you will prove**, the commands, the **expected result**, and the **why**.

Companion: `verify-platform.sh` (same folder): a **read-only** script that runs about twenty of these checks at once and prints PASS/FAIL. It is untested against a live stack: read it before you run it.

Set up your shell once (see `00`):

```bash
export AWS_PROFILE=teacher-test AWS_REGION=us-east-1 STACK=student-hosting
aws sts get-caller-identity --query Account --output text      # confirm it is the TEST account
```

---

## The labs

### Lab 1. Inventory and identity (30 min) `[READ]`

**Prove:** you can name every resource and say whether the stack owns it.

1. `aws cloudformation list-stack-resources --stack-name $STACK --query 'StackResourceSummaries[].[LogicalResourceId,ResourceType]' --output table`.
2. Tick every row against the table in `00`, section 4.1.
3. Now find the things **outside** the stack: `aws lambda list-functions --query "Functions[?starts_with(FunctionName,'sd-')].FunctionName"`, `aws secretsmanager list-secrets --query 'SecretList[].Name'`, `aws acm list-certificates`, `aws s3 ls` (the artifacts bucket), `aws cloudfront list-distributions --query 'DistributionList.Items[].[Id,Comment]'` (you should see **three**: students, portal, and the multi-tenant one).

**Why:** the stack boundary decides what `destroy` and drift detection can see.

### Lab 2. Prove the IAM claims with the simulator (60 min) `[READ]`

Do all eight simulations in `02`, Part 4.2 and record each decision (`allowed` / `implicitDeny`). Then write, in your own words, one sentence per result explaining **which statement or condition** produced it. If you cannot, re-read `02` section 1.4 (evaluation logic).

**Stretch:** run `aws iam get-account-authorization-details --filter Role` and find the `PermissionsBoundary` of `StudentSandboxRole` in the JSON.

### Lab 3. The bucket is private and zoned (20 min) `[READ]`

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name $STACK --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
aws s3api get-public-access-block --bucket $BUCKET                                 # 4 x true
curl -s -o /dev/null -w "direct S3: %{http_code}\n" "https://$BUCKET.s3.us-east-1.amazonaws.com/sites/<slug>/index.html"       # 403
curl -s -o /dev/null -w "via CloudFront: %{http_code}\n" https://<slug>.<your-domain>/                                          # 200
curl -s -o /dev/null -w "uploads via CF: %{http_code}\n" --path-as-is "https://<slug>.<your-domain>/../uploads/<slug>.zip"       # not 200
```

**Why:** three controls stacked (BPA, bucket policy with `SourceArn`, prefix scoping). You should be able to say which control produced each 403.

### Lab 4. Test the router function with the built-in tester (30 min) `[READ]`

Follow `03`, section 4.2. Try five events and predict the output before running each: `/cart`, `/assets/app.js`, `/`, host `Bad_Name.<root>`, host `a.b.<root>`. (Expected: `/sites/<s>/index.html`, `/sites/<s>/assets/app.js`, `/sites/<s>/index.html`, 404, 404.)

### Lab 5. Cache behaviour and invalidation (30 min)

```bash
curl -sI https://<slug>.<your-domain>/assets/<file>.js | grep -iE "x-cache|^age|cache-control"      # 1st: Miss or Hit
curl -sI https://<slug>.<your-domain>/assets/<file>.js | grep -iE "x-cache|^age"                    # 2nd: Hit from cloudfront, age > 0
```

Then redeploy the student's site (`npm run deploy`) and repeat: the first request after publish is a **Miss** (the invalidation removed it). In the console, **CloudFront -> your distribution -> Invalidations** shows the two paths per publish. **Why:** s-maxage one year plus invalidate-on-publish.

### Lab 6. Why the gateway says 403 (20 min)

Do experiments E1 and E2 from `04` (direct call with and without the origin secret). Write down: *what does it prove about the secret's role, and why is it not authentication?*

### Lab 7. Throttling: which layer answered? (20 min)

Run E3 from `04`. Separate the responses into API Gateway's `{"message":"Too Many Requests"}` and the gateway Lambda's JSON with an `error` key. Then repeat against the **portal** (`/api/admin/tokens`) and observe a different throttle (20/50).

### Lab 8. The sandbox probe (60 min) **[CHANGES THINGS: test platform, throwaway student]**

Run experiment E1 in `06` (the probe backend). Capture the JSON it returns. **Expected:** STS works, S3/DynamoDB/SSM/Secrets/Lambda/IAM all denied, outbound internet works. Then delete the throwaway backend. **Why:** this is the single most convincing proof that untrusted code cannot reach your account.

### Lab 9. Watch an atomic counter (30 min) **[CHANGES THINGS: test platform]**

1. Set a throwaway student's `runs_limit` to 3.
2. Call `/api/health` 5 times (experiment E3 in `06`): `200 200 200 429 429`.
3. Look at the counter: `aws dynamodb scan --table-name $T --filter-expression "begins_with(pk, :p)" --expression-attribute-values '{":p":{"S":"runs#"}}'`. The `count` is exactly 3 (the 4th and 5th did not increment: the conditional write failed).
4. CloudWatch -> Metrics -> DynamoDB -> `ConditionalCheckFailedRequests` shows 2.

**Why:** a conditional `ADD` cannot overshoot.

### Lab 10. Sessions: tamper and cross-role (20 min)

Do E1 and E2 in `05`. **Expected:** a tampered session -> 401; a student session on a teacher route -> 401.

### Lab 11. The token lifecycle (30 min) **[CHANGES THINGS: test platform]**

1. Create a token (1 day). Sign in as that student; note it works.
2. **Revoke** it on the teacher page; reload the student page: the existing session fails immediately.
3. Create another token; use the teacher page to **extend** it and observe `expires_at` in the table (projection command in `05`).
4. (Optional) Change `OfflineGraceDays` is a stack parameter: read it in CloudFormation **Parameters**; it is what `GRACE_DAYS` reads.

**Why:** the state is computed, never stored; revocation is immediate because every request re-reads the item.

### Lab 12. Read a change set like a reviewer (45 min) `[READ]`

Do experiment X1 in `08`. Before running `describe-change-set`, **write down** what you predict for `AiProxyApi`, `AiProxyFunction`, `DeployFunction`. Then compare. **Do not apply** the plan unless you intend to switch Gemini off.

### Lab 13. Alarms and the email path (20 min) **[CHANGES THINGS: sends you an email]**

Do X2 in `08`. If no email arrives, fix the SNS subscription (confirm it), then retry. **Why:** an unconfirmed subscription is the commonest silent failure of alerting.

### Lab 14. What does `destroy` leave behind? (2 h, test platform only) **[CHANGES THINGS: destroys the test platform]**

Do this only when you are ready to rebuild the test platform. Before destroying, have at least one throwaway student with a deployed **backend**.

1. Record the baseline: `aws lambda list-functions --query "Functions[].FunctionName"` and `aws logs describe-log-groups --query "logGroups[].logGroupName"`.
2. `npx -y "$PKG" export ~/lab14-backup --profile teacher-test` (a real backup first).
3. `npx -y "$PKG" destroy --everything --profile teacher-test`.
4. After it finishes, list what is left: 

```bash
aws cloudformation describe-stacks --stack-name $STACK                                   # gone
aws s3 ls                                                                                 # gone (incl. artifacts)
aws dynamodb list-tables                                                                  # gone
aws secretsmanager list-secrets --query 'SecretList[].Name'                               # gone
aws lambda list-functions --query "Functions[?starts_with(FunctionName,'sd-')].FunctionName"    # ??? 
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/ --query 'logGroups[].[logGroupName,retentionInDays]' --output table   # ???
aws acm list-certificates --query 'CertificateSummaryList[].DomainName'                  # kept on purpose (the certificate)
aws cloudfront list-distributions --query 'DistributionList.Items[].Id'                  # empty (tenants removed first)
```

**Expected (from reading the code):** the `sd-*` functions and the `/aws/lambda/...` log groups are **still there**, the certificate is kept, everything else is gone. Compare with the README sentence "destroy leaves the account empty". Write down the differences: this is your own bug report (finding F14 in `09`). Clean the leftovers by hand (`aws lambda delete-function --function-name sd-<slug>`, `aws logs delete-log-group --log-group-name ...`), then reinstall with `install` and restore with `import`.

---

## Verification checklist: "is it configured the way I designed it?"

Tick each box after you have **seen the evidence** (a console screen or a command output).

### Storage and edge
- [ ] S3 Block Public Access: all four settings **On** (`get-public-access-block`)
- [ ] Bucket policy has **three** statements, each with `AWS:SourceArn`, resources limited to `sites/*` or `portal/*`
- [ ] Versioning **Enabled**; lifecycle: uploads 1 day, noncurrent versions 30 days, delete markers cleaned
- [ ] Default encryption present (SSE-S3 or better)
- [ ] CORS: rule 1 (`PUT`), rule 2 (`POST`,`GET`) and **no other**
- [ ] Both distributions: viewer protocol redirect/https-only; TLS policy `TLSv1.2_2021` (if aliases); `PriceClass_200`; origins use **OAC**
- [ ] `/api/*` behaviors use **CachingDisabled** and the **AllViewerExceptHostHeader** policy
- [ ] Functions associated: router (student default behavior), backend-route (`/api/*`), portal (both portal behaviors)
- [ ] Direct S3 access returns 403; site through CloudFront returns 200

### Compute and API
- [ ] Four HTTP APIs, **no CORS configuration**, throttles 20/50, 10/20, 20/40, 10/20
- [ ] Function settings: arm64, python3.12, timeouts 29/29/10/29/300, memory 1024/256/256/256/512
- [ ] Lambda **resource policies** allow only API Gateway from the matching API
- [ ] Account concurrency known (`get-account-settings`); alarm threshold consistent with it
- [ ] Student functions `sd-*`: role `StudentSandboxRole`, tags present, log retention 1 week

### Identity
- [ ] Five execution roles + sandbox role; trust only `lambda.amazonaws.com`
- [ ] Sandbox role has `PermissionsBoundary` = `StudentSandboxBoundary`; simulator says logs only
- [ ] `iam:PassRole` allowed only for the sandbox role with `iam:PassedToService`
- [ ] Access Analyzer: no external-access findings
- [ ] No IAM users/keys other than yours; your own key rotated and MFA on

### Data and secrets
- [ ] TokensTable: on-demand, TTL `ttl` enabled, PITR enabled; StudentDataTable `Retain` (if storage on)
- [ ] SSM `TeacherAccount`/`StudentKeys` exist; you know they are `String` (and decided whether to accept it)
- [ ] Secret `openrouter` exists, default or customer key, **not** readable by `sd-*` role (simulator)
- [ ] Teacher password is long and random; lockout tested once (Lab/E3 in `05`)

### Operations and cost
- [ ] 7 alarms exist; SNS subscription **Confirmed**; one test alarm emailed you
- [ ] Log retention set on platform log groups (or consciously left)
- [ ] A **Budget** and **Cost Anomaly Detection** exist (neither is in the template)
- [ ] Lambda quota request filed if the class is larger than ~15 students
- [ ] You have done one `export` and one restore (`install` + `import`) end to end

### DNS and TLS
- [ ] ACM wildcard certificate Issued, Renewal eligible, validation CNAME present and **DNS only**
- [ ] `*.<root>` and `deploy.<root>` records **DNS only**; both resolve to the right distributions
- [ ] A test custom domain walked through all states and removed again

---

## Self-test (answer without looking; then check the file named in brackets)

1. What are the five prefixes in the bucket and which two can CloudFront read? [03]
2. Why does a missing site return 403 and not 404? [03]
3. What does `AWS:SourceArn` prevent? [02]
4. State the IAM evaluation order in four steps. [02]
5. How do a role's policy and its permission boundary combine? [02]
6. Why is `iam:PassRole` dangerous, and how is it constrained here? [02]
7. Name the three layers of identity in the platform. [02]
8. Why does the bucket have CORS but the APIs do not? [03, 04]
9. Does CORS protect against a malicious script? Why not? [03]
10. What is a preflight request and which methods/headers trigger it? [03, 04]
11. What makes `style.css?v=2` not bust the cache? [03]
12. How does one distribution serve every student? [03]
13. What does the router function do about `a.b.<root>`? [03]
14. Why use a presigned PUT, and what are its two limits? [03]
15. Why is HTTP API cheaper, and what do you give up? [04]
16. Explain a token bucket with the numbers for `BackendApi`. [04]
17. Why four APIs and not one? [04]
18. List the gateway's nine checks in order. [04]
19. Why can the run allowance never be exceeded under concurrency? [04]
20. What does the student function never see in its request headers? [04]
21. Why is reserved concurrency impossible on a quota of 10? [04, 08]
22. Why is the origin secret weak, and what does it still do? [04, 09]
23. Why scrypt for the password but plain SHA-256 for the student token? [05]
24. What does an HMAC session prove and not prove? [05]
25. How can revocation be immediate without a revocation list? [05]
26. Which secrets sit in plaintext in DynamoDB, and why? [05]
27. Why is TTL not used for correctness? [05]
28. Why is `StudentDataTable` `Retain`? [05]
29. Name three isolation mechanisms in class storage. [06]
30. How does the OpenRouter delegation limit the blast radius? [06]
31. Why does Gemini need a proxy? [06]
32. What can a hostile student function still do? [06]
33. Why must every DNS record be "DNS only" on Cloudflare? [07]
34. What breaks in a year if you delete the ACM validation record? [07]
35. What is a distribution tenant and what does `{{student}}` do? [07]
36. Difference between `ROLLBACK_COMPLETE` and `UPDATE_ROLLBACK_COMPLETE`? [08]
37. What does `plan` map to in CloudFormation? [08]
38. What does `destroy` not delete? [08]
39. Name two controls that bound the bill and two gaps. [08]
40. Which finding would you fix first and why? [09]

### Answer key (short)

1. `sites/`, `portal/`, `uploads/`, `archive/`, `userdata/`; CloudFront reads `sites/` and `portal/`.
2. CloudFront lacks `s3:ListBucket`, so S3 hides which keys exist.
3. Another customer's CloudFront reading your bucket (confused deputy).
4. Default deny; explicit deny wins; guard rails (SCP/boundary/session) must allow; need an allow in identity or resource policy.
5. Intersection: the effective permission is what both allow.
6. Lets a caller attach a powerful role to code they control; allowed only for the sandbox role, only to Lambda.
7. AWS IAM, application sessions, per-student API keys.
8. Browser cross-origin PUT to S3 vs same-origin `/api` through one CloudFront hostname.
9. No: it is browser-only; scripts/curl ignore it.
10. The browser's `OPTIONS` check before a non-simple request (PUT, custom/Authorization headers, JSON content type).
11. Cache key is the path only; freshness is by invalidation.
12. Host-header routing in a viewer-request function plus a wildcard alias/cert.
13. The name regex rejects dots: 404.
14. Browsers/CLI upload straight to S3 without Lambda byte limits; cannot cap size before upload; expires with signing credentials.
15. Lower price/latency; no WAF, no resource policy, fewer features.
16. Bucket of 20 tokens refilled 10 per second; empty bucket gives 429 from API Gateway.
17. Bulkheads: separate throttles.
18. Origin secret; path; body size; backend/lifecycle; toggle; per-IP rate; in-flight; run allowance; invoke.
19. One atomic conditional `ADD`.
20. `x-origin-secret` and `x-viewer-ip` (stripped).
21. AWS requires 100 unreserved; so no reservation: use an in-flight limiter.
22. Stack UUID: readable, static; it only gates trust in `x-viewer-ip` and the gateway, not authentication.
23. Human password vs 128-bit random.
24. Integrity/origin of the payload; not secrecy, not revocation.
25. Each request re-reads the token item.
26. Per-student keys (`cs_`, `ga_`, OpenRouter sub-key): the platform must inject and compare them.
27. Deletion lags; correctness comes from the key including the period.
28. Student data is irreplaceable.
29. Authenticated slug key; slug-derived data keys; IAM `LeadingKeys`/prefix conditions.
30. A capped, revocable, per-student key; the management key never leaves three roles.
31. Google has no per-user keys.
32. Outbound internet, burn its own caps, write log lines to other `sd-*` groups.
33. The proxy hides the CNAME that ACM/CloudFront look for.
34. The certificate stops renewing and expires.
35. A per-domain instance of the multi-tenant template; substitutes the student into origin paths so it reads only its own folder.
36. First create failed (destroy it) vs an update rolled back (platform still works).
37. `deploy --no-execute-changeset` (a change set); `apply` = `execute-change-set`.
38. `sd-*` functions and log groups (and any log groups), the ACM certificate, and the OpenRouter sub-keys.
39. Run allowances, AI caps, storage caps, throttles; gaps: no budget, no WAF, unbounded CloudFront traffic, no anomaly detection.
40. F1 (the portal function can write the code distribution channel that students auto-run).

---

## Mastery exercises (do these when the labs are done)

1. **Draw from memory** (paper, no notes): the whole architecture (`01`, section 1), then the two request flows C and D with every identity and limit.
2. **Explain to a colleague** in five minutes: "why can't a student read the bucket, another student's data, or my AWS account?".
3. **Write a threat model** (assets, actors, entry points, STRIDE table) for a *new* feature, for example "students can add a contact form that sends email via Amazon SES". Decide the role, the cap, the isolation key and the kill switch **before** you write code (the six rules in `06`, Part 1).
4. **Review a diff:** take any pull request that changes `template.yaml` and list, before approving, every IAM statement it adds, every new public endpoint, and every new thing that costs money.
5. **Teach it:** write a one-page "operations runbook" for a teacher: what each alarm means, who to call, and the first three commands to run.

---

## Cheat sheet: numbers to know cold

| Thing | Value |
|---|---|
| Region | `us-east-1` (CloudFront certificates) |
| Teacher / student session | 8 h / 12 h |
| Login lockout | 5 failures, 15 min, per IP |
| Token | `mds_` + 128-bit random, SHA-256 stored |
| Presigned URL lifetime | 600 s (zips), 300 s (storage files) |
| Site limits | 50 MB zip, 200 MB unzipped, 2,000 files |
| Backend limits | 5 MB zip, 50 MB unzipped, 500 files, 256 MB, 10 s (25 s with AI) |
| Gateway limits | 1 MB in / 1 MB out, 120 req/min/IP, in-flight 3 global / 2 per student, 5,000 runs/month |
| API throttles (rate/burst) | Deploy 20/50, Backend 10/20, Storage 20/40, AiProxy 10/20 |
| Timeouts | API 30 s hard; functions 29 s; student 10 s |
| Token grace | 3 days read-only, then offline (archived, never deleted) |
| Version retention | 30 days (noncurrent), uploads 1 day |
| Student function logs | 7 days |
| Default AI cap | $0.25 per month per student |
| Storage caps | 5 MB data, 50 MB files, 64 KB per value, 10,000 ops/day |
| Alarms | 7 |
| CloudFront | 3 distributions (students, portal, multi-tenant), 3+2 CloudFront functions |
| Lambda concurrency | quota 10 on new accounts; alarm at 8; gateway in-flight 3 |


---

## Update 2026-11-01 (2.1.0 / 2.1.1), read this before the text above

- F14 (destroy leaves student sd-* Lambda functions and log groups): FIXED. destroy finds the functions whose role is this platform sandbox role, deletes them and their log groups (--everything also the platform function log groups). Proven in tests; the live account was not destroyed.
- F1 (platform functions could write portal/): FIXED and proven on real AWS with the IAM simulator, one resource per call. Deploy: sites/ uploads/ archive/ read-write, userdata/ delete only. Sweeper: sites/ uploads/ archive/. No role can read or write portal/. No role can use the KMS signing key.
- Kit signing (KMS ECC_NIST_P256, about 1 dollar a month) is live: tampered, unsigned, older-serial and other-key kits are refused by pinned students with exit 3, and the student keeps working.
- Real-AWS findings: npm drops .gitignore from packages; a Lambda timeout is only a REPORT line with Status: timeout; a stale local install state could plan removals of a live platform (guard refused it); a site taken offline had no Bring online button. All fixed in 2.1.1.
- Still open: F2 (random origin secret), F3 (SSM String for secrets), signing the editor package and the portal pages, fresh install of 2.1.x on a clean account (blocked by CloudFront verification on the test account).
