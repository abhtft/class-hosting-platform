# Design: long-running work for student backends ("jobs"), instead of raw Step Functions

Status: proposal, nothing coded. Private (.temp is git-ignored).

## The problem it solves

A student's backend answers a web request. Today: 10 seconds per request (25 with the class AI), and the API in front of it gives up at 29-30 seconds
no matter what. So these cannot be built: an AI pipeline with several calls in a row, "summarise these 20 notes", generating a report or a
long story, importing a big file, anything that takes a minute. The student (or the AI editor) hits "Task timed out" or a 503.

## Why not Step Functions first

- Students would have to learn state machines (Amazon States Language), and the AI editor would write them: more to get wrong, harder to check.
- Needs new permissions for student-controlled definitions (a state machine can call other AWS services with its own role): a bigger review.
- 90% of the need is "run this function for longer and let me ask for the result later".

## The idea: start a job, ask for the result

Student code stays in the same function. Two small additions:

1. **Start:** the page calls `POST /api/_jobs/<name>` (or the backend calls `jobs.start("report", payload)`). The platform answers at once with `{ id, status: "queued" }`.
2. **Run:** the platform invokes the student's own function again, asynchronously, with `{ "job": { id, name, payload } }`, and a longer timeout (up to 5 minutes) for job invocations only.
3. **Ask:** the page calls `GET /api/_jobs/<id>` every few seconds and gets `queued | running | done | failed` with the result (or a short error).
4. The student function handles jobs with one extra branch: `if event.get("job"): return do_work(event["job"]["payload"])`, and the helper `class_jobs` saves the result (progress text too).

## Pieces

| Piece | What | Notes |
|---|---|---|
| Gateway | New reserved routes `_jobs` in `lambda/gateway.py` | Never reach the student's function as normal requests |
| Invoke | `lambda:InvokeFunction` with `InvocationType=Event` on `sd-*` | The gateway role already invokes `sd-*`; adding the async type needs no new permission |
| Timeout | `update_function_configuration(Timeout=300)` when jobs are on | Replaces 10/25 s for that student's function; a normal request is still cut at 10/25 s by the gateway |
| State | One DynamoDB item per job in the existing tokens table: `job#<slug>#<id>`, TTL 1 day | Status, result (max ~100 KB), error, timestamps |
| Helper | `class_jobs.py` / `class_jobs.mjs` in the kit (same pattern as `class_storage`) | `jobs.set_progress(text)`, returns `result`; reads the job id from the event |
| Teacher | New feature switch **Jobs**, default OFF, per student; limits: jobs per day (default 20), running at once (default 1), max minutes (default 5) | Same shape as the backend runs allowance |
| Portal | A small "Jobs" line on the student's Backend card (last jobs, status) | Optional in the first version |
| Rules / interview | AGENTS.md section "When a request takes longer than 25 seconds"; INTERVIEW.md asks "does anything take more than a few seconds?" | |

## Cost and safety

- Cost: Lambda time only (a 5-minute job at 256 MB is about $0.001) plus a few DynamoDB writes. The daily jobs cap and 1 running job per student bound it; account-wide concurrency stays protected by the existing in-flight limiter.
- Isolation unchanged: the job runs inside the student's own sandbox role (logs only), same as a request. No new IAM for the student role.
- Results are private to the student (the status route checks the student's session and slug, like storage).
- Abuse: a student cannot start jobs while the feature is off, the token is not active, or the cap is reached; a runaway job ends at the 5-minute timeout.
- Failure: a job that crashes gets `failed` with the first line of the error (full text in the logs, readable with `npm run logs`).

## What I would build, in order (about 2 days)

1. Gateway routes + DynamoDB job items + limits + teacher switch, with fake-AWS tests (the established pattern).
2. `class_jobs` helpers (Python and Node) and a template route in the kit that shows start/poll end to end; kit test in the dev portal.
3. Rules, interview question, docs, teacher card.
4. Real-AWS check on the throwaway account (async invoke, the 300 s timeout, TTL cleanup, what `destroy` removes).

## Decisions needed

1. Build "jobs" (recommended) rather than Step Functions? Step Functions stays possible later for teacher-written workflows.
2. Default limits: 20 jobs per day, 1 at a time, 5 minutes. OK?
3. Result size cap 100 KB (bigger results should go to class storage files). OK?

## Not recommended now

- A 24/7 EC2 instance: it breaks the idle-cost-near-zero promise and puts student code on a shared machine. Jobs and the existing storage cover the real needs (long work, saved data). Revisit only for something that truly needs a process that never stops.


---

# FINAL DESIGN (2026-11-01, after reading the real code; supersedes the table above where they differ)

Roadmap context: Jobs (this) -> class vectors -> platform-owned workflows (Step Functions behind the scenes) -> schedules -> heavy lane (Fargate) -> EC2 only as a teacher exception.

## Decisions and why

1. **A separate function per student for jobs: `sd-<slug>_job`** (same zip, longer timeout). The web function keeps its hard 10/25 s limit; a job never relaxes it. The underscore cannot occur in a slug, so `sd-abc_job` can never collide with the web function of a student called `abc-job`. The `sd-*` IAM patterns and destroy already cover it.
2. **A platform runner Lambda (`JobRunnerFunction`) waits for the job function** and writes the outcome to DynamoDB. The student's role is logs-only, so the student's code cannot write results; its return value IS the result. No callback API, no new student permission.
3. **Async invoke of the runner with retries = 0** (EventInvokeConfig), otherwise Lambda would re-run a failed job twice.
4. **Student errors are never shown to the visitor** (they may hold secrets): a failed job says "failed, details are in your backend logs" or "took longer than N minutes". Same rule the gateway already follows.
5. **Limits (teacher, per student, off by default):** jobs per day 20 (max 1000), at once 1 (max 5), minutes 5 (max 15), result 100 KB, payload 64 KB. A global cap on jobs at once protects the account (new accounts may have only 10 concurrent Lambda executions).
6. **Slots held for minutes:** the in-flight counters heal after minutes*60+120 s (the web limiter heals after 60 s, which is wrong for jobs).
7. **Contract for students:** the page calls `POST /api/_jobs/<name>` (JSON body) -> 202 {id, status}; `GET /api/_jobs/<id>` -> {status: queued|running|done|failed, result|error}; `GET /api/_jobs` -> the last jobs. The student's `handler(event)` gets `{"job": {"id","name","payload"}}` and returns the result (JSON). The reserved `_jobs` path never reaches the web function.
8. **Job ids are 128-bit random; the id is the capability** (visitors are anonymous). Items expire (TTL) a day after they finish.
9. Logs: `npm run logs` reads both groups and marks job lines.

## Stages
1. Core: settings and limits, job items, start/status/list, runner, gateway routes, job-function lifecycle, template (runner + IAM + EventInvokeConfig), tests with fakes.
2. Real AWS on test_claude: async invoke, the long timeout, TTL, logs, destroy.
3. Kit and teacher: starter route + frontend helper, local dev server emulation (Python and Node), rules, interview question, docs, teacher card, Usage tab numbers.
