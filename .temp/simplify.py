import os
import re

root = r"D:\Github_Codes\class-hosting-platform-github"


def read(p):
    return open(os.path.join(root, p), encoding="utf8").read()


def write(p, s):
    open(os.path.join(root, p), "w", encoding="utf8").write(s)


# ---------------------------------------------------------------- delete the job queue and its test
for p in ("lambda/worker_jobs.py", "tests/test_worker_jobs.py"):
    os.remove(os.path.join(root, p))

# ---------------------------------------------------------------- portal.py
s = read("lambda/portal.py")
# docs lines
s = re.sub(r"  GET\|POST\|DELETE /api/student/worker/jobs[^\n]*\n", "", s, count=1)
s = re.sub(r"  POST /api/admin/tokens/\{id\}/worker-access [^\n]*\n", "", s, count=1)
s = s.replace('        "worker_access": worker_access_view(item),\n', "", 1)
a = s.index("def worker_access_view(item):")
b = s.index("def traces_view(item):")
s = s[:a] + s[b:]
a = s.index('    if path == "/api/student/worker/jobs" or path.startswith("/api/student/worker/jobs/"):')
b = s.index('    if method == "GET" and (path == "/api/student/traces"')
s = s[:a] + s[b:]
a = s.index('        if path == "/api/admin/worker/jobs" and method == "GET":')
b = s.index('        if path == "/api/admin/worker" and method == "GET":')
s = s[:a] + s[b:]
write("lambda/portal.py", s)

# ---------------------------------------------------------------- worker.py: keep the machine, the schedule, the watchdog and the monthly cap; drop the student levels
s = read("lambda/worker.py")
a = s.index("# ---------------------------------------------------------------- what each student may do")
b = s.index("def handler(event, context=None):")
s = s[:a] + s[b:]
s = s.replace('LEVELS = (0, 1, 2, 3)  # per student: 0 off, 1 reports, 2 platform tasks, 3 their own code (docs/COMPUTE_WORKER_PLAN.md section 11)\nLEVEL_NAMES = {0: "off", 1: "reports", 2: "platform tasks", 3: "own code"}\n', "")
write("lambda/worker.py", s)

# ---------------------------------------------------------------- teacher page
s = read("web/teacher.js")
s = s.replace("\nlet workerJobs = []; // the students' jobs (queue and recent), from GET /api/admin/worker/jobs", "", 1)
s = re.sub(r"\n    if \(!worker\.unavailable\) workerJobs = [^\n]*", "", s, count=1)
s = s.replace("schedule, jobsSection(), last]", "schedule, last]", 1)
a = s.index("const JOB_STATES = {")
b = s.index("/** What this student may do with the compute worker")
s = s[:a] + s[b:]
a = s.index("/** What this student may do with the compute worker")
b = s.index("// ---------- the traces tab: what happened in each answer")
s = s[:a] + s[b:]
s = s.replace("    if (t.worker_access?.available) features.push(workerAccessFeature(t, live, btn));\n", "", 1)
write("web/teacher.js", s)

# ---------------------------------------------------------------- template and its guard test
s = read("template.yaml")
a = s.index("            - !If # students' own jobs (worker_jobs.py)")
b = s.index("            - Effect: Allow\n              Action: [s3:GetObject, s3:PutObject, s3:DeleteObject] # never portal/", a)
s = s[:a] + s[b:]
write("template.yaml", s)
s = read("tests/test_template_worker.py")
a = s.index('assert re.search(r"- UseWorker\\n\\s+- Effect: Allow\\n\\s+Action: s3:DeleteObject')
b = s.index("\n", a)
s = s[:a] + 'assert "s3:DeleteObject" not in "".join(re.findall(r"- UseWorker\\n[^\\n]*\\n[^\\n]*Action: [^\\n]*", portal_fn)), "the portal never deletes anything of the worker"' + s[b:]
write("tests/test_template_worker.py", s)

# ---------------------------------------------------------------- tests: drop the student-access section and the jobs test
s = read("tests/test_worker.py")
a = s.index("# ---------- 8. per-student access")
s = s[:a].rstrip("\n") + "\n"
write("tests/test_worker.py", s)
s = read("tests/run.mjs")
s = s.replace("['test_worker.py', 'test_worker_jobs.py', 'test_template_worker.py', 'test_worker_runner.py']", "['test_worker.py', 'test_template_worker.py', 'test_worker_runner.py']", 1)
write("tests/run.mjs", s)

# ---------------------------------------------------------------- dev server: the job-only fake S3 methods
s = read("tests/dev_server.py")
a = s.index("    def generate_presigned_post(self, Bucket, Key, Fields, Conditions, ExpiresIn):")
b = s.index("\n\n_dev_s3 = DevWorkerS3()")
s = s[:a].rstrip("\n") + "\n" + s[b:]
write("tests/dev_server.py", s)
print("ok")
