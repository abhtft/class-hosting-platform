# AWS deep dive: become the expert in what you built

Private study notes (this folder is git-ignored: `.temp/`). Written from the real `template.yaml` (1,075 lines, 33 resources), the Lambda code in `lambda/`, the CloudFront functions, and `installer/tenant.mjs`. Every claim is tied to a file so you can check it. Where I am not certain of a console label or a price, I say **verify**.

The goal is not to memorise AWS. It is to be able to answer, for any part of your platform: *what is it, why is it there, who can call it, with what identity, what are its limits, how does it fail, what does it cost, and how do I prove all of that in the console?*

---

## 1. How to use these notes

| File | What you learn | Time |
|---|---|---|
| `01-architecture-and-flows.md` | The whole system, every request path, the data model | 2 h |
| `02-iam-deep-dive.md` | IAM theory, every role in the stack, how to prove permissions | 3 h |
| `03-s3-cloudfront-edge.md` | S3, CloudFront, OAC, the router function, caching, CORS on S3 | 3 h |
| `04-api-lambda-cors-throttling.md` | API Gateway, Lambda, the gateway's 9 checks, CORS, throttling | 3 h |
| `05-data-secrets-state.md` | DynamoDB, SSM, Secrets Manager, passwords, sessions, tokens | 2 h |
| `06-connectors-ai-storage-backends.md` | Student backends, class AI, class storage, delegation patterns | 2 h |
| `07-custom-domains-dns-tls.md` | DNS, TLS, ACM, CloudFront multi-tenant, the domain state machine | 2 h |
| `08-cloudformation-lifecycle-ops.md` | CloudFormation, SAM, change sets, alarms, logs, cost, quotas | 2 h |
| `09-security-review-threat-model.md` | A design review: threats, residual risks, hardening list | 2 h |
| `10-labs-checklist-exam.md` | 14 hands-on labs, a verification checklist, a 40-question self-test | 3 h |

Suggested pace: five days. Day 1: files 01 and 02. Day 2: 03 and 04. Day 3: 05 and 06. Day 4: 07 and 08. Day 5: 09 and 10.

Each file has the same shape: **theory** (what the AWS service is and the rule behind it), **what we built** (resource IDs and code references), **console steps** (where to click), **CLI commands** (to verify the same thing), **expected result**, and **what the result proves**.

## 2. Three ways to study (pick one per session)

| Mode | What you need | Good for |
|---|---|---|
| **A. Static** | Nothing running. Just this repo. | Reading `template.yaml`, the code and the policies. Most of IAM, CloudFront and CORS can be learned here. |
| **B. Test platform** | A running platform in your **test** account (`teacher-test`). | Every lab. Your test account currently has no platform: AWS asked it for CloudFront verification. Once AWS confirms, `npx -y "$PKG" install ...` rebuilds it. |
| **C. Read-only look at production** | Your own decision. | Your `default` profile is your live class. **Only ever run `describe`/`get`/`list` commands there, never a command that changes something.** I do not touch it unless you name it, and these notes mark every command that could change anything with `[CHANGES THINGS]`. |

### Mode A: things you can do with no account at all

```bash
# lint the template (CloudFormation rules)
pip install cfn-lint && cfn-lint template.yaml

# the security scan the project's CI runs (34 checks pass; the exceptions are listed in .checkov.yaml)
pip install checkov && checkov -f template.yaml --config-file .checkov.yaml

# see the template exactly as CloudFormation sees it after SAM expands it
# (needs the AWS SAM CLI; this prints the expanded template, creates nothing)
sam validate --lint -t template.yaml
```

## 3. Safety rules for the labs

1. **Always name the profile.** `export AWS_PROFILE=teacher-test` in the terminal you use for labs, then check it:
   ```bash
   aws sts get-caller-identity --query Account --output text   # must print your TEST account, not production
   ```
2. **Region is `us-east-1`** for everything (`installer/engine.mjs` sets `REGION = 'us-east-1'`: CloudFront certificates must live there). In the console, set the region selector to **N. Virginia** before looking for anything except CloudFront, IAM, Route 53, ACM-for-CloudFront (which are global or us-east-1 anyway).
3. **Commands are labelled** `[READ]` (safe) or `[CHANGES THINGS]` (only in the test account).
4. **Never paste secrets** you see (keys in function environment variables, items in the tokens table) into chat, notes or screenshots.
5. Console labels change over time. If a menu is not where I say, use the search box at the top of the console, and the **resource name** (the logical IDs below) to find it.

### Handy variables (put these in your shell)

```bash
export AWS_PROFILE=teacher-test
export AWS_REGION=us-east-1
export STACK=student-hosting

# the physical names of the resources (every lab uses these)
aws cloudformation describe-stacks --stack-name $STACK --query 'Stacks[0].Outputs' --output table      # [READ]
aws cloudformation list-stack-resources --stack-name $STACK \
  --query 'StackResourceSummaries[].[LogicalResourceId,ResourceType,PhysicalResourceId]' --output table  # [READ]
```

## 4. Inventory: what exists in your account

### 4.1 Created by `template.yaml` (CloudFormation stack `student-hosting`)

| Logical ID | AWS type | Why it exists | File:line |
|---|---|---|---|
| `SitesBucket` | S3 bucket | One private bucket for everything: `sites/`, `portal/`, `uploads/`, `archive/`, `userdata/` | template.yaml:164 |
| `SitesBucketPolicy` | S3 bucket policy | Lets only your CloudFront distributions read `sites/*` and `portal/*` | :210 |
| `StudentKeys` | SSM parameter | The old per-student deploy-key map (legacy CLI) | :248 |
| `TeacherAccount` | SSM parameter | Teacher login: username, salt, scrypt hash, session-signing secret | :256 |
| `TokensTable` | DynamoDB table | Tokens, site-name locks, counters, settings: nearly all state | :264 |
| `StudentDataTable` | DynamoDB table | Class storage data (only if storage is on; **Retain**) | :282 |
| `SitesOAC` | CloudFront origin access control | Signs CloudFront's requests to S3 (SigV4) | :301 |
| `RouterFunction` | CloudFront function | Maps host/path to `sites/<student>/` with SPA fallback | :311 |
| `Distribution` | CloudFront distribution | Serves student sites (`*.<domain>`), and `/api/*` to backends | :363 |
| `BackendRouteFunction` | CloudFront function | Rewrites `/api/*` to `/b/<slug>/*`, injects the viewer IP | :423 |
| `StudentSandboxBoundary` | IAM managed policy | The permission **boundary** for student functions | :455 |
| `StudentSandboxRole` | IAM role | The one role every student function runs as (logs only) | :466 |
| `BackendApi` | API Gateway HTTP API | Front door for student backends | :488 |
| `GatewayFunction` | Lambda | The 9-check gateway in front of student functions | :496 |
| `StorageApi` / `StorageFunction` | HTTP API / Lambda | Class storage API | :536 / :544 |
| `AiProxyApi` / `AiProxyFunction` | HTTP API / Lambda | OpenAI-compatible proxy for Gemini | :591 / :599 |
| `PortalFunction` | CloudFront function | Clean URLs for the portal, injects the viewer IP | :627 |
| `PortalDistribution` | CloudFront distribution | The portal (`deploy.<domain>`): pages from S3, `/api/*` to the API | :650 |
| `DeployApi` / `DeployFunction` | HTTP API / Lambda | The portal API: login, tokens, upload/publish, backends, AI, domains | :708 / :715 |
| `SweeperFunction` | Lambda (+ daily schedule) | Daily 03:00 UTC: archive expired sites, finish domain removals | :819 |
| `AlertsTopic`, `AlertsTopicPolicy`, `AlertsEmail` | SNS | One email channel for alarms | :863 to :897 |
| 7 alarms | CloudWatch alarms | Lambda concurrency/throttles/errors, gateway errors, storage errors, backend 5xx | :906 to :1006 |

(Conditions decide which of these exist: `UseBackend`, `UseAi`, `UseGemini`, `UseStorage`, `UseCustomDomain`, `UseAlerts`.)

### 4.2 Created OUTSIDE the template (by the installer or at run time)

| What | Created by | Why outside CloudFormation |
|---|---|---|
| Secrets Manager secret `openrouter` | `secrets` command | It holds keys you type: never in a template |
| ACM wildcard certificate `*.<domain>` (us-east-1) | `installer/engine.mjs` `ensureCertificate` | Needs your DNS validation record |
| Artifacts bucket `<stack>-artifacts-<account>` | installer | Holds the packaged Lambda code CloudFormation needs |
| CloudFront **connection group**, **multi-tenant distribution**, two **CloudFront functions** (`<stack>-spa-fallback`, `<stack>-tenant-api`) | `installer/tenant.mjs` | CloudFormation had no resource for multi-tenant distributions when this was built |
| CloudFront **distribution tenants** (one per student domain) | the portal, at run time (`lambda/domains.py`) | One per student, created on demand |
| Lambda functions `sd-<slug>` and their log groups `/aws/lambda/sd-<slug>` | the portal, at run time (`lambda/backends.py`) | One per student backend |
| SSM parameter for the alert email address | you (`/student-deploy/alert-email`) | Keeps the address out of the repo |
| OpenRouter per-student keys | the portal, via OpenRouter's API | They live at OpenRouter, not in AWS |

Knowing this split is itself architect knowledge: **what the stack owns, and what it does not**, decides what `destroy` removes, what drift can hide, and what survives a rebuild.

## 5. Six questions to ask about every component

When you study each service, answer these six. If you can answer them for all 33 resources, you know the system.

1. **Trust boundary:** what is on the untrusted side (a student, the internet) and what is on the trusted side?
2. **Who can call it?** (public URL? only CloudFront? only another Lambda?)
3. **With what identity?** (an IAM role, a session token, a key, nothing)
4. **What are its limits?** (size, time, rate, concurrency, count)
5. **How does it fail?** (what the caller sees; what the operator sees; does it fail open or closed?)
6. **What does it cost** and what stops the cost from running away?

## 6. Map: which code implements which control

| Control | Where |
|---|---|
| Teacher login, lockout, sessions, token hashing | `lambda/portal.py` (`scrypt_hash`, `sign_session`, `verify_session`, `teacher_login`, `student_login`) |
| Upload, zip validation, publish, archive | `lambda/app.py` (`upload_url`, `publish`, `site_entries`, `archive_site`) |
| Backend gateway: 9 checks, in-flight limiter | `lambda/gateway.py` |
| Student function creation, limits, env var rules | `lambda/backends.py` |
| Atomic counters (allowances, rate limits) | `lambda/portal.py` `DynamoStore.counter_add` |
| Class AI (OpenRouter keys; Gemini proxy) | `lambda/ai.py`, `openrouter.py`, `gemini.py`, `ai_proxy.py` |
| Class storage | `lambda/storage.py`, `storage_api.py` |
| Custom domains | `lambda/domains.py`, `installer/tenant.mjs`, `custom-domain/*.js` |
| CloudFront routing | the functions inside `template.yaml` (`RouterFunction`, `BackendRouteFunction`, `PortalFunction`) |
| Install, plan/apply, destroy | `installer/engine.mjs` |
| Alarms | `template.yaml` lines 862 to 1021 |
