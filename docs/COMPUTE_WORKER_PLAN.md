# Compute worker plan: one small scheduled machine for heavy checks

> **Status: W1 and W2 are built and live (the machine, the watchdog, the schedule, the Worker tab, the installer feature `worker`; the runner installs git, Node.js and Python 3.12). Sections 11 to 13 are a proposal for modularity, student access and the full configuration list: not built, waiting for the owner's answers in section 13. The tasks (W3 onward) are not built.** The plan has the owner's decisions recorded (section 9). It needs the usual shown-and-approved `plan` before any `apply`, and the benchmark (W0) comes first. It supports the quality work in [RAG_ROADMAP.md](RAG_ROADMAP.md) and reads the traces described in [TRACEABILITY_PLAN.md](TRACEABILITY_PLAN.md).

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

**Rule: only platform-owned code runs on this machine. Student code never does.** The machine reads class data that students must not see, so it must never execute anything a student wrote.

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
- No student code on this machine, and no student data copied out of the account.
- No task without a cost cap, and no apply without a shown and approved plan.

## 11. Modularity: four independent layers

The worker is built so each layer can be switched off or replaced without touching the others. Nothing above a layer works without it, and nothing below needs the one above.

| Layer | What it is | Switched by | When off |
|---|---|---|---|
| **A. The machine** | The instance, its disk, role, security group and the control function (built and live) | The installer feature `worker` (`plan --enable worker` or `--disable worker`). **Not part of "install everything"** | Nothing exists in the account: no machine, no disk, no cost |
| **B. Running** | Whether the machine is on right now | The teacher page: Start now, Stop now, the daily schedule | The machine is stopped: only the disk is charged |
| **C. Tasks** | Small platform-owned programs the machine runs (probes, judging, attack tests) | One tick-box per task on the teacher page, each with its own cost cap | A task that is off never runs and never reads anything |
| **D. Student access** | What a student may see or ask of the worker, for their own app | A per-student setting on the teacher page | A student with access off sees and can do nothing |

**What exists today:** layers A and B (plus a built-in test runner). C and D are not built.

### 11.1 Layer A: optional on any account, off by default (your point 1)

- **Default is off.** A new install never creates the machine, the disk or the control function. Teachers who never want it never see a cost, and the teacher page says "not installed" in the Worker tab.
- **Switching it on** is one deliberate command and one shown-and-approved plan, like the other optional features. The plan lists exactly what is added (8 resources) and can be read before anything happens.
- **Switching it off** removes the machine, its disk (deleted with it), the role and the control function. Reports already written stay in the bucket's `worker/` folder (a few kilobytes) until the teacher deletes them.
- **Works on any account:** the machine type defaults to one a **Free plan** account may use (`m7i-flex.large`); a paid-plan account may choose the ARM sizes. The image follows the type. The same template serves both.
- **Cost while installed but stopped:** the disk only (about $2.40 a month for 30 GB). A planned option for teachers who do not want even that: **launch a fresh machine for each run and delete it afterwards** (no disk cost, slower runs). Today's choice (stop and start) stays the default because tools stay installed.

### 11.2 Layer D: student availability, controlled by the teacher for each app (your point 2) (proposed, not built)

**Today students have no access at all.** The machine is platform-only: there is no student route to it, and no student code ever runs there. This section is what could be added, one level at a time, always chosen by the teacher for each student (each student has one app).

| Level | What the student gets | Risk | Proposed |
|---|---|---|---|
| **0. Off (default)** | Nothing. The Worker does not exist for them | None | **Yes: the default for everyone** |
| **1. Reports** | They can read the results of platform checks run on **their own app** (for example "your answers: 12 refusals, 3 unsupported claims, cost per answer"), next to their traces | Low: it is their own data, shown the way traces are | **Yes** |
| **2. Ask for a platform task** | They can request a **named, platform-owned task** for their own app, for example "evaluate my shelf with the class benchmark" or "re-check my last 50 answers". The platform's code runs; the student only supplies a shelf or app name | Medium: uses machine minutes and model money | **Yes, with limits (below)** |
| **3. Run their own code** | Their own scripts run on the machine | **High: untrusted code on a machine that can read class data** | **No.** It would need a separate, isolated, credential-free sandbox, decided later and separately |

**Limits for level 2** (set by the teacher, with safe defaults):

- A number of requests a day and minutes a month **for each student**, and a **class-wide monthly cap** on machine minutes (when it is reached, no further start happens until next month).
- A task may only read **that student's own app data** (their shelves, their traces), never another student's and never the class library's private parts. The task is given the student's name by the platform, not by the student.
- Requests are **queued**. If the machine is off, a request waits for the next scheduled or manual start (or, if the teacher allows **start on request**, it starts the machine, within the class cap).
- Every request and result is recorded, visible to the teacher, and counted against the student's allowance.

### 11.3 Layer C: tasks as plug-in modules (proposed, not built)

- **One file per task** in the repository (`worker/tasks/<name>.py`), each declaring: its name, what it reads, what it writes, its time and model-cost cap, and who may request it (teacher only, or students at level 2).
- A task is **off until the teacher ticks it**. A new task shipped in a release never runs by itself.
- **Least permission per task.** The machine's role today reaches only the `worker/` folder. A task that needs more (the AI key, reading traces or shelves) adds **one narrow read grant**, shown in a plan before it exists, so installing the worker never grants broad access in advance.
- **Results** go to `worker/results/<task>/<date>.json` (and the latest copy), shown on the Worker tab and, at level 1 or 2, to the student for their own app. Reports are deleted after 90 days, like traces.

### 11.4 Safety controls (current and planned)

| Control | State |
|---|---|
| Machine off by default, started only by the teacher or the schedule | Built |
| Watchdog stops a machine that runs without a start request or past its limit | Built (the first real run of the watchdog is still to be seen in the logs) |
| No inbound access, no key pair, IMDSv2 only, encrypted disk | Built |
| Role reaches only the `worker/` folder; only the portal and the control function can start and stop that one machine | Built (guarded by a template test) |
| **Monthly cap on machine minutes**, after which no start happens | Planned |
| **Alert email** when the cap is near or a run fails | Planned (uses the existing alert email) |
| Per-student access level, quotas, request queue | Planned (section 11.2) |
| Report retention 90 days | Planned |
| Signed runner (the machine only runs a runner signed by the platform's key) | Planned (today the runner is code in the bucket's `worker/` folder, written only by the teacher's publish and by the platform's own functions) |

## 12. Configuration reference (everything that can be set)

| Setting | Where | Default | Who changes it | Needs an apply? | State |
|---|---|---|---|---|---|
| Worker installed at all | Installer feature `worker` | **Off** | Teacher, in the terminal | Yes (plan shown first) | Built |
| Machine type | `WorkerInstanceType` | `m7i-flex.large` (2 vCPUs, 8 GB, x86); paid plans may pick `c7g.large`/`c7g.xlarge`/`c7g.2xlarge` (ARM) or the other free-tier types | Teacher, in the terminal | Yes. **Changing the type replaces the machine** (the disk and installed tools are lost and reinstalled) | Built |
| Disk size | `WorkerDiskGb` | 30 GB (20 to 200) | Teacher, in the terminal | Yes. Growing is safe; shrinking replaces the machine | Built |
| Region | the stack's region | us-east-1 | Fixed at install | n/a | Built |
| Network | default VPC, public IP only while on, **no inbound rule** | n/a | Fixed | n/a | Built |
| How you reach it | AWS Systems Manager | n/a | Fixed | n/a | Built |
| Machine image | `WorkerAmiId` | Chosen once by the installer, then kept | Installer | n/a | Built |
| Start now, Start and keep on, Stop now | Worker tab | n/a | Teacher, any time | No | Built |
| Daily start on or off, and the hour (UTC) | Worker tab | Off, 03:00 UTC | Teacher, any time | No | Built |
| Time limit per start | Worker tab and `WorkerMaxMinutes` | 60 minutes (15 to 360) | Teacher, any time | No (the template value is only the default) | Built |
| What the runner does | `worker/runner.py` in the bucket | Installs git, Node.js, Python 3.12 and build tools once, then reports | Changes with the release | Published with the portal | Built |
| Which tasks run | Task tick-boxes | None | Teacher | No | Planned |
| Cost cap per task | Task setting | A small default | Teacher | No | Planned |
| Monthly cap on machine minutes | Worker tab | 40 hours | Teacher | No | Planned |
| Student access level, quotas | Student card | **Off** | Teacher, per student | No | Planned |
| Start on request | Worker tab | Off | Teacher | No | Planned |
| Alert email for caps and failures | existing alert email | Used if the platform has one | Teacher | No | Planned |
| One-run-per-machine mode (no disk kept) | `WorkerEnabled` variant | Off | Teacher, in the terminal | Yes | Planned |

## 13. Questions for the owner before any task is built

1. **Student levels.** Are levels 0 (off), 1 (reports) and 2 (platform tasks) the right set, with level 3 (their own code) ruled out for now?
2. **Level 2 defaults.** Safe starting numbers: 5 requests a day and 30 minutes a month for each student, and a class cap of 40 hours a month?
3. **Start on request.** May a student's request start a stopped machine (within the class cap), or only wait for the teacher's next start or the daily schedule? Recommended: wait, until the cost pattern is known.
4. **Disk or no disk.** Keep stop and start (tools stay installed, disk about $2.40 a month) or add the launch-and-delete mode as an option later? Recommended: keep the current mode now, add the option later if wanted.
5. **First tasks.** Still golden probes and judging flagged answers, teacher-only (level 0 for students) in the first release?
