# Teacher guide: install, run and keep up to date your own class platform

You run the platform in **your own AWS account**. Everything a teacher does (install, check, update, add student custom domains, back up, move to another account, remove) is done with **one package** that you run with `npx` from a GitHub release. Nothing is cloned and nothing is installed permanently.

This guide is for teachers. How it is built is in [TEACHER_ARCHITECTURE.md](TEACHER_ARCHITECTURE.md); what changed in each version is in [../CHANGELOG.md](../CHANGELOG.md).

**Contents**
1. [What you get, and what it costs](#1-what-you-get-and-what-it-costs)
2. [How to run a command (the newest version, or a pinned one)](#2-how-to-run-a-command)
3. [Before you start](#3-before-you-start)
4. [Install](#4-install)
5. [Your first class: teacher login, tokens, students](#5-your-first-class-teacher-login-tokens-students)
6. [Features: what each one is and how to switch it on](#6-features)
7. [Custom domains for students](#7-custom-domains-for-students)
8. [Keep it up to date](#8-keep-it-up-to-date)
9. [Check that it works: verify](#9-check-that-it-works-verify)
10. [Use your AI editor: the teacher tools](#10-use-your-ai-editor-the-teacher-tools)
11. [Back up, and move to another AWS account](#11-back-up-and-move-to-another-aws-account)
12. [Start over or remove everything](#12-start-over-or-remove-everything)
13. [If something goes wrong](#13-if-something-goes-wrong)
14. [All commands](#14-all-commands)
15. [What the tool will never do](#15-what-the-tool-will-never-do)

---

## 1. What you get, and what it costs

After the install you have, in your AWS account:

| Part | What it is for |
|---|---|
| **Teacher page** (`https://deploy.<your domain>/teacher.html`) | create student tokens, switch features on per student, see usage |
| **Student sites** (`https://<student>.<your domain>/`) | each student publishes their website; it is served from S3 through CloudFront |
| **Student kit and editor tools** | what a student downloads or connects to, served from your own portal |
| **Optional features**, each off for every student until you switch it on for that student | a small backend, class AI (OpenRouter or Google Gemini), class storage, the student's own domain |

**Cost.** An idle platform costs close to nothing (S3, DynamoDB on demand, CloudFront and Lambda only when used). The cost drivers are student traffic, AI use (capped per student by you) and stored data. Choose **pay-as-you-go** for CloudFront (the "Switch to a plan" banner in the AWS console is an optional monthly bundle; a class does not need it) and add an **AWS Budget** with an email alert in the AWS console. Student custom domains use CloudFront multi-tenant tenants: check their price in your AWS billing console.

## 2. How to run a command

Every command in this guide starts with `npx -y <package>`, where `<package>` is the release file on GitHub. There are two ways to write it.

**The newest version (recommended).** Always fetches the newest release. `npx` remembers any address it has downloaded, so a plain `latest` link can keep running an old copy; adding a changing `?v=` makes it fetch fresh every time (GitHub ignores the query):

Linux, macOS, AWS CloudShell:
```bash
PKG="https://github.com/abhtft/class-hosting-platform/releases/latest/download/class-platform-teacher.tgz?v=$(date +%s)"
npx -y "$PKG" version
```
Windows PowerShell:
```powershell
$PKG = "https://github.com/abhtft/class-hosting-platform/releases/latest/download/class-platform-teacher.tgz?v=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
npx -y $PKG version
```
Run that line once per terminal window, then use `npx -y "$PKG" <command>` (PowerShell: `npx -y $PKG <command>`). `version` prints the version you got, so you can confirm it.

**A pinned version** (to repeat a result, follow a guide written for one version, or go back after a problem):
```bash
PKG=https://github.com/abhtft/class-hosting-platform/releases/download/v1.2.3/class-platform-teacher-1.2.3.tgz
```
Replace `1.2.3` with the version you want (all versions are on the repository's Releases page, each with its changelog and checksum).

In the rest of this guide `<package>` means `npx -y "$PKG"` and `--profile <name>` is the AWS profile of the account (see section 3). Add `--dry-run` to the commands that change things to see every AWS command without running any.

## 3. Before you start

| You need | Notes |
|---|---|
| An **AWS account** | A brand new account may refuse its first CloudFront distribution until AWS verifies it. The installer detects this and says so; open an AWS Support case ("Account and billing") and ask them to verify the account for CloudFront. |
| **Node 20 or newer** and the **AWS CLI**, signed in | **Easiest: AWS CloudShell** (section 3.1): both are already there. On your own computer: Node from https://nodejs.org/ and the AWS CLI with `aws configure` using an IAM user that has **AdministratorAccess**. |
| Optionally, **a domain you control** | Without one the platform works on free `cloudfront.net` addresses, which is best for a first try. With one, students get `<name>.yourdomain` addresses and the portal is at `deploy.yourdomain`. |

You do **not** need the SAM CLI, Docker, Python, git or an npm account.

### 3.1 AWS CloudShell (nothing to install)
Sign in to the AWS console and open **CloudShell** (the terminal icon at the top). It is already signed in as you, so there is no access key and no profile. It needs Node 20+: check `node --version`; if older, run `sudo dnf install -y nodejs22` (the installer prints this hint too). If the window closes during the 10 to 15 minute build, open CloudShell again and run the same install command: it carries on.

### 3.2 On your own computer
Create an IAM user with **AdministratorAccess** (IAM, Users, Create user), then an access key (Security credentials, Create access key, use case "Command Line Interface"), and run `aws configure --profile mine`. Use that profile name wherever this guide says `--profile <name>`. Use a dedicated user, turn on MFA for it, and delete its access key when you no longer need it. Or let `setup` (section 10) save the key for you.

## 4. Install

```bash
<package> install
```

It asks only what has to be decided now:

1. **Which AWS account**: only when your computer has several profiles and none was chosen (never in CloudShell). Before it creates anything it shows the **AWS account number and user** and asks you to confirm.
2. **Use your own domain?** (default: no). A domain cannot be added to an existing install later, so this is the one optional choice asked now. If yes: the domain, for example `apps.example.com` (student sites become `<name>.apps.example.com`, the portal `deploy.apps.example.com`).
3. **Teacher login name and password** (typed twice, hidden).
4. **Tell you when a new version is released?** (default yes). A notice only; nothing ever updates by itself.

Then it builds the platform (10 to 15 minutes, because CloudFront is slow), publishes the portal pages, the student kit and the student editor tools, and runs the full check (section 9): a throwaway student publishes a small sample site that must come up live. **Every feature is installed** (backends, class AI, Gemini, class storage, custom domains); each stays off for every student until you switch it on for that student.

If the window closes in the middle, run the same command again: it waits for or picks up the stack AWS was building. If it prints **a note that custom domains could not be set up**, the rest still works; see section 7.

### 4.1 Installing with your own domain: three DNS records
With a domain there are two pauses, because DNS belongs to you. Over the whole install you add **three records** at your DNS host:

| # | When | Record | Purpose |
|---|---|---|---|
| 1 | Printed first | CNAME `_abc….<domain>` pointing at an `….acm-validations.aws.` value | lets AWS issue the certificate for `*.<domain>` |
| 2 | After the build | CNAME `*.<domain>` pointing at a `….cloudfront.net` address | all student sites |
| 3 | After the build | CNAME `deploy.<domain>` pointing at a different `….cloudfront.net` address | the portal |

Steps:
1. The first run prints record 1 and stops. Add it, wait a few minutes (AWS checks within minutes), then **run the same command again with the domain**: `<package> install --domain <your domain>` (add `--profile <name>` if you use one).
2. The build runs. It prints records 2 and 3. Add both.
3. Run `<package> finish-domain` (in the guided mode it asks you to press Enter instead). It attaches the domain to the portal and publishes the pages.
4. Set your teacher login if the installer did not (section 5), then run `verify`.

**Tips for any DNS host** (Cloudflare, GoDaddy, Route 53, ...):
- Enter the **name without the domain ending** if your host adds it for you (for the first record, `_abc…publish`, not `_abc…publish.example.com`).
- Enter the value exactly as printed (a trailing dot is fine).
- At **Cloudflare** the records must be **DNS only** (grey cloud), never proxied (orange).
- Check a record from the authoritative server, which updates at once: `nslookup -type=CNAME <name> <one of your DNS host's nameservers>`.

## 5. Your first class: teacher login, tokens, students

1. **Teacher login.** The guided install sets it. If you installed with flags, or want a new password: `<package> teacher <name>` (password typed twice, hidden). A new login can take about a minute to work.
2. Open `https://deploy.<your domain>/teacher.html` (or the `cloudfront.net` address printed at the end of the install) and sign in.
3. **New token**: choose a student name and the number of days. The token is shown **once**; copy it, or use **Copy message for the student**.
4. The student opens your portal's home page, signs in with the token, and follows the **Start here** card: one command connects their AI editor, and they say "set up my class project" and "publish my site". A token that expires stops the student's pipeline; the student asks for regeneration and you approve it on the **Requests** tab.
5. In the student's panel on the teacher page you can **Extend**, take the site **offline** or **revoke** the token, and switch features on (next section).

## 6. Features

Every feature is **installed**; **nothing is on for a student until you switch it on** for that student in their panel. So having all of them available costs almost nothing.

| Feature | What the student gets | What you need |
|---|---|---|
| **Backend** | one small server-side function behind their site (`/api/...`), with a monthly allowance of runs that you set | nothing extra |
| **Class AI** | a capped, revocable AI key their backend uses (their code needs no key of its own): OpenRouter, or Google **Gemini** (`gemini-3.5-flash-lite`); you set the spending cap and period per student | your own AI keys, saved once (below) |
| **Class storage** | a small private database and file area for their backend, with hard size caps | nothing extra |
| **Custom domain** | their own domain for their site | see section 7 |

**Saving your AI keys** (hidden prompts, stored in AWS Secrets Manager, never in a file):
```bash
<package> secrets
```
Paste the OpenRouter management key and/or the Google Gemini key (press Enter to skip one). Until the keys are saved, switching AI on for a student answers with a plain message ("the key is not set up yet... use the secrets command"), not an error.

**Choosing which features the platform has.** The default installs all of them. To install fewer, use `install --features backend,storage` (or `--features none`). To change later:
```bash
<package> plan --enable ai,gemini          # shows the change and a plan id (also adds what they depend on)
<package> apply <plan id>                  # runs exactly that plan
<package> plan --disable storage           # and the same for removing one
```
Dependencies: `ai` needs `backend`; `gemini` needs `ai`; `storage` needs `backend`; custom domains need `backend`.

## 7. Custom domains for students

A student can use **their own domain** (for example `www.theirname.com`) for their site. It is part of the install (CloudFront multi-tenant: two small functions, a connection group and one multi-tenant distribution, which the installer creates and `destroy` removes). There is one address that matters: the platform's **custom-domain address** (a `….cloudfront.net` name, also called the routing endpoint). It is shown on the student's page and printed by the setup (`Students point a CNAME at <address>`).

### 7.1 Steps
1. On the teacher page, in the student's panel, switch **Custom domain** on.
2. The student opens their page and sees the **Custom domain** card. They own a domain (any DNS host). They add **one CNAME**, for a *subdomain* of their domain, pointing at the **custom-domain address from their page**.
3. **Only after the CNAME exists**, the student types the domain into the card and presses Save.
4. The card moves on its own through:

| What the card says | What is happening |
|---|---|
| "Waiting for your DNS record. Add the CNAME shown below" | the CNAME is not yet seen at the custom-domain address |
| "DNS found. Issuing your HTTPS certificate (usually 1-5 minutes)" | CloudFront is issuing a free certificate |
| "Certificate ready. Finishing setup (usually 1-5 minutes)" | the domain is being attached |
| "Your custom domain is live." | `https://<their domain>/` now shows their site |

### 7.2 What to tell students
- Use a subdomain such as `www.` or `app.`; a **bare domain** (`theirname.com`) cannot be a CNAME at most DNS hosts (Cloudflare and a few others can flatten it).
- At **Cloudflare** the record must be **DNS only** (grey cloud).
- The CNAME must exist **before** saving the domain. CloudFront checks ownership first.
- The domain must **not** be under your platform's own domain (`something.apps.example.com`): those names are already served by the platform.
- To remove it, the student uses the remove button on the same card; then they can delete their CNAME.

### 7.3 The most common mistakes
| Message or symptom | Cause and fix |
|---|---|
| "CloudFront refused this domain: The specified domain name isn't valid. Update the domain name to point to the expected CloudFront resource." | The CNAME does not point at the **custom-domain address**. The usual slip is using the platform's *site* address (the target of your `*.<domain>` record) instead. Edit the CNAME to point at the address shown on the student's page, wait a minute, Save again. Check what DNS really answers with `nslookup -type=CNAME <their domain> <authoritative nameserver>`: it must print the custom-domain address. |
| The card stays on "Waiting for your DNS record" | The record is missing, has a typo, or is proxied. At Cloudflare set **DNS only**. |
| The Custom domain switch is missing from the student's panel | Custom domains are not set up on this platform yet: run `<package> custom-domains` (about 10 minutes, safe to repeat). |
| "Owner not enabled for you" on the student's page | You have not switched Custom domain on for that student. |

### 7.4 Setting it up, retrying or removing
- The install sets it up. If that step failed (it never fails the install; it prints a note), or your platform was installed before this existed: `<package> custom-domains` (about 10 minutes; it finds what exists and resumes).
- To switch it off completely: `<package> custom-domains --disable` (asks first; every student's custom domain stops working and the CloudFront pieces are deleted; their DNS records are theirs to remove). AWS keeps one empty, automatic default connection group that cannot be deleted; it costs nothing.
- `destroy` removes these pieces before the stack, and `transfer` recreates them in the new account.

## 8. Keep it up to date

You are told about a new version in three ways (if you said yes at install): the notice in `status`, the "Teacher tools" notice in your editor, and the changelog on GitHub. **Nothing ever updates by itself.**

```bash
<package> status                    # the version your account runs, the features, and whether a newer release exists
<package> plan                      # what updating to this package's version would change (changes nothing)
<package> apply <plan id>           # runs exactly that plan, then republishes the portal pages, student kit and editor tools
<package> upgrade                   # plan, show, ask, apply in one go
```

Run these with the **newest** package (section 2); a pinned older copy will tell you a newer one exists.

Safety rules that are enforced:
- An update that would **replace or remove** a table, bucket, secret, parameter or distribution is **refused**; nothing is changed.
- `apply` only runs the plan that was shown, by its id; if the change set changed or expired, you get a new plan.
- New features arrive off for every student; the custom-domain ids and every setting you have are carried over.
- Students learn about a new student kit themselves (`npm run check` and their editor tool say so) and update with one yes.

After an update, move your editor tools to the same version (the `apply` message prints the exact command), then restart the editor:
```bash
<package> editor-update              # or ask the AI: "update my editor tools"
```

### 8.1 Your students follow your version, and how to go back
The student kit (the rules for their AI editor, `tools/`, the deploy script and the class AI and storage helpers) comes from **your** portal, and a student's project **keeps itself in line with it**. Before a `deploy` or a `status`, the project compares its own kit files with the ones your portal publishes; if any differ it replaces them first (nobody is asked), keeps a copy of every replaced file in the project's `.kit-backup/` folder, and then carries on. Only the kit's own files change, never the student's code. A student who edited a kit file loses that edit (the copy is in `.kit-backup/`), which is the point: every student behaves the way your platform expects. So after you update (section 8), students follow on their next command.

A student's token is **per project**: they run `npm run login` inside each project, and it is saved in that project's `.env.local`. There is no computer-wide token, so two windows can never end up signed in as the same student by accident.

**If a new student kit causes trouble, go back.** Publish the previous release again; every student's next command syncs back to it:
```bash
npx -y https://github.com/abhtft/class-hosting-platform/releases/download/v<previous version>/class-platform-teacher-<previous version>.tgz publish --profile <name>
```
(`upgrade` and `apply` print this exact command, with your previous version filled in.) This puts back the portal pages, the student kit and the editor package; it does not change the AWS stack. Try each release on a throwaway student first.

A project made with a much older kit (before 1.8.0) cannot do this by itself yet: its student runs `npm run platform -- update --apply` once, and from then on it follows you.

## 9. Check that it works: verify

```bash
<package> verify
```
Asks for your teacher login (used once, never saved), then acts like a student and a visitor would, with a throwaway student it creates and removes again:
- the portal pages, the student kit and the editor package are published;
- the teacher page and student pages refuse a visitor without a login;
- your teacher login works; a student token can be created and used;
- every feature the platform has is offered to students;
- a **sample site is published and is live** on your own address, and a site that does not exist is refused.

Each line is `ok` or `FAIL` with the reason. It leaves nothing behind. Run it after an install, an update, a feature change or a transfer. `verify --no-login` runs only the public checks.

## 10. Use your AI editor: the teacher tools

Do everything above by talking to your AI editor, the same way students do. One command, once per computer:
```bash
<package> setup
```
It asks which AWS profile to use, or lets you paste an AWS access key (hidden) that it saves as its own profile `class-platform` (after a backup of your AWS credentials file; your other profiles are never touched); shows the AWS account and asks you to confirm; then adds the teacher tools to **VS Code** and **Antigravity** (after a backup of their settings, one entry called `class-platform-teacher`, pinned to this version). In CloudShell there is nothing to save. Restart the editor, then say things like:

- "Check my AWS account for the class platform"
- "Verify the platform" (public checks; the full check needs your password in a terminal)
- "A new version is out: plan an update", then "apply it"
- "Turn on class storage"
- "Update my editor tools" (moves its own entry to the newest release, after a yes and a backup)

| Tool | Does | Asks first |
|---|---|---|
| `check_environment`, `status`, `verify`, `plan_update`, `job_status` | read, check or explain | no |
| `install`, `finish_domain`, `apply_update`, `publish_portal`, `update_editor_tools` | change AWS or the editor settings (installs and updates run in the background; `job_status` shows progress) | **yes** |
| `teacher_login` | explains how to set the password | n/a |

The AI never sees a password or key: those are typed in your own terminal. The editor tools cannot delete the platform, take a password or key, or touch IAM users. Anything destructive or secret is a terminal command (section 15).

## 11. Back up, and move to another AWS account

**Back up** everything a class has made:
```bash
<package> export my-backup
```
Writes a new private folder with the student tokens and settings, stored student data, published sites and files, the teacher login and the student key map. It holds student AI keys and the teacher login hash: keep it private and delete it when you no longer need it. It does not hold your AI keys secret (use `secrets`), student backend functions or custom-domain registrations. `<package> import my-backup` adds a backup to the platform in the account you are signed in to; it deletes nothing and is safe to repeat.

**Move the class to another AWS account:**
```bash
<package> transfer --from <old profile> --to <new profile>
```
It shows both accounts and asks you to **type both account numbers** and your **teacher login**, then, in order: installs the same platform (same features, same domain, custom domains included) in the new account; copies the data (the old account is only read); runs the full check on the new platform; and writes **`class-transfer-dns.csv`** (open it in a spreadsheet; in CloudShell use Actions, Download file) with every DNS change: first the certificate record (add it before anything else), then the two records you switch (`deploy.<domain>` and `*.<domain>`) with old and new values, then one row per student who uses their own domain, with the new custom-domain address.

If your domain needs the certificate record, the first run stops after printing it: add it, wait a few minutes, run the same command again. Stop students from publishing during the move; anything published after the copy is not included. After the new platform passed its check and you switched DNS, run the same command once more with `--finish` to attach your domain. Keep the old account about a week, then remove it with `destroy`. Student backend functions are not copied (students publish again); each student with a custom domain registers it again on their page after changing the CNAME to the new address. Your AI keys are not copied: run `secrets` for the new account.

*Transfer, export and import were tested with simulated accounts; rehearse them (export, destroy, install, import) before you rely on them for a real class.*

## 12. Start over or remove everything

```bash
<package> destroy                    # remove the platform; keeps student data and your AI keys
<package> destroy --everything       # for a test account: also removes the student data, parameters and AI keys
```
First it shows what it will remove (students, distributions, buckets, functions, tables), then asks for your **teacher login** (checked against your portal; if the portal does not answer, the typed phrase still applies) and for you to type the stack name (or `delete everything`). It removes the custom-domain pieces first, empties the sites bucket including every old version, deletes the stack (CloudFront takes 15 to 25 minutes), removes the installer's staging bucket and its saved state. **Your DNS records are yours to remove.** A "start over" is `destroy --everything`, then `install`.

## 13. If something goes wrong

| Message or symptom | What to do |
|---|---|
| "AWS has not verified this account for CloudFront yet" | Open an AWS Support case (Account and billing); retry when AWS confirms. |
| "not allowed to use CloudFront" or `AccessDenied` | Attach AdministratorAccess to the IAM user (a user with no policy can sign in but every call fails). |
| "The AWS CLI is not installed" or "could not sign in" | Install the AWS CLI and run `aws configure`, or use CloudShell. |
| "The stack ... already exists" | The platform is already installed: use `status`, `plan`, `upgrade`. To start over: `destroy --everything`. |
| "failed state (ROLLBACK_COMPLETE)" | `<package> destroy`, then install again. |
| A window closed during the install | Run the same install command again; it carries on. |
| The certificate stays pending | The validation record is missing, mistyped or proxied (Cloudflare: DNS only). AWS checks again within minutes. |
| The install says custom domains could not be set up | The rest works. Run `<package> custom-domains` later; it resumes. |
| `finish-domain` reports an alias conflict | Another CloudFront distribution (maybe in another AWS account) already claims that name or an overlapping wildcard. Use a different domain for this platform, or remove the old claim. |
| `npx` runs an old version | You used a plain `latest` link that `npx` remembered. Use the newest-version command with `?v=` (section 2), or a pinned address, or run `npm cache clean --force` once. |
| A command printed `node setup.mjs ...` | That form only exists in a git clone; in the package use `npx -y "$PKG" <command>`. |
| Switching AI on says the key is not set up | Run `<package> secrets` and paste your key(s). |
| `verify` fails on "The teacher can sign in" right after setting the login | A new login takes up to a minute; wait and run `verify` again. |
| A plan is **Blocked** | The update would replace a data-holding resource. Nothing changed; open an issue with the plan. |
| A student's site does not update | Ask the student to publish again; CloudFront refreshes within a minute. If a student reaches the old version, check their token has not expired. |

Open an issue on the repository with the version (`version`) and the exact message (never paste passwords, tokens or keys).

## 14. All commands

`<package>` = `npx -y "$PKG"`. Common flags: `--profile <name>` (which AWS profile), `--dry-run` (show every AWS command, change nothing), `--yes` (skip the confirmation; for scripts).

| Command | What it does |
|---|---|
| `install [--domain <d>] [--features a,b \| none]` | guided install (also with flags and no questions); resumes if interrupted |
| `finish-domain` | step two of a domain install, after the DNS records are added |
| `teacher <name>` | set or reset the teacher login (password typed twice, hidden) |
| `secrets` | save your OpenRouter and/or Gemini keys in AWS Secrets Manager (hidden) |
| `status` | the version the account runs, features, portal address, whether a newer release exists |
| `plan [--enable a,b] [--disable a]` | show what an update or a feature change would do; changes nothing |
| `apply <plan id>` | run exactly that plan, then republish the portal, student kit and editor tools |
| `upgrade` | plan, show, ask, apply |
| `verify [--no-login]` | the full outside-in check with a sample site |
| `custom-domains [--disable]` | set up (or remove) what students need to use their own domain |
| `publish` | republish the portal pages, student kit and editor tools |
| `export <folder>` / `import <folder>` | back up a class / add a backup to a platform |
| `transfer --from <p> --to <p> [--csv f] [--finish]` | move the class to another AWS account |
| `setup` | one-time: save your AWS access and add the teacher tools to your editor |
| `editor`, `editor-update` | add the teacher tools to your editor / move them to the newest release |
| `check` | is this computer and AWS account ready |
| `destroy [--everything]` | remove the platform (typed confirmation and teacher login) |
| `version`, `help`, `eject <folder>` | print the version; list commands; copy the platform into a folder you can change with git |

## 15. What the tool will never do

- It never changes your AWS account without showing the account number first and asking, and never deletes anything without a typed phrase (and your teacher login) in **your own terminal**.
- It never takes a password or API key through your editor or the AI; those are typed in a terminal and go straight to AWS (or are hashed).
- It never updates itself, and an update that would replace or remove data is refused.
- It never copies data out of the old account when moving: the old account is only read.
- Your AWS access key (if you use `setup`) is saved only in the standard AWS credentials file under its own profile, after a backup; it is never printed.
