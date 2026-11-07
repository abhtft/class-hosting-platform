# Compute worker: one small machine, off until you start it

> **Status: complete for its purpose (closed 2026-11-07).** It gives the teacher a private machine for heavy checks that do not fit in a web request, ready for future work. It is **optional, off by default, and has no tasks yet** (by decision, see section 9). The history of the design, including what was built and then removed, is in [COMPUTE_WORKER_PLAN.md](COMPUTE_WORKER_PLAN.md).

## 1. What it is, in plain words

One small EC2 machine in your AWS account. It is **switched off** until you start it from the teacher page (or let it start once a day). When it starts it downloads a program (`worker/runner.py`), runs it, writes a short report, and **shuts itself down**. A watchdog makes sure it can never stay on by mistake.

It exists so that **future** heavy work has a place to run without changing the platform: quality probes of the book answers, judging flagged answers with a stronger model, attack tests, benchmark runs. Today it runs only a built-in setup step and reports what is installed.

**It is only for you, the teacher.** Students have no access to it, and no student code ever runs on it. (Students who need more than a web request already have *Background Jobs*, see [BACKGROUND_JOBS.md](BACKGROUND_JOBS.md).)

## 2. Turning it on or off for the whole platform

It is **not installed by default** and is never part of "install everything", because a machine costs money while it runs and its disk costs money while it exists.

| You want | Command (from your terminal, with the usual `--profile` if you use one) |
|---|---|
| See what installing it would add | `node setup.mjs plan --enable worker` |
| Install it | `node setup.mjs apply <plan id>` (the id the plan printed) |
| Remove it completely (machine, disk, role, schedule) | `node setup.mjs plan --disable worker`, then `apply <plan id>` |

The plan lists exactly what is added (the machine, its security group, role and instance profile, a small control function with its role and a 15-minute schedule). **You always see the plan before anything changes.** The machine is created running; the watchdog stops it within about 15 minutes (a few cents); from then on it is off until you start it.

If you remove it, reports already written stay in the bucket's `worker/` folder (a few kilobytes) until you delete them.

## 3. Using it: the Worker tab

Open the teacher page and the **Worker** tab. If the tab says "not installed", install it as in section 2.

| Control | What it does |
|---|---|
| **State** | Off, Starting, Running or Stopping, with the machine type and disk size |
| **Hours this month** | Machine time used, "x of y" hours against your monthly cap, and an **estimated** cost (list prices; the AWS bill is the truth) |
| **Start now** | Starts the machine. It runs the runner, writes a report and **shuts itself down** |
| **Start and keep on** | Starts it and keeps it on until the time limit, so you can look around (section 6) |
| **Stop now** | Stops it at once |
| **Run every day** (switch) and **Hour** | Starts it once a day at the hour you choose (UTC) and lets it shut itself down. Off by default |
| **Time limit** | The longest it may stay on after a start: 30, 60, 120, 180 or 360 minutes (default 60). The watchdog stops it after this |
| **Monthly cap** | The most machine hours a month: 10, 20, 40 (default), 80 or 160. When it is reached, **no start happens** until next month (or until you raise the cap) |
| **Last run** | When the last run happened, how long it took, whether it finished, and its output |

Nothing a student does can start or stop the machine. Only you (or the daily schedule you switched on) can.

## 4. What it costs

| Part | Estimate |
|---|---|
| The machine while it runs (`m7i-flex.large`, about $0.10 an hour) | about $1.50 a month at 30 minutes a day |
| The 30 GB disk (kept while the machine is stopped) | about $2.40 a month |
| A public address while it runs | under $0.10 |
| **Total** | **about $4 a month** at 30 minutes a day, about $2.50 if it is mostly off |

Prices are from memory of AWS's list: check them on the AWS pricing page. With trial credits the cost is covered. Model calls a future task makes are separate and are capped per task.

## 5. The machine and how it is protected

| Item | Setting |
|---|---|
| Size | **`m7i-flex.large`**: 2 vCPUs, 8 GB, x86. Chosen because an AWS **Free plan** account may only use free-tier instance types (`c7g.xlarge` was refused when first tried). A paid-plan account can choose the ARM sizes `c7g.large`, `c7g.xlarge` or `c7g.2xlarge`; the other allowed types are `c7i-flex.large`, `t3.small` and `t4g.small` |
| Disk | 30 GB gp3, **encrypted**, 20 to 200 GB allowed |
| Region and network | us-east-1, the default VPC, a public address only while it runs |
| Who can connect | **Nobody from the internet**: no inbound rule at all, no SSH, no key pair. You reach it through AWS Systems Manager (section 6) |
| Metadata | IMDSv2 only |
| What its role can reach | **Only the `worker/` folder** of the sites bucket, and the Systems Manager connection. No database, no secrets, no other folders |
| Who can start and stop it | Only the portal (your Start and Stop buttons) and a small control function, and only **this one machine** (the permission names its exact address) |
| The watchdog | The control function runs every 15 minutes. It **stops** a machine that is on with no start request, or that has run past its time limit. A hung job cannot run for a month |
| Image | The current Amazon Linux 2023 image for the machine's architecture, **chosen once** at install and then kept, so a new image release never replaces the machine |

A template test guards all of this: the machine has no inbound rule, no key pair, an encrypted disk, IMDSv2, and only the portal and the control function can start and stop it.

## 6. Looking inside the machine (optional)

1. On the Worker tab press **Start and keep on**.
2. From your terminal (this needs the AWS Session Manager plugin installed once):
   `aws ssm start-session --target <instance id> --profile <profile>`
   The instance id is in the stack outputs (`WorkerInstanceId`) and in the EC2 console.
3. When done, press **Stop now**, or let the time limit stop it.

You can run commands as root there. Anything you install or change on the disk stays until the machine is replaced.

## 7. What the runner does

`worker/runner.py` (in this repository, published to the bucket by `portal-publish` and downloaded by the machine at every start):

1. **Setup, once** (a marker file stops it from repeating): installs `git`, Node.js 22 with npm, Python 3.12 with pip and headers, `jq`, `unzip`, `tar`, and `gcc`, `g++` and `make`. Each is tried on its own, and one that is missing from the package repository is reported, not fatal.
2. Prints the versions of what is installed. That text is the report you see under **Last run**.
3. The machine's boot script then uploads the report to `worker/results/` and **shuts the machine down** (unless you started it with "keep on").

Every start in "run" mode takes under a minute once the setup is done.

## 8. Adding work to it later (the extension point)

Nothing needs to change in the platform to give the machine real work. The extension point is **the runner**:

- A task is a function added to `worker/runner.py` (or a file it imports from the repository's `worker/` folder). Its output is printed and becomes the report.
- A task that needs data from the platform should have the platform **stage it into `worker/inputs/`** before the run and read results back from `worker/outputs/` afterwards. That keeps the rule that the machine holds **no secrets and no database access**.
- A task that calls a model should use Amazon Bedrock with the machine's role (a narrow `bedrock:InvokeModel` grant, added by a shown plan), so the machine carries no keys, and the task must have its own time and cost cap.
- Before a task is added that runs **on a schedule**, add the simple tick-box mechanism described in [COMPUTE_WORKER_PLAN.md](COMPUTE_WORKER_PLAN.md) section 12 (one file per task, a tick-box per task on the Worker tab, one result per task, an alert email on failure). It was left out on purpose until there is a task to tick.

The planned first uses are in [RAG_ROADMAP.md](RAG_ROADMAP.md): a **benchmark** of the book answers (its document list needs the owner's approval before any download), **daily golden probes** (fixed questions through the real answer path, an alert when quality drops), and **judging flagged answers**.

## 9. Decisions and what was deliberately left out

| Decision | Why |
|---|---|
| Optional and off by default, not part of "everything" | It costs money; most teachers will not want it |
| Only the teacher can start it | Cost and safety stay with one person |
| **No student access, no student code** | Untrusted code next to class tools is a real risk (a container is not a perfect wall), and *Background Jobs* already answers the student need safely. A per-student access level and a job queue were designed, partly built, then **removed** as over-engineering |
| **No task mechanism yet** | There are no tasks to run. Building the mechanism first would repeat the over-engineering; it is described and ready to add when the first task exists |
| **The benchmark is not part of this feature** | It belongs to the RAG quality work and needs its own approval of the document list; the worker is only where it can run |
| Stop and start (not launch and delete) | Installed tools stay, runs start fast; the cost is the disk (about $2.40 a month) |
| No reservation, no Spot | A reservation is 1 to 3 years and bills while stopped; Spot can be interrupted and a 30-minute job gains little |

## 10. If something goes wrong

| Symptom | What it means and what to do |
|---|---|
| The install fails with "The specified instance type is not eligible for Free Tier" | Your account is on the AWS Free plan. The default size already fits it; if you set an ARM size, remove that setting or upgrade the account. The failed install rolls back and changes nothing |
| The tab says "not installed" | Install it (section 2) |
| **Start now** says the monthly cap is reached | Raise the cap on the Worker tab or wait for next month |
| **Start now** says the machine is already on or still stopping | Wait a minute and try again; the tab shows its state |
| The machine stays on longer than expected | The watchdog stops it within 15 minutes of its time limit. You can press **Stop now** at once |
| "Last run" shows an error or a non-zero exit | The report text says which step failed. Setup retries on the next start; start it again |
| You no longer want it | `plan --disable worker`, then `apply` (section 2) |

## 11. Where the code is

| What | Where |
|---|---|
| The machine, its role, the control function and schedule | `template.yaml` (`WorkerInstance`, `WorkerRole`, `WorkerControlFunction`, and the parameters `WorkerEnabled`, `WorkerInstanceType`, `WorkerDiskGb`, `WorkerMaxMinutes`, `WorkerAmiId`) |
| Start, stop, watchdog, schedule, cap, minutes and cost estimate | `lambda/worker.py` and the routes in `lambda/portal.py` (`/api/admin/worker`, `.../start`, `.../stop`, `.../settings`) |
| The Worker tab | `web/teacher.js`, `web/teacher.html` |
| The runner and its setup | `worker/runner.py` |
| The installer feature and the image choice | `installer/engine.mjs` (`worker`, `withWorkerImage`) |
| Tests | `tests/test_worker.py`, `tests/test_worker_runner.py`, `tests/test_template_worker.py`, and the worker part of `tests/test_installer.mjs` |

## 12. What has and has not been proven on a real account

**Proven on a real AWS account:** the install (including the Free plan refusal and the rollback), the first boot, the runner installing the software and writing a report, the machine shutting itself down, the control function running every 15 minutes and reading the machine's state, the teacher page loading the Worker tab.

**Proven only by tests:** the Start, Stop and daily schedule logic, the monthly cap, the cost estimate and settings validation (with a fake machine and fake storage).

**Not yet tried by a person on the live page:** pressing Start now and Stop now, switching the daily schedule on, and changing the cap and time limit. The first time you do, expect a start to take about a minute.
