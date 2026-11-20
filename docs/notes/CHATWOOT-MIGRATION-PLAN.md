# Moving Chatwoot from Railway to AWS: workload, options, cost, plan

Status: PLAN ONLY. Nothing changed on Railway or AWS. Figures are measured on 2026-11-20 from Railway's own metrics (7 days) unless marked "estimate". **AWS prices were read from the AWS Pricing API on 2026-11-20 (on-demand, Linux)**; Railway's cost is an estimate from its metrics and published rates and must be compared with the real Railway bill.

## 1. What runs on Railway today (project "Chatwoot Inbox", one environment)
| Service | What | Size seen |
|---|---|---|
| Chatwoot | image `ghcr.io/railwayapp-templates/chatwoot:Community`, version **4.18.0** (from `/api`), web and Sidekiq in ONE container, 1 replica, region asia-southeast1 (Singapore), volume 500 MB at `/app/storage` (uploads), custom domain `chatwoot.margdarshakss.com` behind **Cloudflare**, "sleepApplication" ON | RAM ~0.93 GB (0.88 to 1.04), CPU ~0.006 vCPU average (max 0.012), disk 0.1 GB |
| Postgres | Railway template, 500 MB volume | RAM ~0.05 GB, data 0.13 GB, CPU ~0 |
| Valkey (Redis) | Railway template, 500 MB volume | RAM ~0.01 GB, data 0.05 GB |
Variables (names only): `SECRET_KEY_BASE`, `DATABASE_URL`, `REDIS_URL`, `FRONTEND_URL`, SMTP_* (7), `FB_*`/`IG_VERIFY_TOKEN`, `WHATSAPP_*` (app id/secret/config), `ACTIVE_STORAGE_SERVICE`, `MAILER_SENDER_EMAIL`, locale and env flags. No `ACTIVE_RECORD_ENCRYPTION_*` keys are set, so stored secrets are not encrypted with extra keys.

## 2. Traffic and workload (7 days)
* **HTTP requests to Chatwoot: 3,574 in 7 days** (about 510 a day, 21 an hour, 0.006 a second on average; the busiest 6-hour block had 612). Mix: 1,477 2xx, 956 3xx, 1,141 4xx (bursts of about 180 to 280 in a block: scans or a client polling, not users), **0 5xx**.
* Latency: p50 36 to 46 ms, p90 157 to 202 ms, p99 about 765 ms.
* Network: negligible (peak 0.06 GB out in one hour; about 1 GB a month).
* Conversation traffic through the bot adapter: tens of messages a day in testing.
* **Conclusion: the load is tiny.** CPU is idle; the one thing that sizes the machine is RAM: Rails plus Sidekiq use about 1 GB, so the box needs 2 GB at least and 4 GB to be comfortable. Disk needs under 1 GB today. A single small machine carries 10 to 20 times today's traffic.

## 3. Options on AWS
| Option | Parts | Estimated cost a month (USD) | Notes |
|---|---|---|---|
| **A. One EC2 (Graviton), Docker Compose** (recommended) | t4g.small (2 GB) or t4g.medium (4 GB), 30 GB gp3 EBS, public IPv4, daily EBS snapshots, nightly dump to S3, S3 for uploads, Cloudflare stays in front | **Mumbai: t4g.small about 16; t4g.medium about 24** (instance 8.2 or 16.4 at $0.0112 or $0.0224 an hour, EBS 2.7 at $0.0912 per GB-month, public IPv4 3.7 at $0.005 an hour, snapshots and S3 about 1). In us-east-1 the instances cost 12.3 and 24.5 | Same stack as today (Chatwoot, Postgres with pgvector, Redis); all data on one disk with snapshots; manual upgrades |
| **B. Lightsail** | 2 GB plan (2 vCPU, 60 GB SSD, static IP, 3 TB transfer included), same Docker Compose | **about 12** (4 GB plan about 24) | Simplest and cheapest; separate console from the rest of the AWS stack; snapshots are paid extra (small) |
| **C. Managed pieces** | EC2 or Fargate for Chatwoot (web 1 vCPU 2 GB and Sidekiq 0.5 vCPU 1 GB), RDS PostgreSQL db.t4g.micro, ElastiCache cache.t4g.micro, ALB | **about 100 to 110** (Fargate about 55, RDS about 15, ElastiCache about 12, ALB about 22) | Backups and failover handled by AWS; far more than this load needs; the upgrade path when you have many clients |
| D. EC2 plus RDS only | EC2 t4g.small plus RDS db.t4g.micro, Redis in a container | about 40 | A middle step if you want managed database backups |
**Today on Railway (estimate): about 11 to 16 a month** (RAM about 9, small volumes and network about 1, plus the plan fee). **So the move does not save money: on option A in Mumbai it costs about 0 to 5 dollars more a month with t4g.small and about 8 to 13 more with t4g.medium.** What it buys: one cloud and one bill, backups you control (snapshots, point-in-time copies), no "sleeping" application, the worker and Chatwoot in one network later, region choice (Mumbai) and IAM access control.

## 4. Region
Chatwoot's users and the WhatsApp customers are in India; Railway is in Singapore today. **ap-south-1 (Mumbai)** gives the lowest latency to people (a few ms to tens of ms). The bot adapter runs in us-east-1 and calls Chatwoot three times per answer (about 200 to 250 ms each across the ocean): fine today, and a later move of the adapter to Mumbai removes it. **Mumbai is cheaper than us-east-1** for the instance (t4g.small $0.0112 against $0.0168 an hour, t4g.medium $0.0224 against $0.0336), as read from the AWS Pricing API.

## 5. The plan (option A; Lightsail follows the same steps)
**Phase 0: decisions** (section 7). **Phase 1: build the target** (about 1 day): one CloudFormation template (`chatwoot-host.yaml`, own stack, not inside the platform stack): a security group (HTTPS only, from Cloudflare's address ranges; no SSH, access through Systems Manager), an instance role (SSM, S3 for uploads and dumps), the EC2 instance, a separate encrypted EBS data volume (so the machine can be replaced without losing data), an S3 bucket for uploads and database dumps (private, lifecycle 30 days for dumps), daily EBS snapshots (7 to 14 kept), and alarms (instance status check, disk above 80 percent, memory) to the same alert address. On the machine: Docker Compose with the **same Chatwoot version 4.18.0** (an official `chatwoot/chatwoot` image of that tag after checking the Railway template's image is the same code), Postgres with pgvector, Redis, and Caddy for HTTPS (Cloudflare "Full strict" with an origin certificate), data directories on the data volume, `restart: always`.
**Phase 2: dress rehearsal** (about half a day): copy the database and uploads, start everything on a temporary name (`chatwoot-new.margdarshakss.com`, not the real one), log in, open conversations and attachments, send a message through the bot (our adapter keeps the same Chatwoot address, so only a temporary test agent is needed), check the sweeper and the private note. Time the dump and restore (a 0.13 GB database takes a minute or two). Nothing customer-facing changes.
**Phase 3: cutover** (one night, 30 to 60 minutes of downtime): lower the DNS TTL a day before; announce a short window; stop Railway's Chatwoot (so nothing writes), wait until the Sidekiq queue is empty, take the final database dump and copy uploads, restore on AWS, start, point Cloudflare's record at the new origin, check login, one WhatsApp message each way, one bot answer and one hand-over. **WhatsApp (Meta), Facebook and Instagram retry failed webhooks for a while, so a short gap loses no customer message**, but check in Meta's dashboard that delivery resumes. Keep Railway stopped (not deleted) for 7 days as the way back; note that after customers write on AWS, going back loses those new messages.
**Phase 4: after** (the first week): confirm snapshots and the nightly dump to S3 exist and that a restore works (test once); an uptime check on `/api`; a cost alarm; a calendar note for upgrades (Chatwoot releases monthly; upgrade in a rehearsal copy first); then delete Railway after the week.
**What must be carried over exactly:** `SECRET_KEY_BASE` (sessions and tokens), all `SMTP_*`, `FB_*`/`IG_*`, `WHATSAPP_*`, `FRONTEND_URL` (same domain). Bot tokens and secrets are in the database. Our adapter needs **no change** if the domain stays `chatwoot.margdarshakss.com` (its `ChatwootBaseUrl` stays; its user-agent fix for Cloudflare stays).

## 6. Risks
* The Railway template's image is not the official one: check its version and any patches before choosing the target image.
* Postgres on Railway must be reachable for the dump (a temporary TCP proxy on that service, removed afterwards; no password in chat).
* Upgrades and security patches become your job (or move to option C later).
* One machine is a single point of failure: mitigated by snapshots and a rebuild from the template in under an hour, not by failover.
* Cloudflare "Full strict" certificate and the Meta webhook verification token must be checked at cutover.
* If the platform itself moves to a new AWS account (see MIGRATION-AUDIT.md), build Chatwoot straight into that account and region once, not twice.

## 7. Decisions needed
| # | Question | A (recommended) | B | C |
|---|---|---|---|---|
| D1 | Which AWS account and region | the NEW account `805751386990` (terminal profile `new`; the platform is moving there too), ap-south-1 | the current account, us-east-1 | the current account, ap-south-1 |
| D2 | Option | A: EC2 plus Docker Compose | B: Lightsail (cheapest) | C: managed (when many clients) |
| D3 | Size | t4g.medium (4 GB): about 24 a month in Mumbai, room to grow | t4g.small (2 GB): about 16, tight (Chatwoot uses about 1 GB, plus the system, Postgres and Redis) | |
| D4 | In front | Cloudflare stays (free) plus Caddy | AWS ALB (about 22 more) | |
| D5 | Uploads | S3 (small, cheap, survives the machine) | stay on the disk | |
| D6 | Cutover night | a night with the fewest WhatsApp messages (check Chatwoot reports) | | |

## 8. Not verified yet
Railway's real bill, the exact AWS prices in the target region, the Railway image's relation to the official image, how many attachments exist and their size (the volume shows 0.1 GB used), and whether Meta needs the webhook re-verified after the move.

## 9. Which tools do the work (no AWS MCP needed)
Checked in this session on 2026-11-20:
| Need | Tool | State |
|---|---|---|
| Read and change Railway (services, variables, logs, metrics, a temporary TCP proxy, stopping a service) | the Railway MCP | connected; used already |
| Build and run everything on AWS (CloudFormation, EC2, S3, SSM run-command, snapshots) | the **AWS CLI in the terminal**, profile `new` (account 805751386990, user superadmin, default region us-east-1; pass `--region ap-south-1`) | works (identity read) |
| Dump and restore the database, rehearse locally | Docker 29 and PostgreSQL client 18 on this computer; the target and the Railway database both use the image `pgvector/pgvector:pg16`, so a dump taken with the pg16 image restores cleanly | present |
| Run commands on the new machine | `aws ssm send-command` (no Session Manager plugin needed); instance role with SSM | to be created in phase 1 |
| Change the DNS record at Cloudflare | **you** (no Cloudflare access from here, and I should not hold a Cloudflare token) | manual, one step at cutover |
| Meta (WhatsApp, Facebook, Instagram) webhook re-verification, if asked | **you** | manual, if needed |
The **AWS MCP is not needed**: it runs boto3 in a sandbox and cannot run Docker, ssh or local files; the terminal can do everything and is already signed in. It could only help for reading AWS documentation.
**Secrets.** Moving Chatwoot needs `SECRET_KEY_BASE`, the database password, SMTP and the Facebook, Instagram and WhatsApp app values. Reading them through the Railway MCP puts the values in this session. They are never printed or written to a file in git; they go straight into AWS Systems Manager Parameter Store (SecureString) and the machine reads them from there. **Gate G0: you say yes to that, or you copy those values yourself into Parameter Store.**

## 10. The order, replica first, with approval gates
**R1 Build the empty target** in the new account (nothing copied yet). Checkpoint: the template, the machine is up, Chatwoot answers on a temporary name behind a firewall that admits only your IP. Cost starts here (about 24 a month).
**R2 Make the replica** (Railway untouched and still serving): count the uploads first (the database knows them) to choose how to copy them; dump the Railway database over its existing TCP proxy with the pg16 image from this computer, restore it on the machine; copy uploads; put the secrets in Parameter Store. **Make the replica harmless before it starts:** the copy of a live database would otherwise act on live things. So, before Chatwoot boots: blank the agent bot's outgoing URL and the account webhook (the n8n address), start with no SMTP settings (no e-mails), and start Sidekiq only after that. Test only in a new internal test inbox (an API channel), never by replying in a real WhatsApp conversation (the replica holds the real WhatsApp token and would send to real customers).
**R3 Check the replica** with you: login, conversations and attachments look the same, counts of conversations, messages, contacts and users match Railway's, the bot answers from a test conversation, a hand-over note appears. **Gate G1: you approve the replica.**
**R4 Cutover** (a night, 30 to 60 minutes): lower the DNS TTL the day before; stop Railway's Chatwoot, wait until its queue is empty; final dump and restore on AWS (this replaces the replica's data); restore the real bot URL and webhooks and SMTP; start; you change the Cloudflare record to the new address; check one message each way and one bot answer. **Gate G2: you approve after the check.**
**R5 Watch** for 7 days: Railway stays stopped, not deleted (the way back is the DNS record; messages created on AWS after cutover would not be on Railway); confirm snapshots and the nightly dump to S3 work and do one test restore.
**R6 Archive Railway** (on your yes): take a last database dump and a copy of the uploads and keep them in S3 (the archive), stop the three services. **Gate G3.**
**R7 Delete on Railway** (on a separate yes, after the archive is checked): remove the services, volumes and the temporary TCP proxies. Never before.
At each gate I stop and report, as in the earlier sprints.
