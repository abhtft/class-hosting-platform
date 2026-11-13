p = r"D:\Github_Codes\class-hosting-platform-github\docs\COMPUTE_WORKER_PLAN.md"
s = open(p, encoding="utf8").read()


def rep(a, b):
    global s
    assert a in s, a[:60]
    s = s.replace(a, b, 1)


rep("**Rule: only platform-owned code runs on this machine. Student code never does.** The machine reads class data that students must not see, so it must never execute anything a student wrote.",
    "**Rule: the machine's own runner and tasks are platform-owned code. Student code runs only at access level 3, only for a student the teacher switched on, only in a locked-down container, and the machine holds no secrets or database access (section 11.5).** Today nothing of a student's runs there: level 3 is designed but not built.")
rep("- No student code on this machine, and no student data copied out of the account.", "- No student code outside a locked-down container (section 11.5), no secrets or database access on the machine, and no student data copied out of the account.")
rep("The machine is platform-only: there is no student route to it, and no student code ever runs there.", "The machine is platform-only: there is no student route to it, and no student code runs there.")
open(p, "w", encoding="utf8").write(s)

p = r"D:\Github_Codes\class-hosting-platform-github\web\teacher.js"
s = open(p, encoding="utf8").read()
rep("it can only use its own folder in the bucket, and it never runs student code.", "it can only use its own folder in the bucket, and today it runs no student code.")
open(p, "w", encoding="utf8").write(s)

p = r"D:\Github_Codes\class-hosting-platform-github\CHANGELOG.md"
s = open(p, encoding="utf8").read()
rep("and student code never runs on it.", "and today no student code runs on it (a later, per-student, locked-down option is planned in the plan document).")
open(p, "w", encoding="utf8").write(s)
print("ok")
