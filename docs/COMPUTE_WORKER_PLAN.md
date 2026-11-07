# Compute worker plan: one small scheduled machine for heavy checks

> **Status (2026-11-07): the machine is built and live, and the design is deliberately SMALL.** The machine, the Worker tab (Start, Stop, Start and keep on, daily schedule, time limit, monthly cap, last report), the watchdog and the installed software work on real AWS. Student access, a job queue and running students' own code were designed, partly built, then **deliberately removed** as over-engineered (section 11). What is next is one simple task mechanism for the teacher's own checks (section 12), after the benchmark. The plan has the owner's decisions recorded (section 9). It needs the usual shown-and-approved `plan` before any `apply`, and the benchmark (W0) comes first. It supports the quality work in [RAG_ROADMAP.md](RAG_ROADMAP.md) and reads the traces described in [TRACEABILITY_PLAN.md](TRACEABILITY_PLAN.md).

## 1. The goal in one sentence

A **single small server that is off by default**, that the teacher can **switch on and off from the teacher page**, and that also **wakes itself once a day for about 30 minutes** to run the heavy quality checks (benchmark, judging, attack tests) that do not fit in a web request, then goes back to sleep.

## 2. Why this and not something else

| Choice | Decision | Reason |
|---|---|---|
| Always-on server | **No** | After the 6-month credits it would cost every month, and it is one more thing to secure |
| Lambda for everything | **Not for these tasks** | Fine for short jobs (the nightly numbers already use it), but the heavy open-source test tools and runs over 15 minutes need a real machine |
| Scheduled container (Fargate) | **Possible later** | Same cost, no disk, but needs a container image to build and keep |
| **One EC2 instance, stopped when idle** | **Yes** | Simple, cheap per hour, the tools install as on a laptop, and the toggle is just start and stop |

## 3. The machine (us-east-1)

| Item | Choice |
|---|---|
| Type | **m7i-flex.large** (2 x86 vCPUs, 8 GB). Chosen because the account is on the AWS **Free plan**, which allows only free-tier instance types (found when the first apply of `c7g.xlarge` was refused and rolled back). On a paid-plan account the ARM sizes (`c7g.xlarge`, 4 vCPUs, 8 GB) can be chosen instead. The image (x86 or ARM) follows the type |
| Disk | **30 GB gp3, encrypted**. Kept while the instance is stopped (about $2.40 a month) |
| Buying | **On-demand**, no reservation (a reservation is 1 to 3 years and bills while stopped) |
| Network | **No inbound port at all.** No SSH. The teacher's terminal reaches it through AWS Systems Manager. A public IPv4 address only while it runs (about $0.005 an hour) so it can reach package servers and AI providers |
| Security settings | IMDSv2 only, encrypted disk, no key pair, a security group with no inbound rules |
| Software | Amazon Linux 2023 (ARM) from the public image parameter; Python 3.12, Node 22, and the test tools installed once from a start-up script kept in this repository |

**Rule: only platform-owned code runs on this machine. Student code never does** (section 11 says why), and the machine holds no secrets or database access.

## 4. The toggle on the teacher page

A new **Compute worker** card (installed only when the feature is on, like the other connectors):

- State: **Off, Starting, Running, Stopping**, with the time it started and how long it has run today.
- Buttons: **Start now** and **Stop now**.
- Switch: **Run every day at [hour]** (on or off). Off means it only runs when you press Start.
- Tick-boxes for **which tasks run** (section 7).
- Last run: when, how long, the result (passed, failed, a number), and a link to the report file.
- This month: hours used and an **estimated cost** (hours times the price, plus the disk).

How it works underneath (all in your account):
1. The portal function may **start and stop only this one instance** (permission limited to its ARN) and read its state. It cannot create, change or delete any other server.
2. A small **control function** runs on a schedule. At the chosen hour it starts the instance if the daily switch is on. Every 15 minutes it also acts as a **watchdog**: if the instance has been running longer than the limit (default 60 minutes, 3 hours for a heavy run), it **stops it**. So a hung job cannot run all month.
3. On every start the instance runs the task runner, writes results to S3, and **shuts itself down** when finished.

## 5. What it can reach (least permission)

| Can | Cannot |
|---|---|
| Read and write the **`worker/` folder** of the sites bucket (task code, settings, reports) | The student sites, the kit, the portal pages or other folders |
| Read the **one AI secret** (the Google and OpenRouter keys) | Any other secret |
| Read traces (query the trace table) when a task needs yesterday's answers | Write or delete traces |
| Call Bedrock models (judge, embeddings) once enabled, with a spending cap | Anything else in the account |
| Talk to the internet (outbound) | Accept any inbound connection |

## 6. Cost

| Part | Estimate |
|---|---|
| Compute, 30 minutes a day (about 15 hours a month) at about $0.096 an hour (m7i-flex.large) | about $1.50 |
| Disk, 30 GB gp3 | about $2.40 |
| Public IPv4 while running | under $0.10 |
| **Machine total** | **about $4 a month** |
| Model calls made by the tasks (judge, embeddings) | separate, capped per task; a few dollars |

Prices are from memory of AWS's list: **verify them in the AWS pricing page before the apply.** The 30 GB disk is only free if the account is still inside the 12-month free-tier terms; with the trial credits it is covered either way.

## 7. The tasks (what we would run, and when)

| # | Task | When | Why it needs the machine | Output |
|---|---|---|---|---|
| 1 | **Golden probes**: ask a fixed set of benchmark questions through the real answer path and measure refusals, wrong answers and cost | Daily, about 10 minutes | The benchmark runner and its question sets | One line per day: the numbers; an **alert email** when quality drops against the last 7 days |
| 2 | **Judge the flagged answers**: take yesterday's refusals and low-confidence answers from the traces, ask a stronger model whether each was right, and mark the trace | Daily, about 10 minutes, capped at a number of answers | Calls a large model with a budget cap | Marks on traces, a short list for the teacher's Review tab |
| 3 | **Attack tests** (injection, prompt extraction, personal data, English, Hindi and Hinglish) | Weekly, 1 to 2 hours | Heavy open-source red-team tools | A report and the attack success rate |
| 4 | **Retrieval check after a change**: recall and ranking on the benchmark when a shelf, a threshold or a model changes | On demand (button) | Full benchmark run | A before and after table |
| 5 | **Hindi and English embedding comparison** | Once, during the trial credits | Runs many models over the benchmark | A table to choose the embedding model |

Tasks 1 and 2 are the daily ones. 3 and 4 and 5 use the longer limit. Anything that finishes in a few minutes and needs no tools (for example counting refusals) stays in the existing nightly Lambda job.

## 8. The roadmap (each step works alone)

| Step | Deliverable | Done when |
|---|---|---|
| W0 | This plan approved; the benchmark pack and runner exist (roadmap phase 0), because tasks 1 to 5 all need them | The owner approves the benchmark list; a baseline report exists |
| W1 | The instance, its disk, role and start-up script; start and stop from the terminal; the watchdog | `plan` shown and approved; a run starts, writes a report to S3 and stops itself; a hung run is stopped by the watchdog in a test |
| W2 | The teacher card (state, Start, Stop, daily switch, hour, last run, cost) | The toggle works from the teacher page on the live platform |
| W3 | Tasks 1 and 2, with the alert email | A deliberate quality drop produces an alert |
| W4 | Tasks 3, 4 and 5 | A weekly report exists and the owner reads it |

## 9. Owner decisions (2026-11-07)

| Question | Decision |
|---|---|
| What the toggle does | **Stop and start the same instance** (tools stay installed, disk kept) |
| When it runs | **Both:** a Start now button, plus an optional daily schedule switched on later |
| Tasks first | **1 (daily golden probes) and 2 (judging flagged answers)**; the weekly attack tests and the Hindi comparison come after |
| Judge model | **Bedrock**, so the trial credits pay for it (model, price and credit cover verified first) |
| Spot or on-demand | Not asked: **on-demand** for the daily runs; Spot only if the long weekly run is added |
| Machine | **m7i-flex.large** (changed 2026-11-07 from c7g.xlarge because the Free plan refused it; owner chose this), 30 GB gp3, us-east-1 |

Still to confirm before W1: the AWS free-plan limits of the account (some free-plan accounts restrict services until upgraded), the credit expiry date, and the current prices.

## 10. What we will not do

- No always-on server, no reservation, no open ports, no key pair.
- No student code on this machine (section 11), no secrets or database access on it, and no student data copied out of the account.
- No task without a cost cap, and no apply without a shown and approved plan.

## 11. What we decided NOT to build (and why)

The first version of this plan grew past what the platform needs. After building the machine, a per-student "access level" and a job queue, the owner and the maintainer agreed to **remove them**:

| Idea | What was done | Why it was dropped |
|---|---|---|
| Per-student access levels (off, reports, platform tasks, own code) | Built the setting and a card, then **removed** | It was a settings screen for features that did not exist; no level had any effect |
| A student job queue (upload a zip, queue, results) | Built and tested, never applied, then **removed** | A queue with nothing that runs the jobs |
| Running a student's own code on the machine in containers | **Not built** | It would make the teacher responsible for running untrusted code next to class tools (container break-out is a real risk), and the platform **already has a safe answer**: *Background Jobs* runs a student's own function for up to 15 minutes in the same sandbox as their backend |
| A second machine, a job runner, a student kit command | **Not built** | Only needed for the idea above |

If a real need for heavy student computing appears later, the answer is to start from that need (which student, what job, how long) and consider a **separate machine for student code**, not to extend this one. The removed designs are in the repository history (commits `2cda3c3` and `baa7a06`).

## 12. The simple model that is next

**The worker is the teacher's own batch machine.** Nobody else can reach it.

1. **Machine:** as built (section 3). Off by default; the teacher starts and stops it, or lets it start once a day; a watchdog stops it past its time limit; a monthly cap on machine hours stops a start when reached.
2. **Tasks:** a task is **one small Python file in the repository** (`worker/tasks/<name>.py`) with a name, a one-line description, a time limit and a cost cap. The runner runs the tasks the teacher has ticked, one after another, and writes one result per task. A new task shipped in a release **never runs until the teacher ticks it**.
3. **The tick-boxes** live on the Worker tab, next to the schedule. Nothing else to configure.
4. **Results:** the Worker tab shows the latest result of each task; a failed or alarming result sends the **existing alert email**.
5. **Permissions:** the machine reaches only the `worker/` folder (and, when a judging task is ticked, the Bedrock call it needs, with a spending cap). It holds no secrets and no database access; anything a task needs from the platform is **staged into `worker/inputs/` by the platform before the run** and read back from `worker/outputs/` afterwards.
6. **Students:** no access. A teacher may share a result with a student by hand (copy of the report). Heavy student work uses Background Jobs.

**First tasks (teacher-only), after the benchmark exists:** daily golden probes (fixed questions through the real answer path; alert when quality drops) and judging of flagged answers (a stronger model checks yesterday's refusals and low-confidence answers).

## 13. Configuration reference (what exists, and what is added next)

| Setting | Where | Default | Needs an apply? | State |
|---|---|---|---|---|
| Worker installed at all | Installer feature `worker` | **Off**, never part of "install everything" | Yes (plan shown first) | Built |
| Machine type | `WorkerInstanceType` | `m7i-flex.large` (2 vCPUs, 8 GB, x86); ARM sizes for paid-plan accounts | Yes; **changing it replaces the machine** | Built |
| Disk size | `WorkerDiskGb` | 30 GB (20 to 200) | Yes; growing is safe, shrinking replaces the machine | Built |
| Region, network, access | us-east-1, default VPC, no inbound rule, Systems Manager only | Fixed | n/a | Built |
| Start now, Start and keep on, Stop now | Worker tab | n/a | No | Built |
| Daily start on or off, and the hour (UTC) | Worker tab | Off, 03:00 UTC | No | Built |
| Time limit per start | Worker tab | 60 minutes (15 to 360) | No | Built |
| Monthly cap on machine hours | Worker tab | 40 hours | No | Built |
| What the runner does | `worker/runner.py` in the bucket | Installs git, Node.js, Python 3.12 and build tools once, then reports | Published with the portal | Built |
| Which tasks run | Tick-boxes on the Worker tab | None | No | **Next** |
| Cost cap per task | Task file | A small default | No | **Next** |
| Alert email on a failed or alarming result | existing alert email | Used if the platform has one | No | **Next** |
| One-run-per-machine mode (no disk kept) | `WorkerEnabled` variant | Off | Yes | Possible later |

## 14. Safety controls (as built)

| Control | State |
|---|---|
| Off by default; started only by the teacher or the schedule | Built |
| Watchdog stops a machine without a start request or past its limit | Built, and seen running on real AWS |
| No inbound access, no key pair, IMDSv2 only, encrypted disk | Built |
| Role reaches only the `worker/` folder; only the portal and the control function can start and stop that one machine | Built (guarded by a template test) |
| Monthly cap on machine hours | Built |
| Alert email, per-task caps, signed runner | Next or later |

## 15. Build order (small)

| Step | Deliverable | Done when |
|---|---|---|
| W3 | Simplify: remove the student access setting, the job queue and their routes, tests and permission (this change) | The platform has no student-facing worker code; the tests pass; the live platform matches |
| W4 | The task mechanism (section 12): task files, tick-boxes, one result per task, alert email on failure | A test task can be ticked and its result shown; an unticked task never runs |
| W5 | Golden probes and judging, after the benchmark (roadmap phase 0) | Per the roadmap |
