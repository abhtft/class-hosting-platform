import re

root = r"D:\Github_Codes\class-hosting-platform-github"


def read(p):
    return open(root + "\\" + p.replace("/", "\\"), encoding="utf8").read()


def write(p, s):
    open(root + "\\" + p.replace("/", "\\"), "w", encoding="utf8").write(s)


s = read("docs/TEACHER_SETUP.md")
a = s.index("**Cost watch (do this once).**")
b = s.index("| Alert | Fires when | What to do |")
new = """**Alerts and a cost budget (the install asks for them).** The guided install asks for an e-mail address and a monthly budget (default $10). It stores the address, creates an **AWS Budget** that e-mails at 80 percent and 100 percent of the amount and when 100 percent is forecast, and switches on the error alarms below. **AWS sends a confirmation e-mail to the address: open it and confirm, or nothing is delivered.** With flags: `install --alert-email you@example.com --budget 10`, or `--no-alerts` to skip. On a platform that was installed without them:
```bash
<package> alerts --email you@example.com --budget 10   # stores the address and creates the budget
<package> plan                                          # shows the alarms that would be added
<package> apply <plan id>
```
`verify` shows a warning (not a failure) when either is missing. The cost drivers are student traffic (there is no per-site traffic cap), AI use (capped per student by you) and stored data; look at the **Usage** tab now and then.

The platform e-mails the address when something is wrong: a crashed function, throttling, many server errors from student backends, the daily sweeper failing, and, with traces on, a jump in refusals.

"""
s = s[:a] + new + s[b:]
s = s.replace("| `install [--domain <d>] [--features a,b \\| none]` | guided install (also with flags and no questions); resumes if interrupted |",
              "| `install [--domain <d>] [--features a,b \\| none] [--alert-email <e> --budget <usd> \\| --no-alerts]` | guided install (also with flags and no questions); resumes if interrupted |\n| `alerts --email <e> [--budget <usd>]` | store the alert address and create the monthly budget on an existing platform, then `plan` and `apply` to switch the alarms on |")
write("docs/TEACHER_SETUP.md", s)

s = read("README.md")
s = s.replace("3. **Cost and alerts:** an idle platform costs close to nothing. The cost drivers are student traffic, AI use (capped per student) and stored data. **Set up the alert email and an AWS Budget** (see the guide); the install does not create alerts for you.",
              "3. **Cost and alerts:** an idle platform costs close to nothing. The cost drivers are student traffic, AI use (capped per student) and stored data. The install asks for an **alert e-mail and a monthly budget** (recommended): give an address you read, and open the confirmation mail AWS sends.")
s = s.replace("- **Alerts are optional and not set up by the installer** (see the guide).\n", "")
write("README.md", s)

s = read("CHANGELOG.md")
s = s.replace("## 2.3.1\n", "## 2.3.2 (unreleased)\n\n- **Alerts and a cost budget are part of the install.** The guided install now asks for an e-mail address and a monthly budget (default $10), stores the address, passes it to the stack so the error alarms exist, and creates an AWS Budget that e-mails at 80% and 100% of the amount and when 100% is forecast. Flags: `--alert-email`, `--budget`, `--no-alerts`. New command `alerts --email <address> [--budget <usd>]` for a platform that was installed without them (then `plan` and `apply` to add the alarms). `verify` shows a warning when either is missing, `destroy` removes the budget (and, with `--everything`, the stored address), and an update never clears an alert address the stack already has. AWS sends each address a confirmation e-mail that must be opened. **To apply:** `alerts --email you@example.com`, then `plan` and `apply <plan id>`; nothing changes for students.\n\n## 2.3.1\n", 1)
write("CHANGELOG.md", s)

s = read("docs/CRITICAL_ISSUES.md")
s = s.replace("### 1. The installer creates no alerts and no budget\n", "### 1. The installer creates no alerts and no budget (fixed in code, 2.3.2 unreleased)\n\n> **Status:** built and tested (`installer/engine.mjs`: `validateAlerts`, `putAlertEmail`, `ensureBudget`, `setupAlerts`; `setup.mjs alerts`). Not yet applied to a clean account (item 2). The text below describes the problem as found.\n", 1)
write("docs/CRITICAL_ISSUES.md", s)
print("ok")
