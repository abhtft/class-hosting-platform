# Moving Postiz from Railway to AWS: feasibility, options, cost, plan

Status: PLAN ONLY (2026-11-24). Nothing was created, changed or deleted on AWS or Railway. No secret value was read: only variable NAMES, service configs and metrics (7 days). Cost figures are estimates; the AWS ones reuse the prices read from the AWS Pricing API on 2026-11-20 in `CHATWOOT-MIGRATION-PLAN.md` and must be re-checked before the owner approves anything.

## 0. Verdict

* **Through the platform stack `student-hosting` (`template.yaml`): NOT possible.** The platform is static sites plus one Lambda function per student (no ports, no containers, no long-running process, no database). `template.yaml` has no ECS, Fargate, RDS, ElastiCache or Lightsail resource. Its only EC2 is the optional **compute worker** (`WorkerInstance`, off by default, no inbound access, starts, runs a script and shuts itself down; `docs/COMPUTE_WORKER.md`): a teacher batch machine, not a web server, so it cannot serve Postiz. The always-on EC2 pattern is in `chatwoot-host/template.yaml`. Postiz is a long-running Node app (nginx + PM2 with backend, frontend, orchestrator workers and cron) that needs Postgres, Redis and a Temporal server. Lambda cannot host it.
* **On AWS in the same account, as its own separate stack: possible, and a pattern already exists.** `chatwoot-host/template.yaml` is exactly this: its own CloudFormation stack, independent of `student-hosting`, one Graviton EC2 with Docker Compose, a separate encrypted data EBS disk with nightly snapshots (DLM), a private S3 bucket (Retain), SSM Session/run-command instead of SSH, Parameter Store for settings, an EC2 auto-recover alarm. Postiz fits that pattern directly. **Recommended: a new stack `postiz-host/template.yaml`, copied from `chatwoot-host`, with Docker Compose running Postiz + Temporal + Postgres + Redis on one EC2 `t4g.medium` (4 GB), Cloudflare (or direct DNS) in front.** Estimated about **USD 32 a month in us-east-1** (table in section 4).
* **It does not save money.** Railway is probably in the same range (not read; compare with the real Railway bill). The gains are one cloud and one bill, backups you control, no "sleeping" application (all three Postiz services have `sleepApplication: true` today, which can delay scheduled posts), and IAM control.
* Effort: about 1 day to build and rehearse, 30 to 60 minutes of downtime at cutover. Risk: medium-low, because the domain can stay `postiz.margdarshakss.com` (see section 3).

## 1. What runs on Railway today (evidence)

Project **`content-nourishment`** (id `1209fe12-...`), environment `production`, workspace "Anivesh Panjiyar's Projects". All four services are in `asia-southeast1` (Singapore), 1 replica each.

| Service | Image | Config seen | Measured (7 days) |
|---|---|---|---|
| **Postiz** | `ghcr.io/gitroomhq/postiz-app:v2.25.1` (pinned by digest) | port 5000, healthcheck `/`, limit 2 CPU / 2 GB, volume `postiz-volume` 5 GB at `/uploads`, domains `postiz.margdarshakss.com` (custom) and `postiz-production-68fa.up.railway.app`, CDN on, sleepApplication on | RAM avg 1.81 GB, max 2.0 GB (at its 2 GB limit); CPU avg 0.1 vCPU, max 3.6 (bursts); disk 0.78 GB |
| **Temporal** | `temporalio/auto-setup:1.28.1` | private name `temporal`, limit 1 CPU / 0.5 GB, no volume | RAM 0.19 GB, CPU about 0.01 |
| **Postgres** | `railwayapp-templates/postgres-ssl:18` (**PostgreSQL 18**) | private name `postgres`, volume 5 GB, no public TCP proxy now | RAM 0.18 GB, data 0.28 GB |
| **Redis** | `redis:8.2` | volume 5 GB | RAM 0.007 GB, data 0.09 GB |

* **Traffic**: 4,243 HTTP requests in 7 days (about 600 a day): 1,353 2xx, 2,160 3xx, 707 4xx, 23 5xx. Tiny.
* **Total RAM needed: about 2.3 GB for the four services**, plus the operating system. So a 2 GB machine is **not enough**; **4 GB (t4g.medium) is the minimum sensible size**. CPU is idle with short bursts (likely video/image work or Next.js start-up).
* **Variable NAMES on Postiz** (values not read): `DATABASE_URL`, `REDIS_URL`, `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `JWT_SECRET`, `FRONTEND_URL`, `MAIN_URL`, `NEXT_PUBLIC_BACKEND_URL`, `BACKEND_INTERNAL_URL`, `PORT`, `NOT_SECURED`, `IS_GENERAL`, `DISABLE_REGISTRATION`, `API_LIMIT`, `RUN_CRON`, `NX_ADD_PLUGINS`, `STORAGE_PROVIDER`, `UPLOAD_DIRECTORY`, `NEXT_PUBLIC_UPLOAD_DIRECTORY`, social apps `FACEBOOK_APP_ID/SECRET`, `INSTAGRAM_APP_ID/SECRET`, `YOUTUBE_CLIENT_ID/SECRET`, Google sign-in `AUTH_GOOGLE_CLIENT_ID/SECRET`, and a generic OAuth login `POSTIZ_GENERIC_OAUTH`, `POSTIZ_OAUTH_*` (auth/token/userinfo URLs, client id/secret, scope, display name). **Uploads are on local disk** (`STORAGE_PROVIDER` is local, `UPLOAD_DIRECTORY=/uploads`; no Cloudflare R2/S3 variables), so the 5 GB volume holds the media (0.78 GB used).
* **Temporal variable names** show it uses the **same Postgres** (`DB`, `DBNAME`, `VISIBILITY_DBNAME`, `POSTGRES_SEEDS`, `POSTGRES_USER`, `POSTGRES_PWD`), with Elasticsearch off (`ENABLE_ES`). Inferred; verify the database names at the rehearsal.
* **Logs**: Postiz logs show "OpenAI API key not set, chat functionality will not work" and repeated `POST /api/copilot/chat` and `/api/copilot/agent` upstream timeouts (the built-in AI copilot, which has no key; unrelated to the move, but the new server will behave the same unless a key is set). "Email sender information not found" means no e-mail provider is configured. Internet bots probe `/uploads/.env` (404, harmless).
* **The Postiz MCP.** This Claude Code has a Postiz MCP connector (tool prefix `mcp__0c4cb93b-...__`: `integrationList`, `integrationSchedulePostTool`, `uploadFromUrlTool`, `generateImageTool`, `videoStatusTool`, `postsListTool`, `mediaListTool`, ...). Its URL and key were NOT read (they are not in any file I may open without exposing the key; I only searched for the host and found none). Postiz exposes MCP from its own backend, authenticated by the **organisation API key** from Postiz Settings (some versions put the key in the URL path, others use a header). **To be confirmed by the owner:** run `claude mcp list` and look at the host part of the Postiz entry. The key is stored in the Postiz database, so it survives a database restore.

## 2. What the teacher platform can and cannot host (checked)

| Question | Answer | Evidence |
|---|---|---|
| Can `student-hosting` run a container, port, Postgres or Redis? | No | `AGENTS.md`/student rules (static site + one function, "no servers, no ports, no databases"); `template.yaml` has Lambda, API Gateway, DynamoDB, S3, CloudFront, Cognito, KMS, no ECS/RDS/ElastiCache; its one EC2 is the optional batch worker (`COMPUTE_WORKER.md`), not a server |
| Is there any container/server capability in the repo? | Only `chatwoot-host/template.yaml`, a separate stack | file read in full |
| Reusable pattern for "an external app moved from Railway"? | Yes: `docs/notes/CHATWOOT-MIGRATION-PLAN.md` (options A-D, replica first, gates G0-G3, R1-R7) and `chatwoot-host/template.yaml` | read in full |
| Is Postiz known to the platform? | No mention of Postiz anywhere in docs, `template.yaml` or code | search |
| Platform account | The platform lives in the default account (us-east-1). `MIGRATION-AUDIT.md` plans a later move of the whole platform to account `805751386990` (profile `new`); the Chatwoot plan recommended building straight into the new account | see open question Q1 |

So "through the platform" can only mean: **same repo, same conventions, same account, separate stack**, not the platform stack itself.

## 3. Data and cutover: what moves, what breaks

**Keep the domain `postiz.margdarshakss.com`.** This is the single decision that removes most of the risk: social-network redirect URIs, OAuth callbacks, tokens bound to the domain and the MCP URL all stay valid. Only the DNS record changes at cutover.

| Item | Where it lives | How it moves | Breaks if the domain changes? |
|---|---|---|---|
| Postiz database (users, orgs, channels, **encrypted social tokens**, posts, API key, media table) | Postgres 18 | `pg_dump` from Railway with a **pg18** client (the Railway image is PG 18; a pg16 dump tool cannot read it), restore into **postgres:18** on AWS (same major, no upgrade surprises) | Tokens are tied to `JWT_SECRET` (Postiz encrypts channel tokens with it). **`JWT_SECRET` must be copied exactly**, or all channels must be reconnected |
| Temporal state (scheduled-post workflows, history) | databases of the same Postgres (names to verify, probably `temporal` and `temporal_visibility`) | dump and restore them too, in the same window. **Missing this means posts scheduled in the future are not executed** | no |
| Uploads (media files, 0.78 GB used) | Railway volume `/uploads` | copy files (Railway has no volume download MCP tool; use a temporary one-off copy through the app container, or `railway run`/shell by the owner, or re-upload). The DB stores paths like `/uploads/...` under `FRONTEND_URL`; same domain keeps them valid | yes, absolute URLs inside posts would point to the old host |
| Redis | volume 0.09 GB | **expendable**: queue and cache only; start empty. Make sure no job is mid-flight at cutover | no |
| Social apps (Facebook, Instagram, YouTube, Google login, generic OAuth) | provider consoles | **nothing to change if the domain stays**. If it changes: every redirect URI (`https://<host>/integrations/social/<provider>`, and the login callbacks) must be edited in Meta, Google Cloud, and the generic OAuth provider, and Meta app review/domain settings re-checked, and each channel reconnected | yes |
| Secrets (app secrets, `JWT_SECRET`, DB password) | Railway variables | copy by a sanctioned path into Parameter Store `SecureString` under `/postiz/` (gate G0, same rule as Chatwoot). Never in git, never printed | n/a |
| Postiz MCP in Claude Code | local Claude config | same domain: **no change**. New domain: edit the MCP URL once | yes |
| API key (public API and MCP) | DB | restored with the DB | no |
| Webhooks Postiz sends out / receives | in DB | same domain: no change | yes |

**Downtime and cutover (30 to 60 minutes):** pick a quiet window with no posts due (look at `postsListTool` for the next 24 hours); lower the DNS TTL a day before; stop Railway Postiz and Temporal (so nothing writes or publishes), final dump of Postgres, copy uploads, restore on AWS, start, switch DNS, test login, `integrationList`, one scheduled test post. **Do not run both at once**: two Postiz instances with the same database copy would both publish the same scheduled posts to real social accounts (the same danger as the Chatwoot replica). So the replica rehearsal must start with **Temporal stopped (or with workers disabled)** and **no scheduled posts due**, or on a copy where the posts are set aside; publishing nothing is part of the check list.

**Rollback:** keep Railway stopped (not deleted) for 7 days; the way back is the DNS record (and starting the Railway services). Posts created or changed on AWS after the cutover would not exist on Railway; accept that or dump back.

## 4. Options and cost (USD a month, estimate)

Prices: on-demand Linux, us-east-1, from the figures read on 2026-11-20 (t4g.medium about 24.5 with the 4 GB). Public IPv4 3.7. EBS gp3 0.0912 per GB-month (2.7 for 30 GB; plus 20 GB root 1.8).

| Option | Parts | Estimate | Effort | Risk |
|---|---|---|---|---|
| **A. One EC2 t4g.medium, Docker Compose (recommended)** | Postiz + Temporal + Postgres 18 + Redis in Compose; 30 GB encrypted data disk, nightly snapshots, S3 bucket for dumps/uploads, Elastic IP, Cloudflare or Route 53 + Caddy for HTTPS, SSM instead of SSH, Parameter Store | **about 32** (instance 24.5, EBS 4.5, IP 3.7, snapshots/S3 about 1) | build about 1 day (copy `chatwoot-host`) | low-medium; single machine, no failover; manual upgrades |
| A-small. t4g.small 2 GB | same | about 20 | same | **High: Postiz alone sits at 1.8 to 2.0 GB; will swap or be killed. Not recommended** |
| B. Lightsail 4 GB | same Compose, flat plan with static IP | about 24 | about 0.5 day | low; separate console and no CloudFormation parity (Lightsail IaC is limited); x86 |
| C. Managed: ECS Fargate (Postiz 1 vCPU/2 GB, Temporal 0.5 vCPU/1 GB) + RDS PostgreSQL db.t4g.micro + ElastiCache + EFS or S3 for uploads + ALB | AWS-managed backups and failover | **about 120 to 160** | 3 to 5 days, Postiz has no native S3 mode here (needs `STORAGE_PROVIDER` change and testing), EFS needed | low runtime risk, high build risk; far more than this load needs |
| D. EC2 + RDS PostgreSQL only | Redis/Temporal in Compose | about 50 | 1.5 days | middle ground; Temporal on RDS needs its own databases |

Free tier: do not rely on it. A newly opened account may carry credits and some t4g trials, but that must be checked in the Billing console; the default account is old. Cost alarm required. (Railway today: see section 10, about 26 to 30 plus the plan fee, from measured usage; compare with the real bill.)

**Architecture note:** Postiz image is `linux/amd64` and (to verify with `docker manifest inspect`) `linux/arm64`. If arm64 is missing, use `t3.medium` (x86, about 30 for the instance; total about 38) instead of Graviton.

## 5. Security and the owner's standing rules

* **Ask before cost**: nothing is created until the owner says "Yes, execute" on the change set; each cost-bearing step is listed with its monthly price.
* **IaC only**: `postiz-host/template.yaml` (own stack, `DeletionPolicy: Retain` on the bucket, `Snapshot` on the data disk), no console clicks.
* **Checkov before deploy**: run on the template; exceptions recorded in `.checkov.yaml` with a reason (expected: security group egress open, IMDSv2 already required, EBS encrypted, no key pair).
* **Secrets**: only in Parameter Store SecureString under `/postiz/`; never in the template, git, logs or chat; the owner pastes them into Parameter Store, or says yes to gate G0 so Claude reads them from Railway straight into Parameter Store. Rotate `JWT_SECRET`? No (it would break stored tokens).
* **Network**: web port open only to the admin IP while testing; at cutover to Cloudflare's ranges (or 80/443 with Caddy). No SSH. Postgres, Redis and Temporal only on the Compose internal network, not published. Temporal UI/gRPC are not exposed.
* **Backups**: nightly EBS snapshots (7 kept) + nightly `pg_dump` of the Postiz and Temporal databases to S3 (30 days lifecycle) + one tested restore in the first week.
* **Confirm before deleting**: Railway is stopped first, archived (dump and uploads in S3) on a separate yes, and deleted only on a third yes.
* Docs after each phase: `docs/ARCHITECTURE.md`, `DECISIONS.md`, `RUNBOOK.md` updated; English only.

## 6. Ordered plan with gates

Legend: **[C]** Claude does it, **[U]** the owner must do it.

**Phase 0: decisions (no change)**
1. [U] Answer the open questions in section 8 (account, region, domain, size).
2. [U] Run `claude mcp list` and tell Claude the host of the Postiz MCP (not the key).
3. [U] Tell Claude whether to keep `postiz.margdarshakss.com` (strongly recommended).
4. [C] Confirm the image architecture (`docker manifest inspect ghcr.io/gitroomhq/postiz-app:v2.25.1`) and the exact Temporal database names (a read-only look at Railway). **Gate G0:** the owner says yes to Claude moving secrets Railway to Parameter Store, or does it themself.

**Phase 1: build the empty target (cost starts, about 32 a month)**
5. [C] Write `postiz-host/template.yaml` from `chatwoot-host/template.yaml`: VPC/subnet parameters, `AdminCidr`, `t4g.medium`, bucket, role (SSM, bucket, `/postiz/*` parameters), security group, EIP, encrypted data volume, DLM snapshots, recover alarm, Compose with `postgres:18`, `redis:8`, `temporalio/auto-setup:1.28.1` (same version as Railway), `ghcr.io/gitroomhq/postiz-app:v2.25.1` (same version), Caddy.
6. [C] Run Checkov and fix or document exceptions; `aws cloudformation validate-template`; create a **change set** and show it.
7. [U] Read the change set and the cost, answer **"Yes, execute"**. **Gate G1.**
8. [C] Execute; check that the stack is up and Postiz answers on the temporary name, admin IP only.

**Phase 2: replica and rehearsal (Railway untouched)**
9. [C] Put secrets in Parameter Store (after G0). Dump Postiz DB and Temporal DBs from Railway with a pg18 client (needs a temporary TCP proxy on the Railway Postgres: **a change on Railway**, needs the owner's yes and is removed afterwards); restore on AWS; copy uploads.
10. [C] Start with Temporal and workers paused so nothing publishes. Test login, channels list, media, and compare counts (users, channels, posts, media).
11. [U] Review in the browser on the temporary name. **Gate G2.**

**Phase 3: cutover (30 to 60 minutes, owner present)**
12. [U] A day before: lower DNS TTL; check that no posts are due in the window.
13. [C] Stop Railway Postiz and Temporal; final dumps and upload copy; restore; start all; verify.
14. [U] Change the DNS record `postiz.margdarshakss.com` to the new address (Cloudflare or wherever it is managed; Claude has no DNS access and should not hold a token).
15. [C] Verify through the Postiz MCP: `integrationList`, `postsListTool`, schedule and delete a test post, `mediaListTool`. **Gate G3** (owner approves).

**Phase 4: watch and clean up**
16. [C] Day 1 to 7: check snapshot and S3 dump exist, one test restore, an uptime check, a cost alarm. Railway stays stopped.
17. [U] Yes to archive; [C] last dump and uploads to S3. **Gate G4.** Separate yes to delete Railway services, volumes and the proxy. Never before.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Wrong dump tool version (PG 18 vs 16) | use postgres:18 client and server |
| `JWT_SECRET` not copied, all channel tokens unreadable | copy exactly; test one channel in the rehearsal |
| Temporal state lost, future scheduled posts not run | include its databases; compare pending post count before and after |
| Double publishing from two live copies | rehearsal with Temporal off; Railway stopped before AWS starts |
| Instance too small (Postiz at 2 GB limit) | t4g.medium minimum; add 2 GB swap; alarm on memory |
| Single machine, no failover | snapshots, S3 dumps, recover alarm, rebuild from template (about 1 hour) |
| Domain changes and social apps break | keep the domain |
| Meta/Instagram app tied to the old host | check in Meta dashboard after cutover |
| Platform later moves to the new account | build once in the final account (see Q1) |
| Image arm64 missing | use `t3.medium` |
| Postiz upgrades become the owner's job | pin versions; upgrade in a copy first |

## 8. Open questions for the owner

* **Q1.** Which account: the default account us-east-1 (as asked), or the new account `805751386990` that the platform migration targets? Building twice should be avoided. Region: us-east-1 as stated, or ap-south-1 (closer to India, cheaper instance)?
* **Q2.** Keep `postiz.margdarshakss.com` (recommended)? Where is its DNS (Cloudflare, like Chatwoot)?
* **Q3.** What is the Postiz MCP URL host in `claude mcp list` (no key)? Does it use `/mcp/<key>` or a header?
* **Q4.** How many posts are scheduled in the next 30 days, and are there recurring ones? How large is the media library really (5 GB volume, 0.78 GB used)?
* **Q5.** Is the Postiz AI copilot wanted (needs an `OPENAI_API_KEY`; the current timeouts are only the missing key)? Which channels are connected (Facebook, Instagram, YouTube, others)?
* **Q6.** Real Railway monthly bill, to confirm whether about 32 on AWS is acceptable.
* **Q7.** Gate G0: may Claude move the secrets from Railway into Parameter Store, or will the owner do it?
* **Q8.** Is the generic OAuth login (`POSTIZ_GENERIC_OAUTH`) still in use, and which provider is it?

## 9. Not verified

Railway's real bill, exact current AWS prices, image arm64 support, Temporal database names, the MCP URL format, number of scheduled posts, whether Postiz v2.25.1 needs new environment variables for S3 uploads (only matters for option C), and the real size of the database dump.

---

# Update 2026-11-24: owner decisions and the three critical points

Owner's answer: (1) build it in the **NEW account** (`805751386990`, terminal profile `new`), so nothing is built twice when the platform moves; (2) since the platform stack cannot host it, make a **direct transfer process Railway to the new account** (nothing is ever built in the old account); (3) give a **resource consumption estimate and a comparison with Railway**; (4, optional) think about which Postiz features could later be integrated into the teacher platform. Still PLAN ONLY: nothing was created or changed.

This section replaces the account question (Q1) and the "same account" wording above. Everything else in the note stays valid.

## 9. Point 1 and 2: direct transfer Railway to the new account

### 9.1 Principles
* **One hop, Railway to the new account.** The old account is never touched and is not a stepping stone. The only intermediate place is the new account's own S3 bucket.
* **Separate stack in the new account**, `postiz-host`, independent of `student-hosting`. When the platform itself moves later (`MIGRATION-AUDIT.md`), Postiz does not need to move again. It only shares the account, billing and the alert address.
* **Region: default `us-east-1`** (as the owner first asked; platform and Chatwoot adapter run there). Option, not blocking: `ap-south-1` (Mumbai) has a cheaper instance (about 8 dollars less a month) and is where the Chatwoot plan puts Chatwoot; if Chatwoot goes to Mumbai, put Postiz next to it. Postiz talks only to social networks, so latency is not a reason either way.
* Same versions at first (Postiz v2.25.1, Temporal 1.28.1, Postgres 18, Redis 8). Upgrades later, in a copy.

### 9.2 Pre-flight (read-only checks first, then the owner's yes)
| # | Check | Who | Why |
|---|---|---|---|
| P1 | `aws sts get-caller-identity --profile new` returns 805751386990 | Claude | right account before anything |
| P2 | default VPC and a public subnet exist in the chosen region; note the AZ | Claude (read) | the template takes VpcId, SubnetId, AZ |
| P3 | EC2 vCPU quota (a new account can start low; t4g.medium needs 2 vCPU), a billing alarm or budget exists | Claude (read) | avoid a quota error at deploy |
| P4 | image `ghcr.io/gitroomhq/postiz-app:v2.25.1` has `linux/arm64` (`docker manifest inspect`); else use `t3.medium` | Claude | Graviton is about 20 percent cheaper |
| P5 | exact names of the Postiz and Temporal databases, extensions in use, the real list of connected channels | Claude (read-only SQL over a temporary proxy, after the owner's yes) | to dump the right things |
| P6 | where `postiz.margdarshakss.com` DNS lives (Cloudflare?) and its TTL | Owner | the cutover DNS step is manual |
| P7 | Gate G0: secrets path (Claude moves them Railway to Parameter Store, or the owner does) | Owner | no secret in chat, git or files |

### 9.3 The transfer, in order (Claude [C], owner [U])
1. [C] Write `postiz-host/template.yaml` (copy of `chatwoot-host`: Compose with Postiz, Temporal, Postgres 18, Redis, Caddy; S3 prefixes for dumps and media; `/postiz/*` parameters). Run Checkov, validate, create the **change set**, show it with the monthly cost. [U] "Yes, execute". **Gate G1.** [C] Execute with profile `new`.
2. [C] Machine is up, Postiz answers on a temporary name, firewall open only to the owner's IP. Postgres, Redis and Temporal are published on no port.
3. [U] Gate G0: secrets into Parameter Store `/postiz/` (`JWT_SECRET`, DB password, social app ids and secrets, generic OAuth values). `JWT_SECRET` must be identical to Railway's.
4. [C] **Database dump, direct.** Open a temporary TCP proxy on Railway's Postgres (a change on Railway, needs the owner's yes, removed afterwards). Run `pg_dump` **with a PostgreSQL 18 client** for the Postiz and Temporal databases and send it **straight to the new account's S3 bucket** (or run `pg_dump` on the new machine through SSM, so the data never rests on the laptop). Expected well under 100 MB compressed (data is 0.28 GB).
5. [C] **Restore on the new machine** into `postgres:18`, same database names and user. Same Postiz version, so no migration step is needed.
6. [C] **Uploads.** The Railway MCP has no file download. Preferred route (verify in P5): Postiz serves `/uploads/...` publicly and the database lists every media path, so the new machine downloads each file over HTTPS from the live site (about 0.8 GB) into the data disk and checks counts and sizes against the media table (read-only, no Railway change). Fallbacks: a one-off shell copy by the owner, or the Railway MCP `fork-exec` if it can reach the volume (to be checked, not assumed).
7. [C] **Make the replica harmless, then start**: Temporal and the workers stay stopped until the checks pass; `RUN_CRON` and the orchestrator off; no scheduled post may be due; no social channel is touched. Start the web part only. Compare counts (users, organizations, channels, posts, media) with Railway.
8. [U] Look at the replica (login, channels list, media). [C] Check with the Postiz MCP pointed at the temporary name with the same API key. **Gate G2.**
9. [U] A day before: lower the DNS TTL; choose a window with no due posts.
10. [C] **Cutover (30 to 60 minutes):** stop Railway Postiz and Temporal (Postgres stays up for the final dump); final dump and restore over the replica; fetch only new media; start Temporal, workers, cron; verify.
11. [U] Change the DNS record `postiz.margdarshakss.com` to the new address (Claude holds no DNS token). Cloudflare "Full (strict)" with an origin certificate, or Caddy with Let's Encrypt on a direct record.
12. [C] Verify with the MCP and the UI: `integrationList`, `postsListTool` (same count), schedule and delete one test post, `mediaListTool`; check that Meta and YouTube tokens still work. **Gate G3.**
13. [C] Watch for 7 days (Railway stopped, not deleted): snapshots, nightly dump to S3, one test restore, uptime check, memory and disk alarms.
14. Archive and delete on Railway only on separate yeses (**G4**, then delete), as in section 6.

### 9.4 What changes for Claude Code
Nothing if the domain stays: the Postiz MCP URL and key keep working after DNS moves, because the key is in the restored database. During the rehearsal use a second MCP entry (temporary name, same key) and remove it afterwards.

### 9.5 Rollback
Before DNS: nothing lost; keep or delete the replica. After DNS: point the record back to Railway and start Railway's services; posts or changes made on AWS after the cutover are lost unless dumped back (acceptable inside the first hours).

## 10. Point 3: resource consumption and comparison with Railway

### 10.1 Measured on Railway (7 days of Railway metrics, to 2026-11-24)
| Service | RAM average (max) | CPU average (max) | Disk used / volume | Notes |
|---|---|---|---|---|
| Postiz | 1.81 GB (2.00, limit 2 GB) | 0.11 vCPU (3.6 burst) | 0.78 GB / 5 GB | nginx + PM2: backend, frontend, orchestrator, cron in one container |
| Temporal | 0.19 GB (0.19) | 0.011 vCPU (0.08) | none | limit 0.5 GB |
| Postgres 18 | 0.18 GB (0.24) | not read (about 0.01) | 0.28 GB / 5 GB | holds Postiz and Temporal data |
| Redis 8.2 | 0.007 GB | not read (about 0) | 0.09 GB / 5 GB | queue and cache |
| **Total** | **about 2.2 GB average, about 2.45 GB peak** | **about 0.13 vCPU average**, short bursts above 2 vCPU | **1.15 GB used of 15 GB provisioned** | network not measured, expected about 1 GB a month or less |
| HTTP | 4,243 requests in 7 days (about 600 a day), 23 server errors | | | tiny load |

### 10.2 What it needs on AWS
* **Memory is the sizing factor**: about 2.5 GB at the peak plus the operating system, Docker and Caddy (about 0.4 GB) = about 3 GB. A 4 GB machine is the minimum; add 2 GB swap as a safety.
* **CPU**: 2 vCPU burstable (t4g.medium) is enough; watch CPU credits in the first weeks.
* **Disk**: 20 GB root + 30 GB data volume is generous (1.15 GB used).
* **Network**: a few GB a month at most, inside the free outbound allowance.

### 10.3 Cost comparison, per month (USD, estimates)
| Item | Railway today | AWS new account, one EC2 (recommended) |
|---|---|---|
| Compute / memory | RAM 2.2 GB at about 10 per GB-month = about 22; CPU 0.13 vCPU at about 20 = about 3 | t4g.medium (4 GB, 2 vCPU): us-east-1 about 24.5; ap-south-1 about 16.4 |
| Storage | 15 GB volumes at about 0.15 = about 2.3 | 50 GB gp3 (20 root + 30 data) about 4.5 |
| Network and IP | egress about 0 to 1 | public IPv4 about 3.7; egress inside the free allowance |
| Backups | none you control | snapshots + S3 dumps about 1 |
| Plan fee | Hobby 5 (includes 5 of usage) or Pro 20 | none |
| **Total** | **about 26 to 30 plus plan fee (about 26 to 35)** | **about 34 in us-east-1; about 26 in ap-south-1** |

Honest reading: **the move is cost-neutral, not a saving.** Railway's rates (about 10 per GB RAM, 20 per vCPU, 0.15 per GB volume) are from memory and must be checked against the real invoice; AWS rates come from the 2026-11-20 pricing read and must be re-read before approval. What AWS buys: backups you control, no application sleeping (Postiz and Temporal have `sleepApplication` on, which can delay a scheduled publish), the same account and bill as the platform, IAM, a path to S3 for media later. What it costs: you patch and upgrade it. A 1-year Savings Plan later would cut the instance by about 30 to 40 percent (not now). Lightsail 4 GB is about 24 flat but sits outside CloudFormation.

## 11. Point 4 (optional, later): which Postiz features could join the teacher platform

Not needed for the move and not planned in detail. All ideas would call Postiz's public API (organisation API key) **from the backend only**, never from a student's browser.

| Idea | What it is | Fit and risk |
|---|---|---|
| **A. `class_social` helper** | a one-line helper like `class_ai`: a student's backend schedules a post or lists channels through the teacher's Postiz | One API key sees all channels, so a student could post to real accounts. Needs per-student isolation (a separate Postiz organization or channel set, an allowance, a review queue) and the usual per-student switch. Medium effort, real abuse risk. |
| **B. Agent tool in Studio** | a tool `schedule_post` with `needs_approval=True` (the platform's agent pattern already has human approval): the agent drafts, the client approves, then Postiz schedules | Good fit, reuses `class_agent` approval. Key in Secrets Manager per agent. Small to medium. |
| **C. Chatwoot + Postiz** | a hand-over note or frequent customer question becomes a draft post | Nice demo, low risk if draft only. Depends on B. |
| **D. Teacher's own use** | schedule class announcements and content with the MCP already connected | No platform work; works after the move. |
| **E. Media sharing** | Postiz media and the platform's media library (`MEDIA-LIBRARY-PLAN.md`) | Overlap risk; avoid duplicate storage. Later. |

Recommendation: after the move start with **D** (nothing to build) and **B** (small, reuses approval); do **A** only with a real isolation design. Postiz is single-tenant in practice, so a class use needs an explicit decision about who owns the channels.

## 12. Open questions now (replaces Q1)
* Region: default us-east-1, or ap-south-1 (cheaper; next to Chatwoot if that goes to Mumbai)?
* P6: where is the DNS and what is the TTL? P7 / G0: who moves the secrets?
* Is downloading the uploads over HTTPS from the live site acceptable (read-only, no Railway change)?
* Is anything scheduled in the next 30 days that cannot tolerate a one-hour gap?
* Optional ideas B and D: wanted later, yes or no?
