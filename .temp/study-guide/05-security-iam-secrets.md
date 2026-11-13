# Chapter 5: Security thinking, IAM, secrets and the threat model

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> Core idea of the whole project: **assume the student's code is hostile or buggy, and make sure it can still only hurt that student.**

## A. Security mindset

**Q136 [B] What is a "threat model" and what is this platform's one-sentence model?**
A list of who might attack what, and how. Here: students run arbitrary code in the teacher's AWS account, visitors send arbitrary requests to student apps, and tokens/keys may leak. The design goal is containment: no student can read another's data, spend more than their caps, or reach the teacher's control plane.
Study: [OWASP Threat Modeling](https://owasp.org/www-community/Threat_Modeling), [Adam Shostack: Threat Modeling](https://shostack.org/books/threat-modeling-book).

**Q137 [I] What is "defense in depth" and give three layers here for protecting the student kit.**
Multiple independent controls. For the kit: (1) IAM — no platform function can write `portal/`, (2) KMS signature on `kit-version.json`, (3) the student client verifies the signature and refuses a lower serial. Any one failing doesn't give an attacker code execution on students' computers.
Repo: `.temp/design-kit-signing.md`, `tests/test_signing.mjs`.

**Q138 [I] What is "least privilege" and where is it most visible?**
Every role gets only the actions on only the resources it needs. Visible in `DeployFunctionRole` (manage `sd-*` only, pass-role to sandbox only), `GatewayFunctionRole` (invoke `sd-*` only, no S3), `StorageFunctionRole` (DynamoDB `LeadingKeys` conditions, `userdata/*` only), `StudentSandboxRole` (logs only), and the worker control function (start/stop *one* instance ARN).
Study: [IAM best practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)

**Q139 [I] What is the "confused deputy" problem and is there an example here?**
A privileged service is tricked into acting on someone else's behalf. The storage API is a deputy with access to everyone's data; to prevent misuse it works out the owner from the caller's key and *builds every database/S3 key itself*; the caller never names a path. The CloudFront bucket policy uses `AWS:SourceArn` for the same reason.
Study: [The confused deputy problem](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)

**Q140 [I] What is IDOR and how does the design avoid it?**
Insecure Direct Object Reference: a user supplies an id and the server trusts it. Here the student's identity comes from the signed session or the key, never from a path or body; a student asking for another's trace simply "isn't found" because lookups use the signed-in slug.
Study: [OWASP IDOR](https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html)

**Q141 [I] What does "fail closed" mean and where is it used?**
When a check can't be completed, deny. Examples: a missing/invalid origin secret → 403; Google not configured → 503 with no resources created; an unsigned kit manifest after a pin exists → refused; unknown slug → 404.

**Q142 [B] Why does `verify_session` return the same error for a bad signature, a malformed payload and the wrong role?**
Error messages must not help an attacker learn which part was wrong. All return 401 "Please sign in again" (only a genuine expiry says "expired").
Repo: `lambda/portal.py` `verify_session`.

## B. IAM in depth

**Q143 [B] What are an IAM user, role, policy and principal?**
User = long-lived identity (an access key). Role = temporary identity assumed by a service (Lambda) or person. Policy = JSON allow/deny statements. Principal = who/what is making the request. Lambda functions use execution roles; people use users/roles.
Study: [IAM introduction](https://docs.aws.amazon.com/IAM/latest/UserGuide/introduction.html)

**Q144 [I] How does AWS decide Allow vs Deny (the evaluation order)?**
Default deny → explicit Deny always wins → otherwise Allow if an identity policy (and, if present, a boundary/SCP/session policy) allows it. Resource policies (bucket policy) can allow cross-principal. A permission boundary *intersects* with identity policies.
Study: [Policy evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)

**Q145 [I] Why does `ec2:DescribeInstances` have `Resource: '*'` in the worker role?**
Some AWS actions don't support resource-level permissions. The template comments this ("this call cannot be limited to one machine") and restricts Start/Stop to the exact instance ARN. Knowing which actions support resource scoping avoids both over-broad policies and "AccessDenied" mysteries.

**Q146 [I] What is `dynamodb:LeadingKeys` and where is it used?**
A condition key that restricts which partition-key values an identity may touch. The worker control function can read/write only items whose `pk` starts with `worker#`; the storage function can create/delete only under `scoll#`.
Study: [Fine-grained access control for DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/specifying-conditions.html)

**Q147 [I] How can you test IAM without deploying?**
`aws iam simulate-principal-policy` evaluates a role's policies for given actions/resources (one resource per call gives clean answers). The project also has template tests that parse `template.yaml` and assert e.g. "no platform function can write `portal/`".
Repo: `tests/test_template_*.py`.
Study: [IAM Policy Simulator](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_testing-policies.html)

**Q148 [I] Why does the teacher get AdministratorAccess in the docs, and what's the downside?**
The installer creates IAM roles, CloudFront, KMS, etc.; a narrower policy is hard to maintain and easy to get wrong. The downside is a large blast radius for the teacher's key; `CRITICAL_ISSUES` notes this as a rough edge.

**Q149 [I] Why is the installer's saved AWS key written into its own credentials *profile* section?**
So it never overwrites the teacher's other profiles, and `--profile` is explicit for every command. A timestamped backup is made before editing; keys are validated by shape (`AKIA...`, 40-char secret) first.
Repo: `installer/credentials.mjs`.

**Q150 [B] What is an access key and why must it never be in a repo or chat?**
A long-lived credential pair that acts as the user. Anyone with it can act as that user until it is deactivated. The repo has a test that fails if anything key-shaped (`sk-or-v1-...`, tokens, emails) is committed.
Repo: `tests/test_no_identifiers.py`.

## C. Secrets handling

**Q151 [B] Where does each secret live and who can read it?**
Platform OpenRouter management key and Google key → Secrets Manager (Deploy and Sweeper only). Teacher password hash + session secret → SSM SecureString. Student token → shown once, only its SHA-256 stored; on the student's computer only in `.env.local`. Student AI key → on the token item and in the student's function env (capped, revocable).
Repo: `docs/AI_CONNECTOR.md` section 5.

**Q152 [I] Why are Lambda environment variables "write-only" for students?**
So a stolen read-only dashboard/API call can't leak secrets, and AI-editor chat output never contains them. The API returns names only, and logs mask any value of 6+ chars.

**Q153 [I] How does the MCP server avoid putting secrets in an AI's context?**
Output is scrubbed: ANSI codes removed, anything shaped like a token (`mds_...`) becomes `mds_***`, and the teacher MCP also masks `sk-or-...`, `AKIA...`, `AIza...` before the model sees the text. The token is never placed in editor settings.

**Q154 [I] What is "trust on first use" (TOFU)?**
Accept a public key the first time you see it, then pin it and refuse changes (as SSH does). The kit's `signingKey` in `platform.json` is pinned on first download; later manifests must verify against it. Weakness: a first download from an already-compromised portal can't be detected.

**Q155 [I] What is a KMS key and why use an asymmetric one for signing?**
KMS stores keys in hardware-backed modules and never exports the private part; callers ask KMS to sign. An asymmetric ECC_NIST_P256 key lets everyone verify with the public key without being able to sign. Only the teacher's profile has `kms:Sign`; platform roles get none (a template test asserts it).
Study: [KMS overview](https://docs.aws.amazon.com/kms/latest/developerguide/overview.html), [Asymmetric keys](https://docs.aws.amazon.com/kms/latest/developerguide/symmetric-asymmetric.html)

**Q156 [D] Why is the replay "serial" the publish time instead of a counter or version number?**
A rollback publishes old *content* with a *new* serial, so clients that refuse lower serials still accept the teacher's deliberate rollback while rejecting an attacker replaying a genuinely old signed manifest.

**Q157 [D] Why sign the exact bytes of `kit-version.json` rather than canonical JSON?**
Re-serialising JSON can change whitespace/key order and break signatures; signing the raw bytes the server stores and the client downloads avoids canonicalisation bugs.

## D. Web and API security

**Q158 [B] Name the OWASP Top 10 categories most relevant here.**
Broken access control (tenant isolation), cryptographic failures (token/password handling), injection (zip paths, DynamoDB expressions, prompts), insecure design (limits), security misconfiguration (bucket policies), vulnerable components (vendored pypdf), identification and authentication failures (sessions), software and data integrity failures (kit signing, supply chain), logging failures, SSRF.
Study: [OWASP Top Ten](https://owasp.org/www-project-top-ten/).

**Q159 [I] What is XSS and how does the portal avoid it?**
Cross-site scripting: attacker text gets interpreted as HTML/JS. The portal uses a DOM helper that sets text via `textContent`, never `innerHTML` with data, and links get `rel="noopener noreferrer"`. A missing Content-Security-Policy on the portal is a listed residual risk.
Study: [OWASP XSS Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)

**Q160 [I] What is CSRF and why do bearer tokens in headers avoid most of it?**
Cross-site request forgery makes a browser send a cookie-authenticated request the user didn't intend. If auth is an `Authorization` header set by your own JS, a hostile page can't make the browser attach it. The sign-in plan uses cookies for *visitors*, so it must use `SameSite`, `Secure`, `HttpOnly` and a state/PKCE check.
Study: [MDN: CSRF](https://developer.mozilla.org/en-US/docs/Web/Security/Attacks/CSRF)

**Q161 [I] Why a lockout per IP for teacher logins and what's its weakness?**
After 5 failed logins an address is locked 15 min (`sessions.maxFailedLogins`, `lockoutMinutes`). Weakness: per-IP only, so an attacker with many IPs can try more; CRITICAL_ISSUES item 11. Always compute the scrypt hash even for a wrong username so timing doesn't reveal valid usernames.

**Q162 [I] What is SSRF and is it relevant?**
Server-side request forgery: tricking the server into fetching internal URLs (e.g. the metadata service). Relevant to any code that fetches user-supplied URLs; student functions have open egress, so the worker enforces IMDSv2-only. The platform itself fetches only fixed provider endpoints.
Study: [OWASP SSRF](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)

**Q163 [I] What is a path-traversal check in `app.py`?**
Zip entry names are normalised with `posixpath.normpath`; absolute names and names that resolve to `..` are rejected, so an entry can't write outside `sites/<slug>/`.

**Q164 [I] What is "mass assignment" and how does `token_action` avoid it?**
Binding all request fields into a record lets an attacker set fields they shouldn't. Admin routes accept only named parameters per action (`days`, `enabled`, `limit_usd`...), validate types and ranges, and ignore anything else.

**Q165 [D] Why validate JSON booleans strictly (`isinstance(flag, bool)`)?**
In Python `isinstance(True, int)` is `True` and `1`/`"yes"` could be misread as true. Strict checks avoid "truthy" bugs; `shape()` likewise excludes bool from valid HTTP status codes.

**Q166 [I] How does the AI cap hold even if the platform code has a bug?**
OpenRouter itself enforces the per-key dollar limit (the provider says no). "The provider enforces the limit, not our code" is a core design principle: put the control where a bug in your code can't bypass it.

**Q167 [I] Why is the Gemini path a proxy while OpenRouter hands out keys?**
Google has no per-student capped keys, so the platform must keep the real key and meter calls in its own Lambda (`ai_proxy.py`). The student gets `ga_<slug>.<secret>`. This is weaker (the cap is enforced by our code) so it checks key, token status, switch, provider, cap on every call.

**Q168 [D] What is a "residual risk" register and why list them honestly?**
A document of risks accepted on purpose (open egress, one teacher, no CSP, keys readable by admins). It prevents false confidence and tells future you what to fix first. See Spec 8.4 and `docs/CRITICAL_ISSUES.md`.

**Q169 [I] Why were the later security-hardening items (called F2/F3 in our session notes) deferred, and is that acceptable?**
The security phase was wound down on purpose: the owner decided to focus on features until outside teachers or a public beta exist (details in `docs/ROADMAP.md` and `docs/CRITICAL_ISSUES.md`). Deferral is a legitimate risk decision *if it is written down* with the trigger that re-opens it, as it is here.

**Q170 [B] What is the minimum security checklist before telling strangers to use it?**
Alerts and budget, clean-room install proven, backups restore, privacy guidance + erase a student, abuse path for public sites, capacity tested, supply-chain verification. That list is `docs/CRITICAL_ISSUES.md` P0–P1.

## Study materials for this chapter

- **OWASP Cheat Sheet Series**: <https://cheatsheetseries.owasp.org/> (read: Authentication, Session Management, Password Storage, Authorization, Input Validation)
- **AWS Security Best Practices**: <https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html>
- **AWS Well-Architected Security Pillar**: <https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html>
- Book: *Threat Modeling: Designing for Security* (Adam Shostack). Book: *Serverless Security* (Liran Tal) is a good short intro.
- YouTube (search): "AWS re:Inforce IAM policy deep dive", "OWASP Top 10 explained (Fireship)", "Confused deputy problem".
- Hands-on: run `node tests/run.mjs` and read which test files start with `test_template_` — each is a security rule written as a test.
