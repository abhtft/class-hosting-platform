# Class Hosting Platform

**Let every student publish their own website, and optionally a small backend with class AI, storage and books to answer from, straight from their AI editor, on infrastructure you own.**

You run it in **your own AWS account** with one command. Students get a token, not AWS access. You decide, per student, which features are on and how much they may spend.

> **Status: public beta (2.4.0).** Install, update, student deploy, custom domains, backup and restore and the editor tools were tested end to end on real AWS by the author. **Not yet proven:** use by anyone but the author, classes of more than a few dozen students, regions other than `us-east-1`, and moving a class to another account. Read [Known limitations](#known-limitations) and [docs/STATUS.md](docs/STATUS.md), and tell us what breaks.

## What you get

| For the teacher | For the student |
|---|---|
| One command installs everything in your AWS account (about 15 minutes, mostly CloudFront) | A personal address `https://<name>.<your domain>/` (or a free `cloudfront.net` address) |
| A teacher page: tokens, extend, take a site offline, switch features per student, see usage | A starter project made for AI editors, with rules the editor follows; one command to publish |
| Updates by plan and apply, backup and restore, a clean `destroy` | A kit that keeps itself in step with your version |

**Optional features, each off for every student until you switch it on for that student:**

| Feature | What the student's app gets |
|---|---|
| **Backend** | One small Python or Node function behind the site (`/api/...`) with a monthly allowance of runs |
| **Class AI** | A capped, revocable AI key (OpenRouter or Google Gemini); you set the spending cap |
| **Class storage** | A small private database and file area with hard caps |
| **Class knowledge** | Books (PDF, text) the app answers from, with page and source, and "The books do not say." when unsure; plus a class library you maintain |
| **Background jobs** | Work longer than a web request (up to 15 minutes) |
| **Custom domain** | The student's own domain for their site |
| **Traces** | A step-by-step record of every answer (question, passages found, model call, cost), numbers only by default; you control how much text is kept |

**Project sync:** students keep their source files on the platform (`npm run push`, `npm run pull`), so the same code is on every computer they use, and the teacher can make **team projects**, all without GitHub accounts: [docs/PROJECT_SYNC.md](docs/PROJECT_SYNC.md).

An **optional compute worker** (one small machine, off until you start it, teacher-only) is available for heavy checks later: [docs/COMPUTE_WORKER.md](docs/COMPUTE_WORKER.md).

## Install (teachers)

You need an AWS account and either **AWS CloudShell** (nothing to install) or Node 20+ and the AWS CLI on your computer.

```bash
PKG="https://github.com/abhtft/class-hosting-platform/releases/latest/download/class-platform-teacher.tgz?v=$(date +%s)"
npx -y "$PKG" install
```

(The `?v=` makes `npx` fetch the newest release. A PowerShell version and pinned versions are in the guide.) The install asks for your teacher login and whether you want your own domain, builds the platform and checks that a sample site goes live.

**Read these four things first:**

1. **New AWS accounts:** AWS may refuse your first CloudFront distribution until it verifies the account, which can take **days**. Open the AWS Support case first.
2. **Access:** the installer needs broad AWS permissions (AdministratorAccess). Use a dedicated account, turn on MFA, delete the access key when done. CloudShell avoids keeping a key.
3. **Cost and alerts:** an idle platform costs close to nothing. The cost drivers are student traffic, AI use (capped per student) and stored data. The install asks for an **alert e-mail and a monthly budget** (recommended): give an address you read, and open the confirmation mail AWS sends.
4. **Student content:** sites are served from **your** domain and you are responsible for what students publish. You can take any site offline from the teacher page.

Everything else is in the **[teacher guide](docs/TEACHER_SETUP.md)**.

## Students

A student needs a token from you and your portal address. The **Start here** card on the student page gives one command that connects an AI editor (VS Code or Antigravity) and sets up a project; they run `npm run login` once in each project and ask the editor to publish. They need Node 22, git and an AI editor. Without any setup a student can also drag a built site (folder or zip) onto the student page.

Student guide: [student/README.md](student/README.md) and [docs/STUDENT_KIT.md](docs/STUDENT_KIT.md).

## Known limitations

- **Built for classes of a few dozen.** Tokens are created one at a time and features are switched on per student; there are no class-wide defaults.
- **Backend capacity follows your AWS account:** a new account allows only 10 concurrent function executions, so about 3 student requests run at once until you ask AWS for more (free: Service Quotas, AWS Lambda, request 1000). The install reads the limit and sizes the caps; `verify` warns until the account can serve a class of 30. See [docs/TEACHER_SETUP.md](docs/TEACHER_SETUP.md) (section 13a).
- **One teacher login** per platform; no co-teachers and no audit log.
- **Student backends:** one Python 3.12 or Node 22 function, standard library only, 10 seconds per request (25 with the class AI), no SQL database.
- **One class per platform.** Do not import a backup into a platform that already has students. A backup includes books but not traces (download those from the Traces tab).
- **`transfer`** (moving a class to another AWS account) was only rehearsed with simulated accounts: experimental.
- **No third-party security review** has been done.

## Security and privacy

- Report vulnerabilities **privately** via GitHub's "Report a vulnerability" (Security tab). [SECURITY.md](SECURITY.md) also shows how to verify a release (signed with Sigstore) and lists the automated checks.
- Nothing is sent to the maintainers. Your data stays in your AWS account. If class AI is on, student prompts go to the provider you configure (OpenRouter or Google).
- Students never receive AWS credentials; tokens are random values stored only as hashes; destructive actions (delete, passwords, AI keys) are terminal-only and never available to an AI editor.

## Documentation

The **[documentation map](docs/README.md)** has a one-page overview and says what to read for what. The main ones:

| Read | For |
|---|---|
| [Teacher guide](docs/TEACHER_SETUP.md) | Install, run a class, features, alerts, update, back up, remove |
| [Student guide](student/README.md) and [Student kit](docs/STUDENT_KIT.md) | What students do; how the kit works |
| [Project status](docs/STATUS.md), [Critical issues](docs/CRITICAL_ISSUES.md), [Roadmap](docs/ROADMAP.md) | Where the project stands, what blocks a wider release, what comes next |
| [Technical specification](docs/TECHNICAL_SPECIFICATION.md) | The source of truth: architecture, data model, limits, security |
| [API reference](docs/API_REFERENCE.md) | Every route |

## Contributing and testing wanted

Issues and pull requests are welcome; the steps are in [CONTRIBUTING.md](CONTRIBUTING.md). If you teach and have an AWS account, running the install and telling us what worked and what stopped you is the most useful thing you can do: open an issue with the **"I tried the install"** template.

## License

[MIT](LICENSE)
