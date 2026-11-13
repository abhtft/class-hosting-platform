root = r"D:\Github_Codes\class-hosting-platform-github"
p = root + r"\docs\TEACHER_SETUP.md"
s = open(p, encoding="utf8").read()


def rep(a, b):
    global s
    assert a in s, a[:80]
    s = s.replace(a, b, 1)


rep("13. [If something goes wrong](#13-if-something-goes-wrong)\n14. [All commands](#14-all-commands)",
    "13. [If something goes wrong](#13-if-something-goes-wrong)\n13a. [Alerts, cost and recovering a file](#13a-alerts-cost-and-recovering-a-file)\n14. [All commands](#14-all-commands)")
rep("| **Optional features**, each off for every student until you switch it on for that student | a small backend, class AI (OpenRouter or Google Gemini), class storage, the student's own domain |",
    "| **Optional features**, each off for every student until you switch it on for that student | a small backend, class AI (OpenRouter or Google Gemini), class storage, class knowledge (books to answer from), background jobs, the student's own domain, and traces (a record of each answer) |")

rep("""| **Custom domain** | their own domain for their site | see section 7 |
""", """| **Class knowledge** | books (PDF, text, Markdown, HTML) their app answers from, with page and source, and "The books do not say." when the books do not answer; plus a **class library** you build once and every student can search | the Google key saved once (below); it reads the books |
| **Traces** | a step-by-step record of each answer their app gives (the question, which passages were found, the AI call, the cost) | nothing extra; the student needs the backend |
| **Custom domain** | their own domain for their site | see section 7 |
""")

rep("""**Saving your AI keys**""", """**Class knowledge, in a few words.** Switch it on in the student's panel (the **Class knowledge** card). Their app searches its own shelves and the class library; they can also put books in a `knowledge/<shelf>/` folder of their project and run `npm run knowledge` (a deploy does it too). Reading a textbook costs a few US cents once; the class-wide ceiling for adding books is **$3 a month** by default, and searching keeps working when it is reached. Default limits per student: 5 shelves, 20 books, 5,000 passages, 50 MB, 500 searches a day, 25 MB per file. Build the **class library** on the **Library** tab (only add books you may share). Scanned pages without text are skipped. The platform never shows you what a student's shelf contains, only counts and sizes.

**Traces, in a few words.** Open the **Traces** tab to see answers newest first and open one to see each step. A student's **Traces** card sets what is stored: **numbers only** (the default: no question or answer text is stored), **masked** text (emails, phone numbers and identifiers hidden) or **full** text; how long it is kept (30, 60 or 90 days, default 90, then deleted automatically); a daily limit; and whether the student can see their own app's traces (`npm run traces` in their project). **Download archive** produces one compressed file for all students or one, and a banner reminds you before traces expire. A daily table on the Traces tab counts answers, refusals, errors and cost; with the alert email set (section 13a) you get an email when refusals jump. Only store text for students whose users know their questions are recorded; the file you download may contain personal text.

**The compute worker (optional).** A small machine for heavy checks, off until you start it, teacher-only, about $4 a month. It is not part of a normal install and has no tasks yet. See [COMPUTE_WORKER.md](COMPUTE_WORKER.md) and install it only if you need it: `<package> plan --enable worker`.

**Saving your AI keys**""")

rep("Paste the OpenRouter management key and/or the Google Gemini key (press Enter to skip one).",
    "Paste the OpenRouter management key and/or the Google Gemini key (press Enter to skip one). The Google key is also what reads books for class knowledge.")

rep("Dependencies: `ai` needs `backend`; `gemini` needs `ai`; `storage` needs `backend`; custom domains need `backend`.",
    "Dependencies: `ai`, `storage`, `knowledge` and `traces` need `backend`; `gemini` needs `ai`; custom domains need `backend`. The feature names are `backend`, `ai`, `gemini`, `storage`, `knowledge`, `traces`, `customdomains` and `worker`; **`worker` is never part of the default** (it costs money while it exists): ask for it with `plan --enable worker`.")

rep("""## 14. All commands""", """## 13a. Alerts, cost and recovering a file

**Cost watch (do this once).** The install does **not** create a budget or alerts. In the AWS console create an **AWS Budget** for the account with an email alert (for example at $10 and $25 a month). The cost drivers are student traffic (there is no per-site traffic cap), AI use (capped per student by you) and stored data. Look at the **Usage** tab now and then.

**Error alerts (not available from the installer yet).** The platform can email one address when something is wrong (a crashed function, throttling, many server errors from student backends, the daily sweeper failing, and, with traces on, a jump in refusals). The address is read from an AWS Systems Manager String parameter whose **name** is passed to the stack as `AlertEmailParameter`; the installer has no option for it yet (tracked in [CRITICAL_ISSUES.md](CRITICAL_ISSUES.md)). Until it has, the alerts exist only on a platform whose stack was given that parameter by hand. Do not rely on alert emails for a class: check the teacher page, and keep the AWS Budget.

| Alert | Fires when | What to do |
|---|---|---|
| `lambda-concurrency-near-limit`, `lambda-throttles` | account-wide function concurrency is near the limit, or invocations were throttled | other apps in the account can be throttled; use **Backend: off** for the busiest student |
| `portal-function-errors`, `backend-gateway-errors` | platform code crashed | read that function's CloudWatch logs |
| `sweeper-errors` | the daily sweeper failed on a site or domain (it retries next day) | read the sweeper logs |
| `backend-5xx` | 20 or more server errors from student backends in 5 minutes | read the logs of the busiest `sd-*` function |

**Recovering a file (S3 versioning).** The site bucket keeps old versions: an overwritten or deleted site file is recoverable for **30 days**. Uploaded zips (`uploads/`) are removed after 1 day. The bucket name is the physical id of `SitesBucket` in the stack.
```bash
aws s3api list-object-versions --bucket <bucket> --prefix sites/alice/index.html --profile <name>
aws s3api copy-object --bucket <bucket> --key sites/alice/index.html --copy-source "<bucket>/sites/alice/index.html?versionId=<VersionId>" --profile <name>
# if the file was deleted: remove its DeleteMarker, which brings the previous version back
aws s3api delete-object --bucket <bucket> --key sites/alice/index.html --version-id <DeleteMarker VersionId> --profile <name>
```
Pages cached by CloudFront show the restored file after the cache expires or an invalidation.

**What a backup (`export`) contains, and what it does not.** It contains tokens and settings, the teacher login, stored student data and the published sites and files. It does **not** contain class knowledge (books, the class library) or traces: after an `import` those start empty and students add their books again. (This gap is listed in [CRITICAL_ISSUES.md](CRITICAL_ISSUES.md).)

## 14. All commands""")
open(p, "w", encoding="utf8").write(s)
print("ok")
