import re

root = r"D:\Github_Codes\class-hosting-platform-github"


def read(p):
    return open(root + "\\" + p, encoding="utf8").read()


def write(p, s):
    open(root + "\\" + p, "w", encoding="utf8").write(s)


def sub(p, a, b, count=1):
    s = read(p)
    assert a in s, (p, a[:70])
    write(p, s.replace(a, b, count))


sub("docs/AI_CONNECTOR.md", "Full steps: [END_TO_END.md section 4.5c](END_TO_END.md).", "Full steps: [TEACHER_SETUP.md](TEACHER_SETUP.md), section 6 (saving your AI keys).")
sub("docs/API_REFERENCE.md", "Companion to [END_TO_END.md](END_TO_END.md).", "Companion to [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md).")
sub("docs/API_REFERENCE.md", "see END_TO_END.md section 3.3a", "see AI_CONNECTOR.md")
sub("docs/STUDENT_KIT.md", "Companion to [END_TO_END.md](END_TO_END.md), [AI_CONNECTOR.md](AI_CONNECTOR.md)", "Companion to [AI_CONNECTOR.md](AI_CONNECTOR.md)")
sub("docs/TRACE_SCHEMA.md", "The plan and the reasons are in [TRACEABILITY_PLAN.md](TRACEABILITY_PLAN.md); the code is", "The reasons are in section 11; the code is")
sub("docs/TECHNICAL_SPECIFICATION.md", "([END_TO_END.md](END_TO_END.md), [API_REFERENCE.md]", "([API_REFERENCE.md]")
sub("docs/TECHNICAL_SPECIFICATION.md", "(runbook in [END_TO_END.md section 4.5b](END_TO_END.md))", "(runbook in [TEACHER_SETUP.md](TEACHER_SETUP.md), recovering a file)")
sub("lambda/worker.py", "See docs/COMPUTE_WORKER_PLAN.md.", "See docs/COMPUTE_WORKER.md.")
sub("worker/runner.py", "(docs/COMPUTE_WORKER_PLAN.md)", "(docs/COMPUTE_WORKER.md)")
sub("template.yaml", "See docs/COMPUTE_WORKER_PLAN.md.", "See docs/COMPUTE_WORKER.md.")

# COMPUTE_WORKER: carry the one idea from the plan that matters (the task model) and drop links to the deleted plan
s = read("docs/COMPUTE_WORKER.md")
s = s.replace(" The history of the design, including what was built and then removed, is in [COMPUTE_WORKER_PLAN.md](COMPUTE_WORKER_PLAN.md).", " Section 9 records what was built and then removed.")
a = s.index("- Before a task is added that runs **on a schedule**")
b = s.index("\n", a)
s = s[:a] + "- Before a task is added that runs **on a schedule**, add a simple tick-box mechanism: one small file per task in `worker/tasks/` with a name, description, time limit and cost cap; a tick-box per task on the Worker tab (a task that is not ticked never runs, and a task shipped in a release never runs by itself); one result per task; an alert email on a failed or alarming result. It was left out on purpose until there is a task to tick." + s[b:]
write("docs/COMPUTE_WORKER.md", s)

# RAG_ROADMAP: the trace plan became the trace schema
s = read("docs/RAG_ROADMAP.md")
s = s.replace("[TRACEABILITY_PLAN.md](TRACEABILITY_PLAN.md)", "[TRACE_SCHEMA.md](TRACE_SCHEMA.md)")
s = re.sub(r"\(\[TRACEABILITY_PLAN\.md\]\(TRACEABILITY_PLAN\.md\) section 9\)", "([TRACE_SCHEMA.md](TRACE_SCHEMA.md))", s)
write("docs/RAG_ROADMAP.md", s)
print(re.findall(r"TRACEABILITY_PLAN[^\n]{0,60}", read("docs/RAG_ROADMAP.md")))
