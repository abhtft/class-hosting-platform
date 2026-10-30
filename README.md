# Class Hosting Platform

**Let every student publish their own website, and optionally a small backend with class AI and storage, straight from their AI editor, on infrastructure you own.**

You run it in **your own AWS account** with one command. Students get a token, not AWS access. You decide, per student, which features are on and how much they may spend.

> **Status: public beta (2.0.x).** The install, update, student deploy, custom domains, backup/restore and the editor tools were tested end to end on real AWS accounts by the author. Not yet proven: moving a class to another account (`transfer`), classes of more than a few dozen students, regions other than `us-east-1`, and use by anyone but the author. Please read [Known limitations](#known-limitations) and tell us what breaks.

## What you get

| For the teacher | For the student |
|---|---|
| One command installs everything in your AWS account (about 15 minutes, mostly CloudFront) | A personal address `https://<name>.<your domain>/` (or a free `cloudfront.net` address) |
| A teacher page: create tokens, extend, take a site offline, switch features per student | A starter project made for AI editors, with rules the editor follows |
| Optional per student, **off until you switch them on**: a small backend, class AI with a dollar cap, class storage, the student's own domain | One command to publish; the site can also be uploaded from the browser as a zip |
| Updates by plan and apply, backup and restore, a clean `destroy` | The student's kit stays in step with your version automatically |

## Install (teachers)

You need an AWS account, and either **AWS CloudShell** (nothing to install: open it in the AWS console and paste the two lines below) or Node 20+ and the AWS CLI on your own computer.

```bash
PKG="https://github.com/abhtft/class-hosting-platform/releases/latest/download/class-platform-teacher.tgz?v=$(date +%s)"
npx -y "$PKG" install
```

(The `?v=` makes `npx` fetch the newest release; a PowerShell version and pinned versions are in the guide.) The install asks for your teacher login and whether you want your own domain, then builds the platform and checks that a sample site really goes live.

**Before you start, read these four things:**

1. **New AWS accounts:** AWS may refuse your first CloudFront distribution until it has verified the account, which can take **days**. If you are starting from a brand-new account, open the AWS Support case first.
2. **Access:** the installer needs broad AWS permissions (AdministratorAccess). Use a dedicated account or user, turn on MFA, and delete the access key when you are done. CloudShell avoids keeping a key on your computer.
3. **Cost:** an idle platform costs close to nothing; the cost drivers are student traffic, AI use (capped per student by you) and stored data. Add an AWS Budget with an email alert in the AWS console.
4. **Student content:** sites are served from **your** domain. You are responsible for what students publish. You can take any site offline from the teacher page.

Everything else is in the **[teacher guide](docs/TEACHER_SETUP.md)**: first class, features, custom domains, updates, backups, destroy and troubleshooting.

## Students

Students need a token from you and your portal address. The fastest route is the **Start here** card on the student page, which gives one command that connects an AI editor (VS Code or Antigravity) and sets up a project; they then run `npm run login` once **in each project** and ask the editor to publish. Needed on the student's computer: Node 22 and git, and an AI editor. With no setup at all, a student can also drag a built site (a folder or a zip) onto the student page.

Student guide: [docs/STUDENT_KIT.md](docs/STUDENT_KIT.md) and [student/README.md](student/README.md).

## Known limitations

- **Built for classes of a few dozen.** There are no bulk actions: tokens are created one at a time and each feature is switched on per student. No class-wide defaults yet.
- **One teacher login** per platform; no co-teachers or audit log.
- **No notifications:** regeneration requests and expiries show only when you open the teacher page.
- **Student backends are small by design:** one Python 3.12 or Node 22 function, standard library only (no package installs), 10 seconds per request, no SQL database (class storage is a key-value and file store with hard caps).
- **One class per platform.** Do not import a backup into a platform that already has students: it replaces the teacher login and can overwrite sites. See the guide.
- **`transfer`** (moving a class to another AWS account) has only been rehearsed with simulated accounts: treat it as experimental and keep your old account until the new one is verified.
- **No third-party security review** has been done.

## Security and privacy

- Report vulnerabilities **privately** through GitHub's "Report a vulnerability" on the Security tab. See [SECURITY.md](SECURITY.md).
- Nothing is sent to the maintainers: your data stays in your AWS account. If you switch class AI on, student prompts go to the AI provider you configure (OpenRouter or Google Gemini).
- Students never receive AWS credentials; tokens are random values stored only as hashes; destructive actions (delete, passwords, AI keys) are terminal-only and never available to an AI editor.

## Documentation

| Read this | For |
|---|---|
| [Teacher guide](docs/TEACHER_SETUP.md) | Install, run a class, update, back up, remove |
| [Student kit](docs/STUDENT_KIT.md) | The starter project, rules for AI editors, how the kit stays in sync |
| [Technical specification](docs/TECHNICAL_SPECIFICATION.md) | The source of truth: architecture, data model, limits, security |
| [Teacher architecture](docs/TEACHER_ARCHITECTURE.md) | How the installer, updater and teacher tools are built |
| [MCP](docs/MCP.md) | The editor tools and how tokens and updates are handled |
| [AI connector](docs/AI_CONNECTOR.md), [Storage connector](docs/STORAGE_CONNECTOR.md) | The optional per-student features |
| [API reference](docs/API_REFERENCE.md), [End to end](docs/END_TO_END.md) | Every route; the whole workflow |
| [AWS learnings](docs/AWS_LEARNINGS.md), [New account and transfer](docs/NEW_ACCOUNT_AND_TRANSFER.md) | Background and a worked account move |
| [Legacy manual setup](docs/LEGACY_MANUAL_SETUP.md) | The original SAM and deploy-key route, kept for reference |

## Contributing and testing wanted

Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). If you teach and have an AWS account, running the install and telling us what worked and what stopped you is the most useful thing you can do: open an issue using the **"I tried the install"** template.

## License

[MIT](LICENSE)
