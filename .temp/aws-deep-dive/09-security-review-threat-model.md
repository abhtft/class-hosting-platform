# 09. Security review and threat model (read it like an architect reviewing your own design)

Purpose: practise the most valuable architect skill, **finding the weak points in a design you are proud of**. This is my reading of your code and template. **None of the findings below is a proven exploit**: they are design observations, each with the evidence, an honest rating, and a fix. Where I am unsure, I say **verify**. Treat the list as a hardening backlog and as study material: for each finding, try to explain *why* it is a weakness before reading the fix.

Ratings combine **impact** and **likelihood**: High / Medium / Low. "Likelihood" means: how realistic is it that someone in your situation (a classroom platform with a small number of students) would meet it.

---

## 1. Assets, actors, entry points

**Assets** (what an attacker wants, or what you must not lose): (1) your **AWS account** and bill; (2) the **teacher login** and session-signing secret; (3) the **AI keys** (platform management key, Google key, per-student sub-keys); (4) **student data** (sites, class-storage data and files); (5) **availability** of the portal and sites; (6) your **reputation and AWS standing** (content you host).

**Actors:** anonymous internet visitor; a **malicious or careless student** (the most likely adversary: they run code on your account); a student who stole another's token; someone who learned a token from a screenshot; an attacker who obtained read access to your AWS metadata (the stack id, function environments); a supply-chain attacker (compromised release, kit or dependency).

**Entry points:** `*.<root>` (sites), `deploy.<root>` (portal pages + API), the four public `execute-api` URLs, presigned S3 URLs, the custom-domain tenants, the student's own laptop (the kit and `.env.local`), the teacher's laptop (the installer and your AWS keys).

## 2. Controls that are working (credit where due)

| Area | Control | Evidence |
|---|---|---|
| Data at rest | Private bucket, Block Public Access, OAC, bucket policy with `AWS:SourceArn` | template.yaml:164 to 245 |
| Identity | No long-lived keys on any function; per-function roles; PassRole pinned to one role; permission boundary on the sandbox | template.yaml:455 to 485, 787 to 792 |
| Auth crypto | scrypt for the password, constant-time compares, equalised timing, lockout, HMAC sessions, hashed tokens | `portal.py` |
| Isolation | Slug from session; slug-prefixed keys; `LeadingKeys` condition; per-student API keys | `app.py`, `storage.py`, `template.yaml:566` |
| Abuse limits | Gateway's nine checks; atomic counters; four separate throttles | `gateway.py` |
| Safe operations | Change-set plans, no data replacement, typed destroy, teacher login on destroy, terminal-only secrets | installer |
| Supply chain (2.0.3+) | Signed releases (Sigstore), pinned workflows, CodeQL, checkov, Scorecard, Dependabot | repo |

## 3. STRIDE by component (a checklist to run on any design)

STRIDE = **S**poofing, **T**ampering, **R**epudiation, **I**nformation disclosure, **D**enial of service, **E**levation of privilege.

| Component | S | T | R | I | D | E |
|---|---|---|---|---|---|---|
| Portal login | lockout, scrypt | HMAC sessions | **no audit trail of who did what** | uniform errors | API throttle + scrypt cost | role-bound sessions |
| Site serving | Host-based routing | OAC read-only | CloudFront logs **off** | private bucket | no WAF; Shield Standard | n/a |
| Backend gateway | origin secret | validated shaping | one log line per call | errors hidden | in-flight caps, allowances | sandbox role |
| Student function | none needed | zip limits | its own logs | no creds to read | timeouts, memory | boundary |
| Storage API | per-student key | validated names | ops counter | slug-derived keys | caps | LeadingKeys |
| Custom domains | CNAME ownership | n/a | CloudTrail | n/a | tenant quotas | **dangling-CNAME takeover (F5)** |
| Kit distribution | hash check | **writable by the portal function (F2)** | n/a | n/a | n/a | **RCE on student laptops if compromised (F2)** |

---

## 4. Findings

### F1. The kit and the portal pages are writable by the internet-facing function (cross-trust link)  **Impact: High, Likelihood: Low -> Medium**

**Evidence.** `DeployFunction` (the function that parses **untrusted uploads** and serves the **public** API) has `s3:PutObject` on `${SitesBucket.Arn}/*` (template.yaml:730 to 733), which includes the **`portal/`** prefix. The student kit (`starter-kit.zip`), the editor package, `kit-version.json` and the portal pages are served from `portal/`. Since 2.0, a student's project **automatically syncs its kit files from the portal before every deploy**, replacing `portal-deploy.mjs` and `tools/*.mjs`: **code the student's computer then runs**.

**Attack chain (if DeployFunction were ever compromised, for example by a vulnerability in zip or request handling):** overwrite `portal/starter-kit.zip` and `portal/kit-version.json` (with a matching hash and `managed` list) -> every student's next `npm run deploy` or `status` downloads, "verifies" (the hash comes from the same poisoned source) and **runs attacker code on the student's laptop**. The hash check protects against corruption in transit, **not** against a poisoned origin.

**Why it matters architecturally:** the component with the largest attack surface can write the **code distribution channel**. Auto-sync made this link live. Two independent defences are missing: *least privilege* (the function does not need to write `portal/`) and *signature verification* (the client trusts whatever the portal serves).

**Fixes (in order of value).** (1) **Privilege separation:** add an explicit `Deny` for `s3:PutObject`/`DeleteObject` on `portal/*` for `DeployFunction` (and the sweeper); let only the **teacher's `portal-publish`** (your own credentials) write there. (2) **Sign the kit** at release/publish time (for example Sigstore/minisign with a key only the teacher's publish step holds) and have `portal-deploy.mjs`/`platform.mjs` **verify the signature** before applying. (3) Separate S3 buckets for *user content* and *distribution*.

### F2. The origin secret is the stack UUID  **Impact: Medium, Likelihood: Low**

**Evidence.** `!Select [2, !Split ['/', !Ref 'AWS::StackId']]` (template.yaml:153, 396, 683) is used as `x-origin-secret` and injected into every function's environment. It is readable with `cloudformation:DescribeStacks`, in the CloudFront origin custom headers, and in function configuration. It never rotates.

**Impact if known.** The trust in `x-viewer-ip` collapses: calls made **directly** to `DeployApi`/`BackendApi` can forge any client IP, dodging the **teacher-login lockout (5 failures per IP)** and per-IP rate limits. The attack is still bounded by the API throttle (20 rps) and scrypt's CPU cost, so **a long random teacher password keeps this safe**. The gateway also stops being "CloudFront-only".

**Fix.** Generate a real random secret (`AWS::SecretsManager::Secret` with `GenerateSecretString`, referenced by `{{resolve:secretsmanager:...}}` in the CloudFront header and the Lambda environment), rotate by redeploy. Better still: make the lockout independent of IP (also lock on the *username* with a global counter), and alarm on a spike of failed logins.

### F3. Teacher hash and signing secret in SSM `String`; history keeps old hashes  **Impact: Medium, Likelihood: Low**

**Evidence.** `TeacherAccount` is `Type: String` (template.yaml:256). Anyone with `ssm:GetParameter(History)` on it can read the scrypt hash, salt and the **session-signing secret** (which lets them mint teacher sessions without the password!). Overwrites keep previous versions.

**Fix.** Create it out-of-band as a **`SecureString`** with a customer-managed KMS key whose key policy names only the portal role; limit parameter history; remember the signing secret is *as sensitive as the password*.

### F4. Student keys (`cs_`, `ga_`, OpenRouter sub-keys) stored in plaintext in DynamoDB (and PITR)  **Impact: Medium, Likelihood: Low**

**Evidence.** `storage.py:148`, `ai.py:245`: keys are generated once and stored as attributes of the token item; PITR backups copy them. Five roles can read the table.

**Fix.** Store a **hash** of `cs_`/`ga_` keys and compare the hash (constant-time) so a table read does not reveal usable keys; the key is injected into the function at creation (the plaintext lives only in the function's encrypted environment). For the OpenRouter sub-key, only the **provider-side hash** and the key injected into the function need to exist; keep nothing else. Alternatively encrypt the attribute with a KMS envelope key.

### F5. Subdomain takeover through a dangling CNAME  **Impact: Medium, Likelihood: Low -> Medium**

See `07` (Part 6). After a student removes their domain in the portal but leaves the DNS record pointing at your **shared** routing endpoint, **another** student can register that same name and the (shared-endpoint) ownership check passes. **Verify** whether CloudFront's multi-tenant ownership check requires a TXT token in your account. **Fixes:** a DNS TXT challenge tied to the student; a cool-down before a released name may be re-registered; tell teachers to ask students to delete their CNAME first.

### F6. Student sites share a registrable domain; no CSP/headers  **Impact: Medium (for visitors), Likelihood: Medium**

**Evidence.** All sites are `*.<root>`; student sites have **no response headers policy** (no CSP, no `frame-ancestors`, no HSTS from CloudFront) while the portal has the managed security headers (template.yaml:691). One student's JavaScript can set a cookie for `.<root>` that other students' (and `deploy.`) pages receive. The portal uses `sessionStorage` bearer tokens, which are origin-isolated, so the portal is not exposed to this.

**Fix.** Use a **dedicated registrable domain** for the platform (not one that hosts other cookie-bearing apps). Attach a response-headers policy to the student distribution with at least `X-Content-Type-Options: nosniff` and `Strict-Transport-Security`; consider a restrictive `Content-Security-Policy` (it may break some student apps: make it a teacher-selectable option).

### F7. Hosting arbitrary content: abuse and account standing  **Impact: Medium, Likelihood: Medium**

Students can publish phishing pages or malware under your domain, and student functions can reach the internet. AWS's **Acceptable Use Policy** applies to **your** account: an abuse report can lead to suspension. Controls today: the teacher can take a site **offline** at once; tokens expire. **Add:** a published abuse contact and takedown procedure; a "hosting terms" note for students; optionally Google Safe Browsing monitoring of your domain.

### F8. `DeployFunction` is a monolith with broad permissions  **Impact: Medium, Likelihood: Low**

One function handles login, tokens, uploads, student-function management, ACM, tenants, secrets and storage cleanup, so its role is the union (see `02`, 2.1). **Fix.** Split by duty: *portal* (tokens, sessions), *deployer* (S3 publish), *backend-manager* (`sd-*` functions + PassRole), *domains* (ACM + tenants), *ai-admin* (the management key). Each role then shrinks to a few statements, and a bug in one cannot use the powers of another. Cost: more functions and more template.

### F9. Shared sandbox role can write logs into every `sd-*` log group  **Impact: Low, Likelihood: Low**

(`02`, 2.6). One student's function can inject log lines into another's group. Not a disclosure. **Fix:** one role per student (IAM role count limit is 1,000), or accept and document.

### F10. Student code has unrestricted outbound internet  **Impact: Low-Medium, Likelihood: Medium**

No VPC: the function can call any host. Abuse: reaching third parties from your AWS addresses, small proxying, scraping. Bounded by 10 s, 256 MB, run allowances. **Fix options:** accept and document (most classroom platforms do); or put functions in a VPC with an egress-filtering firewall (cost: NAT gateway and firewall: usually not worth it here).

### F11. Presigned PUT cannot limit size before upload  **Impact: Low, Likelihood: Low**

Site and backend zips are checked **after** upload (`publish`). **Fix:** presigned **POST** with `content-length-range` (as class storage already does).

### F12. No WAF, no edge rate limit, no cost cap on CloudFront  **Impact: Medium (cost), Likelihood: Low**

Throttling happens only at API Gateway and in app counters. A flood of requests to a popular or abused **site** is served (and billed) by CloudFront; **AWS Shield Standard** covers volumetric DDoS but not a cost cap. **Fix:** an **AWS WAF** rate-based rule on the portal distribution (about a few dollars per month); a **Budget** with alerts and **Cost Anomaly Detection**; the kill switch (take the site offline).

### F13. Weak forensics: log retention and no access logs  **Impact: Low, Likelihood: n/a**

Platform function log groups are created by Lambda and **never expire**; API Gateway access logs and CloudFront standard logs are **off**; there is no teacher-action audit log (single teacher login: no *who*). **Fix:** set retention (30 to 90 days) on the platform log groups; enable access logging on `DeployApi`; add an audit item in DynamoDB for teacher actions.

### F14. `destroy` leaves resources behind  **Impact: Low (cost/hygiene), Likelihood: High**

(`08`, 1.5b.) Student `sd-*` functions and log groups, the platform functions' log groups, and the per-student **OpenRouter sub-keys** (valid until their cap) survive `destroy --everything`. The docs say the account ends empty. **Fix:** destroy lists and deletes `sd-*` functions and their log groups, and (if the management key is still readable) revokes the student sub-keys; document what is intentionally kept (the ACM certificate).

### F15. A token is the only credential for a student  **Impact: Medium, Likelihood: Medium**

Whoever holds the `mds_` token controls that student's site, backend and keys until it expires or is revoked. It lives in plaintext in `.env.local` on the student's disk and in the portal URL-free sessions. No MFA. **Mitigations that exist:** expiry, instant revoke, regeneration approval, the rules forbidding agents to open `.env.local`. **Ideas:** shorter default expiry; show the last-used time/IP on the teacher page; rotate on suspicion.

### F16. Zip handling in memory  **Impact: Low, Likelihood: Low**

`publish` reads each entry into memory (16 parallel workers) inside a 1024 MB function; limits are 50 MB zipped / 200 MB unzipped / 2,000 files. A crafted archive near the limits could make one invocation run out of memory (a failed request, not a persistent outage). **Verify** how `zipfile` handles entries whose declared size disagrees with their real size, and consider a compression-ratio check.

### F17. Fixed-window limiters and eventual-consistency reads  **Impact: Low**

The per-IP limiter resets each minute (a burst across the boundary can reach 2x); authorization reads are eventually consistent (a revoked token may work for under a second). Acceptable; know them.

### F18. Scaling cliffs, not vulnerabilities  **n/a**

`Scan` in the portal and sweeper; the single hot `inflight#global` item; Lambda quota 10 on a new account.

---

## 5. Risk matrix

```
Impact   High   |  F1 (portal/ write + auto-sync)
         Medium |  F2 origin secret   F3 SSM String   F4 plaintext student keys   F5 dangling CNAME
                |  F6 shared domain   F7 content abuse   F8 monolith   F12 cost
         Low    |  F9  F10  F11  F13  F14  F16  F17
                +---------------------------------------------------------------------
                     Low likelihood                              Higher likelihood
```

## 6. If I could fix only five things (my order)

1. **F1:** Deny `portal/*` writes to the Lambda roles, and sign the kit. (It closes the one path from the public internet to code execution on student machines.)
2. **F14:** make `destroy` clean up `sd-*` functions and revoke sub-keys. (A concrete bug that contradicts the docs.)
3. **F2 + F3:** a random origin secret and a `SecureString` teacher record. (Cheap, and they remove the "anyone with AWS read can bypass the lockout or mint sessions" cases.)
4. **F4:** hash the per-student keys.
5. **F12 + budget:** a WAF rate rule, a Budget and Cost Anomaly Detection.

## 7. Attacker walkthroughs (answer them before you check)

1. *I am a student with a valid token. How could I read another student's class-storage data?* (You cannot: your key names your slug, the data key is built from the authenticated slug, IAM limits the function to `userdata/` and the data table.)
2. *I control my own student function. How do I read the teacher password hash?* (The sandbox role has no `ssm:GetParameter`; there is no network path to SSM without credentials; the only secret you can read is your own environment.)
3. *I steal a student's token from a screenshot. What can I do, and how does the teacher stop me?* (Everything that student could, until the teacher revokes: revocation is immediate, even for sessions already issued.)
4. *I find the BackendApi URL. What now?* (Direct calls get `403 forbidden` without the origin secret; if I somehow learn it, I still face the nine checks, the throttle and the allowances.)
5. *I upload a zip containing `../../portal/index.html`.* (`site_entries` rejects `..` and absolute paths; keys are always `sites/<slug>/...`.)
6. *I register `www.victim.com` as a custom domain after the victim removed theirs but left DNS.* (See F5: this is the one I would worry about.)
7. *I compromise `DeployFunction` via a parsing bug.* (See F1 and `02`, 2.1: serious, but cannot create IAM entities or pass other roles; the kit-poisoning path is the one to close.)
8. *Count what a total compromise of one student function costs you.* (Its own allowances, its AI cap, some log noise: bounded and capped.)

> UPDATE: F1 part 1 (privilege separation) is fixed in commit after 22658b1: Deploy and Sweeper are limited to sites/, archive/, uploads/ (userdata/ delete-only). Part 2 (signing the kit) is still open.


---

## Update 2026-11-01 (2.1.0 / 2.1.1), read this before the text above

- F14 (destroy leaves student sd-* Lambda functions and log groups): FIXED. destroy finds the functions whose role is this platform sandbox role, deletes them and their log groups (--everything also the platform function log groups). Proven in tests; the live account was not destroyed.
- F1 (platform functions could write portal/): FIXED and proven on real AWS with the IAM simulator, one resource per call. Deploy: sites/ uploads/ archive/ read-write, userdata/ delete only. Sweeper: sites/ uploads/ archive/. No role can read or write portal/. No role can use the KMS signing key.
- Kit signing (KMS ECC_NIST_P256, about 1 dollar a month) is live: tampered, unsigned, older-serial and other-key kits are refused by pinned students with exit 3, and the student keeps working.
- Real-AWS findings: npm drops .gitignore from packages; a Lambda timeout is only a REPORT line with Status: timeout; a stale local install state could plan removals of a live platform (guard refused it); a site taken offline had no Bring online button. All fixed in 2.1.1.
- Still open: F2 (random origin secret), F3 (SSM String for secrets), signing the editor package and the portal pages, fresh install of 2.1.x on a clean account (blocked by CloudFront verification on the test account).


#status

Yes. F6 was the last security item I worked on, and only part of it, as I explained. In the order I worked on them:

F14, destroy leaving student functions behind. Fixed.
F1, the platform’s functions being able to write the kit and portal pages. Fixed, with signing added on top.
F1’s follow-up, the signed list of every published file and the verify audit. This covers the pages and editor packages that signing alone couldn’t reach.
F6, the student-site headers. Partly fixed.
Not touched: F2, F3, F4, F5, F7 to F13, and F15 to F17. Under your instruction to focus on features, they stay deferred. The one practical step for you from that group is an AWS Budget alert, which is the real protection for F12.

So the security phase is closed at F6. Jobs, stage 1, is next as soon as you confirm the limits.
