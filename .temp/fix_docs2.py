import re

root = r"D:\Github_Codes\class-hosting-platform-github"


def read(p):
    return open(root + "\\" + p, encoding="utf8").read()


def write(p, s):
    open(root + "\\" + p, "w", encoding="utf8").write(s)


s = read("docs/COMPUTE_WORKER_PLAN.md")
s = re.sub(r"\*\*Rule: the machine's own runner and tasks are platform-owned code\..*?not built\.", "**Rule: only platform-owned code runs on this machine. Student code never does** (section 11 says why), and the machine holds no secrets or database access.", s, count=1, flags=re.S)
s = s.replace("- No student code outside a locked-down container (section 11.5), no secrets or database access on the machine, and no student data copied out of the account.", "- No student code on this machine (section 11), no secrets or database access on it, and no student data copied out of the account.")
write("docs/COMPUTE_WORKER_PLAN.md", s)

s = read("CHANGELOG.md")
a = s.index("Each student also gets a **Compute worker** setting")
b = s.index("This release builds the machine, the toggle, the schedule and the watchdog;", a)
s = s[:a] + "Students have no access to it and no student code runs on it (students who need more than a web request already have Background Jobs). " + s[b:]
write("CHANGELOG.md", s)
print("ok")
