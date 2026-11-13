p = r"D:\Github_Codes\class-hosting-platform-github\docs\COMPUTE_WORKER_PLAN.md"
s = open(p, encoding="utf8").read()
i = s.index("## 11. Modularity: four independent layers")
head = s[:i]
head = head.replace(
    "> **Status: W1 and W2 are built and live (the machine, the watchdog, the schedule, the Worker tab, the installer feature `worker`; the runner installs git, Node.js and Python 3.12). Sections 11 to 13 are a proposal for modularity, student access and the full configuration list: not built, waiting for the owner's answers in section 13. The tasks (W3 onward) are not built.**",
    "> **Status (2026-11-07): the machine is built and live, and the design is deliberately SMALL.** The machine, the Worker tab (Start, Stop, Start and keep on, daily schedule, time limit, monthly cap, last report), the watchdog and the installed software work on real AWS. Student access, a job queue and running students' own code were designed, partly built, then **deliberately removed** as over-engineered (section 11). What is next is one simple task mechanism for the teacher's own checks (section 12), after the benchmark.")
tail = '''## 11. What we decided NOT to build (and why)

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
'''
open(p, "w", encoding="utf8").write(head + tail)
print("ok")
