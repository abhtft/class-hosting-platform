import os


def edit(p, a, b):
    s = open(p, encoding="utf8").read()
    assert a in s, (p, a[:50])
    open(p, "w", encoding="utf8").write(s.replace(a, b, 1))


root = r"D:\Github_Codes\class-hosting-platform-github"
edit(root + r"\CHANGELOG.md", "## 2.3.1 (unreleased)\n\n", """## 2.3.1 (unreleased)

- **Compute worker (optional, off by default): one small machine for heavy checks.** A new feature you ask for with `plan --enable worker` (it is not part of "install everything", because a machine costs money while it runs). It creates one EC2 machine (c7g.xlarge, 4 ARM vCPUs and 8 GB, 30 GB encrypted disk, us-east-1) that is **off** until you start it. A new **Worker** tab on the teacher page shows its state, the hours used this month with a cost estimate, a **Start now** button (the machine runs its checks, writes a report to S3 and shuts itself down), **Start and keep on** (stays on until the time limit, for you to look around through Systems Manager), **Stop now**, an optional **Run every day at [hour]** switch, the time limit, and the last report. A control function runs every 15 minutes as a **watchdog**: it stops a machine that has no start request or has run past its limit, so a hung job can never run for a month. Nothing can connect to the machine from the internet (no inbound rule, no key pair, IMDSv2 only), its role reaches only the `worker/` folder of the bucket, only the portal and the control function may start and stop that one machine, and student code never runs on it. The image is chosen once and kept, so a new Amazon Linux release never replaces the machine. This release only builds the machine, the toggle, the schedule and the watchdog with a built-in test runner; the real tasks (daily quality probes, judging flagged answers) come later. See `docs/COMPUTE_WORKER_PLAN.md`. **To apply:** `plan --enable worker`, show the plan, `apply`; about $5 a month at 30 minutes a day (the machine only while it runs, the disk always).
""")
edit(root + r"\docs\COMPUTE_WORKER_PLAN.md", "> **Status: a plan with the owner's decisions recorded (section 9). Nothing is created in AWS and no code is written.**", "> **Status: W1 and W2 are built and tested locally (the machine, the watchdog, the schedule, the Worker tab, the installer feature `worker`), not yet applied to any AWS account. The tasks (W3 onward) are not built.** The plan has the owner's decisions recorded (section 9).")
print("ok")
