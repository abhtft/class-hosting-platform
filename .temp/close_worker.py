import re

root = r"D:\Github_Codes\class-hosting-platform-github"


def read(p):
    return open(root + "\\" + p, encoding="utf8").read()


def write(p, s):
    open(root + "\\" + p, "w", encoding="utf8").write(s)


s = read("docs/README.md")
s = re.sub(r"\| \[COMPUTE_WORKER_PLAN\.md\]\(COMPUTE_WORKER_PLAN\.md\) \|[^\n]*\n",
           "| [COMPUTE_WORKER.md](COMPUTE_WORKER.md) | **Built, closed:** the optional compute worker, one small machine that is off until the teacher starts it (Start, Stop, daily schedule, time limit, monthly cap), for heavy checks later; install and remove, cost, protection, what the runner does, how to add work, what is proven |\n"
           "| [COMPUTE_WORKER_PLAN.md](COMPUTE_WORKER_PLAN.md) | The history and decisions behind the compute worker, including what was built and then removed (student access, a job queue) |\n", s, count=1)
write("docs/README.md", s)

s = read("docs/COMPUTE_WORKER_PLAN.md")
s = re.sub(r"> \*\*Status \(2026-11-07\):.*?\(section 12\), after the benchmark\.", "> **Status: CLOSED (2026-11-07). This is the design history. The current, accurate guide is [COMPUTE_WORKER.md](COMPUTE_WORKER.md).** The machine, the Worker tab, the watchdog and the installed software are built and live. Student access and a job queue were designed, partly built and **deliberately removed** (section 11). The simple task mechanism (section 12) is documented but **not built**, on purpose, until there is a first task to run.", s, count=1, flags=re.S)
write("docs/COMPUTE_WORKER_PLAN.md", s)

s = read("docs/TECHNICAL_SPECIFICATION.md")
row = [l for l in s.split("\n") if l.startswith("| F18 |")][0]
new = "| F19 | Compute worker (optional, off by default): one small EC2 machine (`m7i-flex.large`, 30 GB encrypted disk, us-east-1) for the teacher's own heavy checks; started and stopped only from the teacher page or an optional daily schedule, a 15-minute watchdog stops it past its time limit, a monthly cap on machine hours, no inbound access, a role that reaches only the `worker/` folder, no student access (`docs/COMPUTE_WORKER.md`, `lambda/worker.py`, `worker/runner.py`) |"
s = s.replace(row, row + "\n" + new, 1)
write("docs/TECHNICAL_SPECIFICATION.md", s)
print("ok")
