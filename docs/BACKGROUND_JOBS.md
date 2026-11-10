# Background jobs: how they work, in detail

A web request to a student's backend stops after **27 seconds**. Background jobs are for the work that takes longer: a chain of AI calls, summarising many notes, building a report, a big import. The page **starts** a job and **asks for the result later**; the student's own code runs it in a second function with a longer time limit.

This document explains the whole design: what the student and the teacher see, the parts and how they fit, every state and limit, the security model, what happens when something fails, what it costs, and how it was tested. The route and field tables are also in [API_REFERENCE.md](API_REFERENCE.md) ("Background jobs"); the specification entry is section 4.6b of [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md). The code is in [`lambda/jobs.py`](../lambda/jobs.py).

## 1. At a glance

| | |
|---|---|
| **Who switches it on** | the teacher, per student, in the student's panel (the **Background jobs** card under Backend). **Off for everyone by default.** The student needs the backend ON |
| **Limits the teacher sets** | jobs per day (default 20, at most 1,000), jobs at the same time (default 1, at most 5), minutes per job (default 5, at most 15) |
| **Fixed limits** | job input at most 64 KB; result at most 100 KB; a finished job is kept one day; at most 2 jobs at once on the whole platform |
| **What the page does** | `runJob('report', input)` (helper in `frontend/src/api.ts`), or `POST /api/_jobs/<name>` then `GET /api/_jobs/<id>` |
| **What the backend does** | handles `{"job": {"id", "name", "payload"}}` instead of a web request and **returns the result** (JSON) |
| **What runs it** | the student's own code, in a second function `sd-<slug>_job`, started and watched by a platform function (the **job runner**) |
| **What it costs** | a fraction of a cent for a 5-minute job (section 13) |

## 2. The student's view

**The page** starts a job and waits, with a progress message if it wants one:

```ts
import { runJob } from './api'

const summary = await runJob('summarise', { notes }, { onStatus: (job) => setMessage(job.status) })
```

`runJob` posts to `/api/_jobs/summarise`, polls `/api/_jobs/<id>` every 2 seconds, resolves with the result, and throws an `ApiError` whose message is safe to show when the job is refused, fails or takes longer than the page is willing to wait.

**The backend** handles the job in the same file as its web routes. Python (`backend/app.py`):

```python
def run_job(job):                      # job = {"id", "name", "payload"}
    if job["name"] == "summarise":
        return {"text": do_the_work(job["payload"])}   # what you return IS the result
    raise ValueError("No such job")

def handler(event, context):
    if "job" in event:                 # a background job, not a web request
        return run_job(event["job"])
    ...                                # the normal web routes
```

Node (`backend/index.mjs`) does the same with `runJob(job)` and `if (event.job)`.

**Rules of the contract**

- The result is JSON, at most **100 KB**. Bigger results go to class storage and the job returns a reference.
- The input is JSON, at most **64 KB**.
- A job is not a server: it ends when the function returns, and the next job starts from scratch.
- A job has the same environment variables, class AI and class storage settings as the web function.
- If the job crashes, the page sees only "the job raised an error". The details are in the logs (`npm run logs`, job lines are marked `[job]`).

**Locally** `npm run dev` runs jobs too (section 14).

## 3. The teacher's view

In the student's panel, under **Backend**, the **Background jobs** card has a switch and an **Edit limits** dialog (jobs a day, at the same time, minutes per job). The card shows today's usage (`3 / 20 today · 1 at a time`). The hint says when the student must publish the backend again (the job function is created from the student's latest code).

Switching it on for a student who already has a backend creates the job function at once, from the code of their last publish. Switching it off makes AWS itself refuse the job function. Switching the **backend** off also stops new jobs. Removing the backend deletes both functions but keeps the teacher's settings.

## 4. The parts

```
 Visitor's browser (the student's site)
        |  POST /api/_jobs/<name>      GET /api/_jobs/<id>
        v
 CloudFront  --/api/*-->  API Gateway  -->  GATEWAY function  (answers /_jobs itself)
                                                |   \
                       single-item reads/writes |    \ starts (asynchronously)
                                                v     v
                                         DYNAMODB   JOB RUNNER function  (platform, up to 15 min)
                                      (tokens table)       |  calls and waits (synchronously)
                                                ^          v
                                                |     JOB FUNCTION  sd-<slug>_job  (the student's code, the teacher's time limit)
                                                |          |
                                                +----------+  the runner writes done / failed + result
```

| Part | Where | What it does |
|---|---|---|
| **Gateway function** | `lambda/gateway.py` | The only way in. Answers the reserved path `/b/<slug>/_jobs...` itself (it never reaches the student's web function). Runs the same checks as for a web request (origin secret, slug, body size, backend ready, token active, teacher switch, per-visitor rate), then hands over to `jobs.handle` |
| **Jobs module** | `lambda/jobs.py` | All the logic: settings and limits, start, read, list, the job function lifecycle, the runner's work |
| **Job runner** | `lambda/job_runner.py` | A tiny platform function (15 minutes, 128 MB). The gateway starts it with `{slug, id}`; it calls the job function, waits, and records the outcome. **Asynchronous retries are off** |
| **Job function** | `sd-<slug>_job` | The student's own zip, deployed a second time with the teacher's time limit (up to 15 minutes), the same sandbox role and the same environment variables. The web function `sd-<slug>` is unchanged: it keeps its 10 s (25 s) limit |
| **Portal function** | `lambda/portal.py`, `backends.py` | Creates and updates the job function when the teacher switches jobs on and on every publish; refuses it at AWS when jobs are off; deletes it with the backend |
| **Tokens table** | DynamoDB | Holds the job items, the counters and the teacher's settings (section 7) |
| **Kit** | `starter/template` | The example job in both backends, `runJob()` in `api.ts`, the local dev servers, the rules for AI editors |
| **Teacher page** | `web/teacher.js` | The card, the switch and the limits dialog |

## 5. A job's life, step by step

```
page      gateway                          runner                      job function
 |  POST /_jobs/report {input}                                              |
 |-------->| checks: backend ready, token active, teacher switch, visitor rate
 |         | name ok? input <= 64 KB and valid JSON?
 |         | take a slot for this student, then one for the platform  (else 429 jobs_busy)
 |         | count one against today's jobs                            (else 429 jobs_allowance_exhausted)
 |         | write the job item: status "queued"; add its id to the student's recent list
 |         |---- invoke runner (asynchronously, no retries) ---->|
 |<--202 {id, status: queued}                                    |
 |                                                               | status -> "running" (started)
 |  GET /_jobs/<id>  (every 2 s)                                 |---- invoke (waits, up to the limit) ---->|
 |-------->| read the job item; if it is old and unfinished, show it failed         | runs the student's code
 |<--{status: running}                                           |<------------- the returned value ---------|
 |                                                               | checks: JSON? <= 100 KB? function error? timeout?
 |                                                               | status -> "done" + result, or "failed" + a plain message
 |                                                               | finished = now; the item expires one day later
 |                                                               | release the two slots
 |  GET /_jobs/<id>                                              |
 |<--{status: done, result: ...}
```

Notes on the order:

- The two slots are taken **before** the daily count, so a refusal for "busy" never uses up a job of the day, and a refusal for the daily limit gives the slots back.
- If starting the runner fails, the job is marked failed, the slots are released, and the page gets `502 jobs_unavailable`. The daily count is not refunded.
- Reading a job never starts anything; it only reads (and, for a stale job, corrects the record, section 6).

## 6. States and time

```
          start                runner starts              runner finishes
 (none) --------> queued ----------------------> running ----------------> done      (with "result")
                    |                               |  \
                    |                               |   +----------------> failed    (with a plain "error")
                    +-------------------------------+
                      nobody finished it for (minutes x 60 + 300) seconds:
                      shown, and recorded, as failed "The job did not finish"
```

| Moment | What is stored |
|---|---|
| Start | status `queued`, `created`, the input (as text), an expiry two days ahead |
| Runner starts | status `running`, `started` |
| End | status `done` with `result` (the JSON as text), or `failed` with `error`; `finished`; the expiry becomes **one day after the end** |
| Expiry | DynamoDB removes the item some time after the expiry; a job whose item is gone is "no such job" (404) |

A job can be stuck in `queued` or `running` only if the runner itself was stopped (for example the 15-minute ceiling of the runner, or an AWS fault). The next read shows it as failed, so a page never waits for ever. The slots such a runner held free themselves (section 9).

## 7. Data model

All items live in the existing tokens table; keys are single items, and nothing is ever scanned (section 10).

| Item (`pk`) | Fields | Notes |
|---|---|---|
| `job#<slug>#<id>` | `slug`, `id` (128 random bits as 32 hex characters), `name`, `status`, `created`, `started`, `finished`, `payload`, `result`, `error`, `ttl` | one per job |
| `joblist#<slug>` | `ids` (the latest 20, newest first), `ttl` | how the list route finds a student's jobs without a scan |
| `jobs#<slug>#<YYYY-MM-DD>` | `count`, `ttl` | jobs started today (UTC); expires a week after the day ends |
| `jobsrun#<slug>` | `count`, `updated_at` | jobs of this student running now (queued or running) |
| `jobsrun#global` | `count`, `updated_at` | jobs running now on the whole platform |
| the student's token item, `backend` map | `jobs_enabled`, `jobs_per_day`, `jobs_concurrent`, `jobs_minutes`, `job_function_name` | the teacher's settings and the name of the job function; they survive a backend removal |

## 8. The job function

**Why a second function and not the same one with a longer limit.** A Lambda function has **one** time limit, fixed when it is created. If the web function itself got 5 or 15 minutes, a runaway web request could run that long (the gateway stops waiting after 10 or 25 seconds, but the function would keep running and billing). With two functions the web function keeps its hard limit and only the job function is long. The cost of idle is zero for both.

**Name.** `sd-<slug>_job`. A slug can contain letters, digits and `-` but never `_`, so this name can never be the web function of another student (`sd-abc_job` versus `sd-abc-job`). The `sd-` prefix keeps it inside every permission that is already scoped to student functions.

**Lifecycle**

| When | What happens |
|---|---|
| Teacher switches jobs on, student already has a web function | the job function is created from the code of the student's last publish, with the teacher's time limit and a copy of the web function's environment |
| Teacher switches jobs on first, the student publishes later | the first publish creates both |
| Student publishes again | both functions get the new code |
| Teacher changes the minutes | the job function's time limit is updated |
| Student sets or removes an environment variable, or the teacher switches the class AI or class storage | the new environment is copied to the job function too (best effort; the three places that change the web function's environment all do it) |
| Teacher switches jobs off | AWS is told to allow zero concurrent runs of the job function; switching on lifts it |
| Student removes the backend | both functions and the job log group are deleted; the teacher's job settings are kept |
| Platform `destroy` | the job functions have the same role and prefix as web functions, so `destroy` finds and deletes them and their log groups |

The job function logs to its own group `/aws/lambda/sd-<slug>_job` (kept 7 days, like the web function's).

## 9. Limits and protection

| Limit | Value | Where it is enforced | What the page sees |
|---|---|---|---|
| Jobs a day | teacher, default 20 (0 to 1,000), UTC day | an atomic counter | `429 jobs_allowance_exhausted` with `resets_at` |
| Jobs at once, per student | teacher, default 1 (1 to 5) | a slot counter | `429 jobs_busy` |
| Jobs at once, whole platform | 2 (template setting `INFLIGHT_JOBS_GLOBAL`) | a slot counter | `429 jobs_busy` ("too many jobs on this platform") |
| Time per job | teacher, default 5 minutes (1 to 15) | the job function's own Lambda time limit, enforced by AWS | the job fails: "took longer than its limit of N minutes" |
| Input | 64 KB | the gateway | `413 payload_too_large` |
| Result | 100 KB | the runner | the job fails: "larger than 100 KB" |
| Job name | `[a-z0-9][a-z0-9_-]{0,39}` | the gateway | `400 bad_job_name` |
| Visitors | the same per-visitor limit as web requests | the gateway | `429 too_many_requests` |

**Why the platform-wide cap is 2.** A running job holds **two** Lambda executions (the runner waits while the job function works). A new AWS account may allow only **10 concurrent Lambda executions in total**, shared with the portal, the web functions and everything else. Two jobs at once leaves four executions busy at most. A teacher on an account with a higher quota can raise the template value.

**Slots that leak.** The slot counters are released when a job ends, even if it failed. If a runner is killed before it can release them, the counter is stuck above zero; the next start sees a counter untouched for longer than `minutes x 60 + 120` seconds (a job's own limit plus two minutes) and resets it. The web limiter uses 60 seconds; that would be wrong for jobs, which legitimately hold a slot for minutes.

**Asynchronous retries are off** for the runner. AWS retries a failed asynchronous invocation twice by default; for a student's job that would mean running it two or three times, and billing it.

## 10. Security model

What each role may do (nothing here was widened for jobs except the two rows marked new):

| Role | Jobs-related permissions |
|---|---|
| **Gateway** | read, write and update single items in the tokens table; invoke `sd-*` functions (the web functions) and, **new**, the job runner |
| **Job runner** (**new**) | read, write and update single items in the tokens table; invoke **only** functions named `sd-*_job`; write its own logs. It cannot invoke a web function and has no access to S3, the signing key or anything else |
| **Portal function** | unchanged: it already manages `sd-*` functions (create, update, delete, configuration, concurrency) and may pass the student sandbox role to Lambda only |
| **Student sandbox role** (both functions) | **logs only**, as before. The student's code cannot read the table, the secrets or the bucket |

Properties that matter:

1. **The student's code never writes the result.** Its role is logs-only; the runner writes what the function returned. A student cannot forge another job's result or read another student's.
2. **The student's error text is never passed on.** An exception message can contain a secret the student printed or an input value. The page sees "the job raised an error ... (npm run logs)"; the text stays in the student's own log. The gateway follows the same rule for web requests.
3. **A job id is a capability.** It is 128 random bits and is readable only through the site that started it (another site answers "no such job"). Visitors are anonymous, so whoever holds the id may read that job's result; do not put secrets in results.
4. **No table scan, by design.** The gateway and runner roles can read single items but cannot scan or query the table (a scan would read every token). That is why the job list is read from one `joblist#<slug>` item. A first version used a scan, worked in the in-memory test store and failed on real AWS with a permission error; a test now fails if `jobs.py` ever scans or queries.
5. **Visitors can use up the student's jobs.** Anyone who can reach the site can start jobs, which count against the student's daily and concurrent limits, exactly as visitors can use up the run allowance for web requests. The limits bound the cost; the teacher can lower them or switch jobs off.
6. **Everything is off by default** and refused at several levels: the teacher's switch in the gateway, `reserved concurrency = 0` on the job function at AWS, the backend switch, the token's state (an offline, expired or revoked site starts no job and a queued one fails).

## 11. What happens when something goes wrong

| What | What the page sees | Where to look |
|---|---|---|
| Jobs are off for this student | `403 jobs_off` | the teacher's card |
| Switched on but the student has not published since | `409 jobs_not_ready` | the student publishes the backend once more |
| Too many jobs at once | `429 jobs_busy` (retry in 5 s) | wait, or raise "at the same time" |
| Daily limit used | `429 jobs_allowance_exhausted` with `resets_at` | the teacher's card |
| The student's code raises an error | job `failed`: "The job raised an error. The details are in your backend logs (`npm run logs`)." | `npm run logs`, lines marked `[job]` |
| The job runs past the time limit | job `failed`: "took longer than its limit of N minutes" | `npm run logs --errors` (the report line says `Status: timeout`) |
| The result is not JSON or is over 100 KB | job `failed` with that reason | the job's own code |
| The job function is missing (removed or never created) | job `failed`: "The job function is missing. Publish your backend again." | publish again |
| The job function is busy or still updating | job `failed`: "busy or still updating. Please try again." | retry in a moment |
| The token expired or the site went offline while the job waited | job `failed`: "offline or expired" | the teacher extends the token |
| The runner could not be started | `502 jobs_unavailable`; the job is marked failed | the platform functions' logs |
| The runner was killed | the job shows `failed` after the grace period ("did not finish") | the runner's log group |

## 12. Logs and troubleshooting

- **Student:** `npm run logs` (or the editor's `logs` tool) merges the web function and the job function in time order, job lines marked `[job]`. `--errors` covers both. Locally the same prints appear in the terminal of `npm run dev`.
- **Platform maintainer:** the job runner has its own log group (it prints the id of a failed job and the kind of failure, never the student's error text); the gateway's group shows refusals and start failures.
- **A job is slow to start:** the job function may be cold (a second or two), and the runner starts asynchronously. A quick job took about 7 seconds end to end in the live tests, including two polls.
- **A list that fails with a permission error** means somebody introduced a scan; see point 4 of section 10.

## 13. Cost

Lambda bills memory times time. The runner (128 MB) waits for the whole job and the job function (256 MB) runs it, so each second of a job bills about 0.375 gigabyte-seconds. Measured on a real account: a **40-second test job billed about 40 seconds in each function, roughly two hundredths of a cent**. At that rate (approximate; check current AWS prices):

| Job | Cost |
|---|---|
| 40 seconds | about 0.02 cent |
| 5 minutes (the default limit) | about 0.18 cent |
| 15 minutes (the highest limit) | about 0.5 cent |
| A student at the defaults, every job running its full 5 minutes, 20 times a day | about 3.5 cents a day at the very most |

Idle costs nothing: no function runs and no capacity is reserved. A few DynamoDB writes per job are negligible. Calls the job makes to the class AI are counted against the student's AI cap as usual.

## 14. Local development

`npm run dev` emulates the same routes in both local servers (`tools/dev_server.py`, `tools/dev_server.mjs`), with the platform's answers:

| Same as on the platform | Different locally |
|---|---|
| The routes, statuses, names, 64 KB and 100 KB limits and the plain failure message | one job at a time, no daily limit |
| A job name checked the same way | the time limit is not enforced: a job that runs longer than 5 minutes prints a warning in the terminal |
| `run_job` / `runJob` is the same function the platform calls | a crash prints its full details in the terminal (on the platform they are in the logs) |
| The list and the status routes | jobs are forgotten when the server restarts |

## 15. How it was tested

- **Platform tests** (`tests/test_jobs.py`, 14 groups, run in every build): the teacher's validation and the off-by-default behaviour; creating the job function with the right time limit, role, tags and environment while the web function keeps 10 s; start, read, list and every result shape; failures that never show the student's error text; the daily, per-student and platform limits, including slots that heal; bad input; one student unable to read another's job; stale jobs and a runner called twice; switching off and on, environment copying, merged logs, removal; and the guard against table scans. The tests run the student's real Python handler.
- **Template tests:** the runner's permissions and the gateway's are checked (the gateway's set of actions did not grow).
- **Kit tests** (`tests/test_kit.mjs`): both local servers run the starter job, show a crash as a plain message, and refuse bad input.
- **Installer test:** `destroy` deletes job functions and their log groups.
- **On real AWS, with a real student** (before the release): a quick job; a 40-second job past the 25-second web limit; a crash and an oversized result with plain messages; the student's variables in the job function; the platform-wide cap (the third job refused, a new one accepted when two had finished, all slots back to zero); the expiry of the job items; the merged logs; and a job that never ends, stopped by AWS at the teacher's limit after 63 seconds with exactly 60 billed. The live run found one real bug (the scan), which the in-memory tests could not.

## 16. Decisions and what was rejected

| Question | Decision | Why |
|---|---|---|
| Run the student's job in the same function or a second one | **a second function** | the web function keeps its hard limit (section 8) |
| How the result gets back | **a platform runner writes it** | the student's role is logs-only; a callback API would need a new credential for the student's code and a new route to defend |
| Start the runner synchronously or not | **asynchronously, retries off** | the page must get its id at once; a retry would run the job twice |
| Show the job's error text to the visitor | **never** | it may contain a secret |
| List jobs by scanning | **never** | the gateway cannot scan, and should not be able to |
| Step Functions for every student | **not now** | students would write state machines (a second language), and a student-defined state machine runs under its own role. If workflows are added later, the platform should generate and own the definition from a simple list of steps, with the student's function as each step. Jobs are the layer under that |
| A dedicated server (EC2) | **not as a default** | it costs money while idle, shares a machine between students' code, and cannot be capped like a function. A teacher-approved exception at most |
| Raise the 15-minute ceiling | **no** | it is Lambda's own limit; longer work is a case for a workflow or a container, not for a longer job |

## 17. Where things are

| What | File |
|---|---|
| The logic | `lambda/jobs.py` |
| The runner | `lambda/job_runner.py` |
| Gateway route | `lambda/gateway.py` (`jobs.handle` after the per-visitor limit) |
| Settings, function lifecycle hooks | `lambda/backends.py` (`set_backend`, `publish`, `_write_env`, `remove`, `logs`), `lambda/ai.py` and `lambda/storage.py` (environment copy) |
| Infrastructure | `template.yaml` (`JobRunnerFunction`, the gateway's variables and permission) |
| Teacher card | `web/teacher.js` (`jobsFeature`, `toggleJobs`, `editJobs`) |
| Student kit | `starter/template/backend/app.py`, `starter/template/variants/node-backend/index.mjs`, `starter/template/frontend/src/api.ts`, `starter/template/tools/dev_server.py` and `.mjs`, `starter/rules/AGENTS.md`, `starter/template/INTERVIEW.md`, `student/portal-deploy.mjs` (`status`) |
| Tests | `tests/test_jobs.py`, `tests/fakes.py` (`FakeLambda.platform`), `tests/test_kit.mjs`, `tests/test_installer.mjs` |

## 18. Running it

- **Enable for a student:** their panel, **Background jobs** switch (the backend must be ON). Tell them to publish the backend once more.
- **Change the limits:** **Edit limits**. Lowering "minutes" updates the job function; it does not stop a job already running.
- **A student says a job is stuck:** read it with `GET /api/_jobs/<id>`; after the grace period it shows as failed. If slots seem stuck, they heal by themselves within the job's limit plus two minutes.
- **Stop all jobs for a student now:** switch **Background jobs** off (AWS refuses the job function at once) or the backend off.
- **Raise the platform-wide cap:** change `INFLIGHT_JOBS_GLOBAL` for the gateway in `template.yaml` and apply, only if the account's concurrency quota allows it.
- **Remove everything:** `destroy` deletes the job functions and the runner with the rest of the platform.

## 19. What this is the foundation for

Jobs are the first layer of a larger plan for long and heavy work: next a class knowledge service for search over documents (its search and embedding run in platform code, not in the student's function), then platform-owned workflows (fan-out, retries, pauses) built on the same runner and limits, then schedules. See the project's direction notes; each stage is tested on a real student before it is released.
