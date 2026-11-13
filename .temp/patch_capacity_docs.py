root = r"D:\Github_Codes\class-hosting-platform-github"


def sub(p, a, b):
    f = root + "\\" + p.replace("/", "\\")
    s = open(f, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(f, "w", encoding="utf8").write(s.replace(a, b, 1))


# CHANGELOG
sub("CHANGELOG.md", "## 2.3.1\n", """- **A class can use the AI together: the backend limits follow your AWS account.** The gateway let only 3 student requests run at once for the whole platform (right for a new AWS account, which allows only 10 concurrent function executions), so a class of 30 asking the AI in the same minute mostly saw "busy". The install and `plan` now read the account's Lambda concurrency limit and set the caps from it (the whole limit is used, ten executions stay free for the portal): a limit of 1000 allows about 247 requests at once, up to 8 per student, and about 494 requests a second. They are template parameters (`BackendInflightGlobal`, `BackendInflightPerStudent`, `BackendApiRate`, `BackendApiBurst`, `BackendWaitMs`); a new account keeps today's small values and the install tells you how to ask AWS for more. When every slot is busy, the gateway now **waits up to 1.5 seconds** for one to free up (only on accounts with room for it), and the starter's `api()` **asks again up to three times** on "busy" (the platform answers that before your code runs, so repeating is safe). `verify` shows a warning until the account can serve a class of 30. **To apply:** raise the limit once (AWS console, Service Quotas, AWS Lambda, Concurrent executions, request 1000: free, usually granted in a day), then `plan` and `apply <plan id>`; the kit sync brings the retry to student projects (kit 2.3.2). A measuring script for a real platform is `tests/live_load.py`.

## 2.3.1
""")

# CRITICAL_ISSUES
sub("docs/CRITICAL_ISSUES.md", "### 3. Backend capacity: 3 requests in flight for the whole platform", "### 3. Backend capacity: 3 requests in flight for the whole platform (fixed in code, 2.3.2 unreleased)")
sub("docs/CRITICAL_ISSUES.md", "- **Fix.** (a) Read the account's real concurrency quota", "- **Status.** Done in code: the installer reads the quota and sets the caps (parameters), the gateway waits up to 1.5 s for a slot when the account has room, the starter retries on \"busy\", `verify` warns until the account can serve 30, and `tests/live_load.py` repeats the measurement. Still to do: apply it on the live platform and run the script there.\n- **Fix (as designed).** (a) Read the account's real concurrency quota")

# TEACHER_SETUP: capacity
sub("docs/TEACHER_SETUP.md", "**Recovering a file (S3 versioning).**", """**How many students can use the AI at once (capacity).** Every student request can hold up to four function executions at the same moment (the gateway, the student's function and the function it calls), and a new AWS account allows only **10** in total, so by default only about 3 requests run at once and a class of 30 asking together will often see "busy". Ask AWS for more (free, usually granted within a day): AWS console, **Service Quotas**, **AWS Lambda**, **Concurrent executions**, request **1000**. Then run `plan` and `apply <plan id>`: the installer reads the new limit and raises the caps (about `(limit - 10) / 4` requests at once; 1000 allows about 247, 8 per student, 494 requests a second), and the gateway waits up to 1.5 seconds for a free slot before it answers "busy", while the student kit asks again up to three times. `verify` shows a warning until the account can serve a class of 30. `tests/live_load.py` measures it on a real platform.

**Recovering a file (S3 versioning).**""")

# STATUS + README
sub("docs/STATUS.md", "**Everyone at once.** A class of 30 using the AI features in the same minute exceeds what the backend gateway allows (3 requests in flight, about 10 a second), so some students see \"busy\". It is by design on a new AWS account (10 concurrent executions), but it will be the first thing a real class notices.",
    "**Everyone at once.** The backend gateway's caps now follow the account's Lambda concurrency limit (a limit of 1000 serves about 247 requests at once; the gateway waits briefly and the kit retries). On a new account (limit 10) only about 3 run at once until the teacher asks AWS for more (free); `verify` warns until then. Applied on the live platform only after the owner approves the plan; measured by `tests/live_load.py`.")
sub("README.md", "- **Backend capacity is small by design:** at most 3 student backend requests run at once on the whole platform (2 per student) and the backend API allows about 10 requests a second, because a new AWS account may allow only 10 concurrent function executions. A class using the AI at the same moment will see \"busy\" answers. See [docs/STATUS.md](docs/STATUS.md).",
    "- **Backend capacity follows your AWS account:** a new account allows only 10 concurrent function executions, so about 3 student requests run at once until you ask AWS for more (free: Service Quotas, AWS Lambda, request 1000). The install reads the limit and sizes the caps; `verify` warns until the account can serve a class of 30. See [docs/TEACHER_SETUP.md](docs/TEACHER_SETUP.md) (section 13a).")
print("ok")
