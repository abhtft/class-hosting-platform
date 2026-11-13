p = r"D:\Github_Codes\class-hosting-platform-github\docs\COMPUTE_WORKER_PLAN.md"
s = open(p, encoding="utf8").read()
i = s.index("## 13. Questions for the owner before any task is built")
s = s[:i] + '''## 11.5 Level 3: a student's own code, made safe enough (design)

Student code is untrusted, so the design assumes it is hostile and limits what a hostile program could reach. **The machine holds nothing worth stealing**, and each job runs in a container that can reach almost nothing.

**The machine (the same one) never holds secrets or database access.** Its role reaches the `worker/` folder of the bucket and, later, `bedrock:InvokeModel` for the judge tasks (no keys at all). It has **no** database, no Secrets Manager and no other bucket folders. Anything a trusted task needs from the platform (a student's traces, a shelf's questions) is **staged by the platform into `worker/inputs/` before the run**, and anything it produces is **read back by the platform** from `worker/outputs/`. So even a complete break-out of a student container finds no credentials of value.

**Each job runs in a fresh container** (Docker on the machine):

| Control | Setting |
|---|---|
| Identity | A non-root user, no extra Linux capabilities, `no-new-privileges`, a read-only system, a small writable scratch folder |
| AWS credentials | **Not reachable**: the machine requires the newer metadata protocol with a hop limit of 1, which a container cannot pass, and the container network also blocks the metadata address |
| Network | **None by default** (no outbound). A per-student teacher switch may allow outbound internet for a project that needs to download data |
| Limits | CPU, memory, number of processes, disk and a hard **time limit** (default 10 minutes, set by the teacher), enforced by the container, plus the machine's own watchdog |
| Input | Only the files the student submitted for this job, copied into a fresh folder |
| Output | A results folder (size-capped) returned to that student, plus the job's log tail |
| Lifetime | The container and its folders are deleted when the job ends; nothing carries over to the next job or the next student |
| Languages | Python 3.12 and Node 22 (the machine's installed runtimes) |
| Packages | No installs by default; the teacher may allow outbound network for a student so their job can install packages |

**Accepted residual risk, stated plainly:** a container is not a perfect wall. A kernel flaw could let hostile code break out of it. The design limits the damage (the machine has no secrets and little permission, the machine is only on while a job or task runs, jobs of different students never run at the same time, and the machine is stopped afterwards) but cannot reduce it to zero. If this ever matters more (an outside class, sensitive data), the answer is a separate machine for student code, which the same template could create.

**One job at a time, in a queue:** jobs wait until the machine is running; the teacher starts and stops the machine (decision 3), so a student request **never** starts it. A job that finds the machine on runs in turn; a queued job older than a limit (default 7 days) is dropped with a message.

**How a student sends a job (decision from the owner):** from their project. A kit command (for example `npm run worker -- script.py`) uploads the script and its input files, shows the queue state, and fetches results and log when done. Teacher-side: a queue view with each job's student, size, state, time used, and a delete or cancel button.

**Accounting:** every job counts minutes against the student's monthly allowance and against the class cap; the student sees their own use.

## 13. Owner decisions (2026-11-07) and the build order

| Question | Decision |
|---|---|
| Student levels | **All four levels, each a per-student toggle, off by default:** 0 off, 1 reports, 2 platform tasks, 3 their own code (design in section 11.5) |
| Level 2 defaults | 5 requests a day and 30 minutes a month for each student; a class cap of 40 hours a month on machine minutes |
| Start on request | **No.** The machine is started and stopped only by the teacher (the button or the daily schedule); a student request never starts it |
| Disk | Keep stop and start; the launch-and-delete mode may come later as an option |
| First tasks | Golden probes and judging flagged answers, teacher-only |
| Level 3 isolation | **The same machine with strict containers** (the second machine stays a later option) |
| Level 3 network | **None unless the teacher allows it for that student** |
| How jobs are sent | **From the student's project** with a kit command |

**Build order (each step works alone and is applied only after a shown plan):**

| Step | Deliverable | Done when |
|---|---|---|
| W3 | The per-student access level (0 to 3) and its quotas, the class monthly cap on minutes (a start is refused when the cap is reached), shown on the student card and the Worker tab | The teacher can set a level for a student and a cap for the class; the cap stops a start in a test |
| W4 | The job protocol and queue: student routes and teacher queue view; jobs stored in the bucket, quotas and caps enforced, nothing starts the machine | A student's job appears in the teacher's queue; over-quota and level 0 are refused |
| W5 | The container job runner on the machine (level 3) with every control in section 11.5, plus tests that try to break out (read the machine's credentials, reach the network, use too much memory or time, read another student's files) | All escape attempts fail on the real machine |
| W6 | The student kit command and results, teacher-side cancel and delete | A student sends a script from their project and reads the result |
| W7 | Platform tasks (level 2) and the first teacher tasks (golden probes, judging), after the benchmark | Per the roadmap above |
'''
open(p, "w", encoding="utf8").write(s)
print("ok")
