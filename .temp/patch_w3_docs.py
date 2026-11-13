root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


edit("web/teacher.js", "h('p', { class: 'privacy small muted' }, 'You start and stop the machine yourself (the Worker tab); a student’s request never starts it. Own code runs one job at a time, in a fresh container with no access to your account.')",
     "h('p', { class: 'privacy small muted' }, 'This only stores what the student will be allowed to do: reports, platform tasks and running their own code are not available yet, so no level has an effect today. You start and stop the machine yourself (the Worker tab); a student’s request will never start it.')")
edit("CHANGELOG.md", "This release builds the machine, the toggle, the schedule and the watchdog;", "A **monthly cap on machine minutes** (default 40 hours, set on the Worker tab) refuses a start once it is reached. Each student also gets a **Compute worker** setting on their card (level 0 off, 1 reports, 2 platform tasks, 3 their own code, plus request, minute and job-time limits and an internet switch for level 3), off by default; **this release only stores the setting: no level has an effect yet** (the job queue and the locked-down container runner are the next steps in `docs/COMPUTE_WORKER_PLAN.md`). This release builds the machine, the toggle, the schedule and the watchdog;")
edit("docs/COMPUTE_WORKER_PLAN.md", "| W3 | The per-student access level (0 to 3) and its quotas, the class monthly cap on minutes (a start is refused when the cap is reached), shown on the student card and the Worker tab | The teacher can set a level for a student and a cap for the class; the cap stops a start in a test |",
     "| W3 | **Built, not yet applied.** The per-student access level (0 to 3) and its quotas, the class monthly cap on minutes (a start is refused when the cap is reached), shown on the student card and the Worker tab. The setting is only stored: no level has an effect until W4 and later | The teacher can set a level for a student and a cap for the class; the cap stops a start in a test |")
print("ok")
