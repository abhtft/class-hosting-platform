# Student frontend hosting (teacher setup)

## Get your own copy running (teachers)

You need an AWS account, Node 20+ and the AWS CLI signed in (`aws configure`). Then one command, no download and no git:

```bash
npx -y https://github.com/abhtft/class-hosting-platform/releases/latest/download/class-platform-teacher.tgz install
```

It asks for your AWS profile, an optional domain, which features you want and your teacher login, then installs everything into **your** account (10 to 15 minutes). Later, each new release is one command (the release page shows it): `npx -y https://github.com/abhtft/class-hosting-platform/releases/download/v<new>/class-platform-teacher-<new>.tgz upgrade`. Step by step: [docs/TEACHER_SETUP.md](docs/TEACHER_SETUP.md).

---

Every student publishes their built frontend (`dist/`) to **your** AWS account with one command:

```bash
node deploy.mjs
```

Students get no AWS access. They only get a personal deploy key.

```
deploy.mjs ──POST /upload-url──▶ API Gateway ─▶ Lambda ──(checks key in SSM)
           ──PUT dist.zip──────▶ S3 uploads/<student>.zip
           ──POST /publish─────▶ Lambda: unzip → S3 sites/<student>/ , clear CloudFront cache
Browser ──▶ CloudFront ──(router function)──▶ S3 sites/<student>/   (private bucket, OAC)
```

| File | What it is |
|---|---|
| `template.yaml` | Everything in AWS: bucket, CloudFront, router function, API, Lambda, key store |
| `lambda/app.py` | The upload/publish API (stdlib + boto3, nothing to install) |
| `admin.mjs` | Add, remove and list students |
| `student/deploy.mjs` | The file you hand to students |
| `student/deploy.yml` | Optional GitHub Action for students (auto-deploy on push) |
| `student/portal-deploy.mjs`, `portal-deploy.yml`, `CICD.md` | Token-based deploy of `frontend/` and `backend/` from a laptop or GitHub Actions (guide for students: `student/CICD.md`) |
| `student/README.md` | Student instructions |

## Documentation map

| Read this | For |
|---|---|
| **[docs/TECHNICAL_SPECIFICATION.md](docs/TECHNICAL_SPECIFICATION.md)** | **The source of truth for Release 1.0 (MVP):** scope, architecture, data model, state machines, every feature with its flow and rules, limits, configuration, security, release procedure, decisions, open items and a requirements-to-tests traceability matrix. Its numbers are checked against the code by a test. If another document disagrees, this one wins |
| **[docs/STUDENT_KIT.md](docs/STUDENT_KIT.md)** | **Student kit:** a starter project for AI editors (rules, checker, local dev, auto-deploy): the journey from a token to a deployed app, how the architecture is enforced, security, publishing, tests |
| **[docs/AWS_LEARNINGS.md](docs/AWS_LEARNINGS.md)** | **A learning guide to the AWS ideas in this project:** CloudFront, S3 and presigned uploads, CORS, IAM and least privilege, Lambda limits, API Gateway, DynamoDB design, CloudFormation change sets, secrets, security mindset, cost, testing against the real account, a log of real mistakes and exercises |
| **[docs/TEACHER_SETUP.md](docs/TEACHER_SETUP.md)** | **Install and update your own copy:** `npx -y class-platform-teacher install` (own AWS account, optional domain, no clone), `upgrade`, feature switches, the teacher MCP, cost and removal; versions in [CHANGELOG.md](CHANGELOG.md) |
| **[docs/MCP.md](docs/MCP.md)** | **How MCP is used here:** the `class-platform-mcp` package, its five tools, one-command editor setup, token handling, distribution from the portal, updates, security model, tests and troubleshooting |
| **[docs/STORAGE_CONNECTOR.md](docs/STORAGE_CONNECTOR.md)** | **Class storage connector (live, off per student until the teacher switches it on):** a capped per-student database and file area for backends, how isolation and cost are controlled, how to switch it on |
| **[docs/AI_CONNECTOR.md](docs/AI_CONNECTOR.md)** | **Class AI connector:** workflow diagrams (setup, teacher on/off, a student's AI call, key lifecycle), where each secret lives, threat model, IAM matrix, operations and emergencies |
| **[docs/NEW_ACCOUNT_AND_TRANSFER.md](docs/NEW_ACCOUNT_AND_TRANSFER.md)** | **New AWS account and project transfer:** two accounts with CLI profiles, what a new account needs (credits, CloudFront verification), the install without the SAM CLI (verified), and the designed installer and export/import between accounts (not built yet) |
| **[docs/END_TO_END.md](docs/END_TO_END.md)** | **The complete workflow:** architecture, how requests travel, teacher and student workflows, setup, operations, verification, security, troubleshooting, limits |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Every HTTP route, request, response and error code |
| [implement.md](implement.md) | Step-by-step setup and verification (Parts 1 to 4) |
| [student/BACKEND.md](student/BACKEND.md) | Student guide to writing a backend |
| [backend_wire.md](backend_wire.md) | Design reasoning for student backends |
| [learning.md](learning.md) | Lessons learned, mistakes and fixes |

## Web portal (deploy.example.edu)

Besides the CLI, there is a web portal with two sections, each with its own login:

- **Teacher** (`/teacher`, username and password): create tokens with an expiry and an optional student name and site name, then share the token. Extend, revoke or take a site offline, and approve or deny **regeneration requests** from the inbox.
- **Student** (`/student`, the token is the login): shows a validity bar, drag-and-drop for the build folder (or a `.zip`) with a checklist of the upload guidelines, and a **Request regeneration** button once the token is within 3 days of expiry.

Token life: active until expiry, then 3 days read-only (the site stays online and the student can request regeneration), then the site goes **offline** (moved to `archive/`, never deleted). Approving a request or extending the token restores it.

```
deploy.example.edu ─ CloudFront ─┬─ /*     → S3 portal/ (the pages in web/)
                                       └─ /api/* → API Gateway → Lambda → DynamoDB
Daily: sweeper Lambda archives sites past expiry + grace
```

| File | What it is |
|---|---|
| `web/` | The portal pages (plain HTML/JS, no build step): `index`, `teacher`, `student`, `common.js`, `check.js`, `zip.js`, `shared.css` |
| `lambda/portal.py` | Token, login and regeneration logic |
| `tests/dev_server.py` | Runs the whole portal locally with demo data |

Setup: `sam deploy`, then `node admin.mjs teacher-setup <name>`, `node admin.mjs portal-publish`, and a `deploy` CNAME at your DNS host. Step by step in [implement.md](implement.md), Part 2.

## Custom domains for students

The teacher can switch a custom domain on per student (the **Domain: off / on** button). The student then enters a subdomain such as `www.theirname.com`, adds one CNAME at their DNS host, and HTTPS is set up automatically. Built on CloudFront multi-tenant distributions; the AWS pieces created by hand and the operating notes are in [implement.md](implement.md), Part 3.

## Student backends (pilot)

The teacher can let a student host a small function (Node 22 or Python 3.12) that their site calls at `/api/...` (no CORS to configure). Per student the teacher has a **Backend on/off** toggle and a **run allowance** (a number of runs per day, month or in total). Student code runs in a log-only sandbox behind a gateway with an in-flight limiter that protects the rest of the AWS account. Deployed (`BackendEnabled="true"` in `samconfig.toml`; the template default is `false`). Plan: [backend_wire.md](backend_wire.md). Setup and operations: [implement.md](implement.md) Part 4. Students: [student/BACKEND.md](student/BACKEND.md) and the starter templates in `student/backend-template/`.

**Class storage (optional, off per student by default; deployed with `StorageEnabled`):** the teacher can give a student's backend a small database and file area with hard caps; the student uses the `class_storage` helper (the kit's sample backend has a `/notes` route). Code: `lambda/storage.py`, `lambda/storage_api.py`. Details: [docs/STORAGE_CONNECTOR.md](docs/STORAGE_CONNECTOR.md).

**Class AI (optional, off by default):** the teacher can switch on an AI key for a student's backend: a capped, revocable OpenRouter key per student (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AI_MODEL` appear in the function), with a dollar cap the teacher edits and a usage meter. The platform's own management key stays in Secrets Manager. Code: `lambda/ai.py`, `lambda/openrouter.py`. Workflows and security: [docs/AI_CONNECTOR.md](docs/AI_CONNECTOR.md); setup: [docs/END_TO_END.md](docs/END_TO_END.md) sections 3.3a and 4.5c; students: [student/BACKEND.md](student/BACKEND.md).

## One-time setup (about 20 min)

Prerequisites: AWS CLI (logged in), [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html), Node 22+.

### 1. Pick the URL style

| | Subdomain (recommended) | Path |
|---|---|---|
| Student URL | `https://alice.apps.example.com` | `https://dxxxx.cloudfront.net/alice/` |
| Student code changes | **none** | must set `base: '/alice/'` in `vite.config` |
| You need | a domain you control + free ACM certificate | nothing |

For subdomains, request an ACM certificate for `*.apps.example.com` **in us-east-1** (CloudFront requires that region) and validate it via DNS.

### 2. Deploy the stack

```bash
cd infra/student-deploy
sam deploy --guided --stack-name student-hosting --capabilities CAPABILITY_IAM
```

When prompted, set `RootDomain=apps.example.com` and `CertificateArn=arn:aws:acm:us-east-1:...`, or leave both empty for path mode. Choose any region close to your students (e.g. `ap-south-1`). The first deploy takes about 5 minutes (CloudFront). Later `sam deploy` runs reuse the saved answers.

### 3. Point DNS (subdomain mode only)

Create one record at your DNS provider: `*.apps.example.com  CNAME  <DistributionDomain output>`.
With Route 53 you can use an A/ALIAS record to the distribution instead.

### 4. Bake the API URL into the student script

Copy the `DeployApiUrl` output into `API_URL` at the top of `student/deploy.mjs`. You hand out this exact file.

### 5. Add students

```bash
node admin.mjs add alice bob carol     # prints DEPLOY_KEY + site URL for each
node admin.mjs list
node admin.mjs remove bob              # revokes the key within a minute; the site stays up
```

Send each student their `DEPLOY_KEY` line, plus `student/deploy.mjs` and `student/README.md`.

## Day-to-day

- **Take a site down:** `aws s3 rm s3://<BucketName>/sites/alice --recursive`
- **See what's deployed:** `aws s3 ls s3://<BucketName>/sites/`
- **Lambda logs:** `sam logs --stack-name student-hosting --tail`
- **Tear everything down:** empty the bucket, then run `sam delete --stack-name student-hosting`

## Limits and costs

- 50 MB zip, 200 MB unzipped, 2,000 files, about 25 s publish time per deploy. A typical Vite app is 1–5 MB and publishes in 2–3 s.
- The key map is one standard SSM parameter (4 KB), which holds about 100 students. `admin.mjs list` shows how much is used. For more students, switch the parameter to the Advanced tier in the console.
- The API is throttled to 20 requests/s (burst 50) to protect against loops or abuse.
- Each publish clears 2 cache paths, and the first 1,000 paths a month are free (about 500 deploys). A class typically costs **$0–1/month**.

## Security model (kept simple on purpose)

- Students never get AWS credentials. Their key only lets them overwrite **their own** `sites/<name>/` folder.
- The bucket is private. Only this CloudFront distribution can read it, and only `sites/*`.
- The Lambda role can touch only this bucket, create cache invalidations on this distribution, and read the key parameter.
- Keys are stored in plain text in SSM, which is fine for a class. Rotate a key with `remove` and then `add`.
- **Do not change the `StudentKeys` or `TeacherAccount` resources in `template.yaml`.** If CloudFormation ever updates one, it resets to `{}` (all CLI keys, or the teacher login).
- Portal tokens are random 128-bit values and only their SHA-256 is stored. Teacher and student sessions use different signing keys, and a student session can only write to its own site folder.

## Backends

Student frontends are static, so CloudFront does **not** proxy `/api`. Each app must call its backend through `import.meta.env.VITE_API_URL`, and that backend must:
- be served over **HTTPS** (browsers block HTTP calls from HTTPS pages), and
- allow the student's site origin in CORS (`https://alice.apps.example.com`, or `*` for simplicity).
