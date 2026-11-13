root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


edit("docs/COMPUTE_WORKER_PLAN.md", "| W4 | The job protocol and queue: student routes and teacher queue view; jobs stored in the bucket, quotas and caps enforced, nothing starts the machine | A student's job appears in the teacher's queue; over-quota and level 0 are refused |",
     "| W4 | **Built, not yet applied.** The job protocol and queue (section 11.6): student routes and the teacher's queue on the Worker tab; jobs stored in the bucket, quotas and caps enforced, nothing starts the machine. Nothing runs the jobs yet (W5) | A student's job appears in the teacher's queue; over-quota and level 0 are refused |")
edit("docs/COMPUTE_WORKER_PLAN.md", "## 13. Owner decisions (2026-11-07) and the build order", """## 11.6 The job protocol (built in W4)

The platform stores and queues jobs; the machine (W5) only reads one file and writes results. The machine has no database access.

| Step | Who | What | Where |
|---|---|---|---|
| 1. create | Student (level 3) | `POST /api/student/worker/jobs {language: python or node, entry: file name}`. Checks: level 3, requests a day, at most 3 open jobs, minutes left this month. Answers with a private upload form for **one zip of at most 5 MB** | Record `wjob#<slug>#<id>` in the tokens table; upload key `worker/jobs/<slug>/<id>/input.zip` (built from the signed-in student, never from the request) |
| 2. submit | Student | `POST .../jobs/<id>/submit` after uploading. The platform checks the zip exists and its size, marks the job **queued**, rewrites the queue file | `worker/queue/queue.json`: queued jobs only, oldest first, each with id, slug, language, entry, input key, time limit, internet yes or no |
| 3. run (W5) | The machine, when the teacher has started it | Takes jobs one at a time, runs each in a locked-down container, writes `status.json` (state, start, finish, seconds, exit code, log tail) and `result.zip` | `worker/jobs/<slug>/<id>/` |
| 4. sync | The platform | Whenever a student or the teacher looks at the job, it reads `status.json`, updates the record, **adds the minutes to the student's month (once)**, and re-stages the queue | Counter `wjm#<slug>#<month>` |
| result | Student | `GET .../jobs/<id>/result` gives a 5-minute download link to `result.zip` | |
| cancel, delete | Student or teacher | A waiting job can be cancelled; a finished one deleted (record, input, status and result). A running job stops at its time limit | |
| expiry | The platform | A job that waits more than 7 days for the machine expires | |

Teacher routes: `GET /api/admin/worker/jobs` (queue and the 20 most recent), `POST .../jobs/<slug>/<id>/cancel`, `DELETE .../jobs/<slug>/<id>`.
**Nothing in this protocol starts the machine.** The tests make any call to EC2 from the jobs code a failure. The portal's permission to delete job files is limited to `worker/jobs/`.

## 13. Owner decisions (2026-11-07) and the build order""")
edit("CHANGELOG.md", "**this release only stores the setting: no level has an effect yet** (the job queue and the locked-down container runner are the next steps in `docs/COMPUTE_WORKER_PLAN.md`).", "**levels 1 and 2 have no effect yet.** Level 3 (own code) now has the **job queue**: a student at level 3 can create a job (a zip of at most 5 MB with a Python or Node entry file), submit it, see it wait, cancel it and read its status, within limits you set (requests a day, at most 3 open jobs, minutes a month); a new **Students' jobs** list on the Worker tab shows the queue with Cancel and Delete. **Nothing runs the jobs yet and nothing starts the machine**: the locked-down container runner is the next step in `docs/COMPUTE_WORKER_PLAN.md`.")
print("ok")
