# Install and update your own copy (teacher guide)

You run the platform in **your own AWS account**, from **one command**; nothing is cloned. This page covers the first install, keeping it up to date, switching features on, and doing all of it from your AI editor with the teacher MCP.

```bash
npx -y https://github.com/abhtft/class-hosting-platform/releases/latest/download/class-platform-teacher.tgz install    # first install (guided)
npx -y https://github.com/abhtft/class-hosting-platform/releases/download/v<new>/class-platform-teacher-<new>.tgz upgrade   # later, when a new release is out
```

`abhtft/class-hosting-platform` is the repository you got this from (its README shows the exact line). The release file is the whole platform, so there is no clone and no npm account; `npx` only needs Node. Pin a version (`.../download/v1.0.0/...`) when you want exactly that version; "latest" is for the first install. Everywhere below, **`<package>`** stands for that address, and `node setup.mjs <command>` (from a git clone) means the same thing. Your install state is kept in `~/.class-platform-teacher` and can always be rebuilt from the AWS stack, so a new computer just works. Technical background: [NEW_ACCOUNT_AND_TRANSFER.md](NEW_ACCOUNT_AND_TRANSFER.md). What changed in each version: [../CHANGELOG.md](../CHANGELOG.md).

## 1. The simplest way: AWS CloudShell (nothing to install)

1. Sign in to the **AWS console** with your own AWS account and open **CloudShell** (the terminal icon at the top of the console). It is already signed in as you: no AWS CLI to install, no access key, no profile.
2. Paste the install line from the top of this page and answer the questions.

CloudShell needs Node 20 or newer. Check with `node --version`; if it is older, run `sudo dnf install -y nodejs22` first (the installer prints this hint too). If the window closes during the 10 to 15 minutes of building, open CloudShell again and run the same command: it carries on from where it was.

New AWS accounts can refuse their first CloudFront distribution until AWS verifies them (an AWS Support case, "Account and billing"). The installer detects this and says so.

## 2. Or from your own computer

You need Node 20+ (https://nodejs.org/) and the AWS CLI signed in (`aws configure`, with an IAM user that has **AdministratorAccess**). You do **not** need the SAM CLI, Docker, Python or git: the package carries the platform. (Contributors and people who want to change the code use `git clone`, or `npx ... eject <folder>`.)

### What the install asks

```bash
npx -y <package> install
```

Only what has to be decided now:
1. **Which AWS account**: asked only when your computer has several AWS profiles and none was chosen (never asked in CloudShell). **Before it creates anything it shows the AWS account number and user it will use and asks you to confirm.**
2. **Your own domain?** (default no: free test addresses on `cloudfront.net`, everything can still be tried). A domain cannot be added to an install later, so this is the one optional choice asked now.
3. **Your teacher login name and password** (typed twice, hidden).
4. **Tell you when a new version is released?** (default yes). A notice only: nothing ever updates by itself.

Then it checks the account, builds the platform (10 to 15 minutes, CloudFront is slow), publishes the portal pages, the student kit and the MCP package, and **runs the full check**: a throwaway student publishes a small sample site, which must come up live (see `verify` below). Try `<package> install --dry-run` first to see every AWS command it would run, without running any.

**Every feature is installed** (student backends, class AI, Google Gemini, class storage). Nothing changes for a student until **you switch a feature on for that student** on the teacher page, so having them all available costs almost nothing. The two AI features need your keys: save them once with `<package> secrets` (hidden prompts); until then, switching AI on for a student answers with a plain instruction instead of an error. To install fewer, use `--features backend,storage` (or `--features none`); to change later, `<package> plan --disable ai` (or `--enable ...`, several names separated by commas) and then `apply <id>`. Other flags: `--domain apps.example.com --profile name`.

**With a domain** (for example `apps.example.com`) there are two pauses, because DNS is yours:
1. It requests a certificate and prints **one DNS record**; add it at your DNS host, wait a few minutes, run the same command again.
2. After the first deploy it prints **two DNS records** (`*.apps.example.com` for student sites, `deploy.apps.example.com` for the portal); add them, press Enter, and it attaches the portal address.

When it ends it prints your portal address. Sign in at `<portal>/teacher.html`, create a student token, and give the student the **Start here** command on their page.

### Check that it really works: `verify`

```bash
<package> verify
```

Asks for your teacher login (used once, never saved), then acts like a student would, with a throwaway student it creates and removes again:
- the portal pages, the student kit and the MCP package are published;
- the teacher list and the student pages refuse a visitor without a login;
- your teacher login works and a student token can be created and used;
- a **sample site is published and is live** on your own address (`sample...` in a domain install, or the free address), and a site that does not exist is refused;
- every feature you switched on is offered to students.

It prints each check as `ok` or `FAIL` with the reason, and leaves nothing behind. Run it after an install, after an update, and after moving to another account. `verify --no-login` runs only the public checks (this is also what the editor tool does, because passwords are never typed into an editor).

### Starting over (testing)

```bash
<package> destroy --everything
```

First it shows exactly what it will remove (number of students, distributions, buckets, functions, tables), then asks for your **teacher login name and password** (checked against your portal; if the portal does not answer, the typed confirmation still applies) and for you to type `delete everything`. Then it deletes the platform and every leftover (all bucket versions, the student data table, parameters, the installer's staging bucket and the AI keys secret), so the account is as before the install and `install` can run again. Without `--everything`, `destroy` keeps the student data table and the AI keys on purpose and tells you what it kept. Your DNS records are yours to remove.

## 3. Keep it up to date

```bash
npx -y <package-of-the-new-version> upgrade
```

The teacher page and `status` tell you when a newer release exists and print this exact command. After an upgrade, run the same release's `editor` command once (`apply` and `status` print it), then restart the editor: the teacher tools in your editor stay on the version they were set up with until you do, just as a student re-runs the setup command to update theirs. (With a git clone: `git pull`, then `node setup.mjs upgrade`.) `upgrade` shows what would change in plain words ("adds 1, modifies 3, replaces no data"), asks, applies exactly that plan and republishes the portal, kit and MCP package. Safety rules it enforces:
- New features arrive **switched off**; nothing changes for you until you turn them on.
- An update that would replace or remove a data-holding resource (tables, buckets, secrets, distributions) is **refused** and nothing is changed.
- `apply` only runs the plan that was shown, by its id. If the change set changed or expired, you get a new plan.
- Students learn about the new kit by themselves (`npm run check` and the MCP `status` tool tell them) and update with one yes.

`node setup.mjs status` says which version your account runs and which features are on. `node setup.mjs plan` shows an update without applying it.

## 4. Switch features on or off

```bash
node setup.mjs plan --enable ai        # shows the change and a plan id; also turns on what ai needs
node setup.mjs apply <plan-id>
```

Features: `backend` (a small server per student), `ai` (OpenRouter, needs `backend`), `gemini` (Google Gemini, needs `ai`), `storage` (small database and files, needs `backend`). AI keys are added with `node setup.mjs secrets` (hidden prompts). After enabling, switch each student on from the teacher page.

## 4a. Back up, and move to another AWS account

**Back up** everything a class has made:

```bash
<package> export my-backup
```

Writes a new private folder with the student tokens and their settings, the stored student data, the published sites and files, the teacher login and the student key map. The folder holds student AI keys and the teacher login hash: keep it private and delete it when you no longer need it. It does not hold the AI keys secret (re-enter it with `secrets`), student backend functions or student custom domains. `<package> import my-backup` adds a backup to the platform in the account you are signed in to; it deletes nothing and is safe to repeat.

**Move the class to another AWS account:**

```bash
<package> transfer --from <old profile> --to <new profile>
```

It needs a profile for each account. It shows both accounts and asks you to **type both account numbers** and your **teacher login**, then, in order:
1. installs the same platform (same features and domain) in the new account;
2. copies the data across (the old account is only read, never changed);
3. runs the full check on the new platform (a throwaway student publishes a sample site);
4. writes **`class-transfer-dns.csv`**: every DNS change the move needs, to open in a spreadsheet. Columns: `who, name, type, old_value, new_value, note`. First the one record that lets AWS issue the new certificate (add it before anything else), then the two records you switch (`deploy.<domain>` and `*.<domain>`) with their old and new values, then one row per student who uses their own domain.

If your domain needs the certificate record, the first run stops after printing it: add it, wait a few minutes, run the same command again and it continues. Stop students from publishing during the move, because anything published after the copy is not included.

After the new platform has passed its check and you have switched DNS, run the same command once more with `--finish` to attach your domain to the new platform. Keep the old account for a week as a way back, then remove it with `destroy`. Student custom domains and backend functions are not moved automatically in this release: the CSV lists each student's domain, and students publish their backend again.

## 5. The teacher MCP (do all of this from your AI editor)

This works like the students' setup: one command once per computer, then you talk to the AI.

```bash
npx -y <package> setup
```

It asks which AWS profile to use, or lets you paste an AWS access key (hidden) that it saves as a profile of its own called `class-platform` (after a backup of your AWS credentials file; your other profiles are never touched), shows the AWS account it signs in to and asks you to confirm, then adds the teacher tools to your editor. After that no `aws configure` is needed. (In AWS CloudShell there is nothing to save. If you only want the editor part, `npx -y <package> editor` does just that.)

It finds VS Code and Antigravity, shows exactly what it will add, asks, backs up the settings file and merges one entry, `class-platform-teacher`, pinned to this version (the editor then runs that same release file with the `mcp` command). No folder needs to be open. Restart the editor and say things like:

- "Check my AWS account for the class platform"
- "Install the platform, try mode" (it asks you to confirm the account first, then works in the background)
- "A new version is out: plan an update" then "apply it"
- "Turn on class storage"
- "Update my editor tools" (after a new release: it rewrites its own editor entry, after a yes and a backup)

| Tool | Does | Asks first |
|---|---|---|
| `check_environment` | Node, AWS CLI, account, CloudFront access | no, read only |
| `verify` | the public checks that the portal answers and nothing private is open (the full check with the sample site is run in your terminal) | no, read only |
| `status` | version, features, portal, update waiting | no, read only |
| `update_editor_tools` | moves these editor tools to the newest release (rewrites the editor settings entry after a backup) | yes |
| `install`, `finish_domain` | the install, in the background (`job_status` shows progress) | yes |
| `plan_update` | what an update or feature switch would change | no, read only |
| `apply_update` | exactly the plan shown | yes |
| `publish_portal` | republish pages, kit and MCP package | yes |
| `teacher_login` | explains how to set the password in your terminal | n/a |

Left out on purpose: deleting the stack, typing passwords or API keys, anything with IAM users. Those stay in your own terminal (`node setup.mjs teacher <name>`, `node setup.mjs secrets`, `node setup.mjs destroy`), so nothing sensitive can pass through a chat or be talked into by a prompt hidden in a web page or file.

## 6. Cost and removing it

An idle platform costs close to nothing (S3, a few DynamoDB tables on demand, CloudFront, Lambda on request). The cost drivers are student traffic, AI use (capped per student) and storage. Add an AWS Budget in the console.

`node setup.mjs destroy` (typed confirmation) empties the sites bucket and deletes the stack. Data tables, the AI secret and your DNS records are kept on purpose; remove them by hand for a clean account.

## 7. If something goes wrong

| Message | What to do |
|---|---|
| "AWS has not verified this account for CloudFront yet" | Open an AWS Support case (Account and billing). Retry when AWS confirms |
| "not allowed to use CloudFront" or `AccessDenied` | Attach AdministratorAccess to the IAM user |
| "failed state (ROLLBACK_COMPLETE)" | `aws cloudformation delete-stack --stack-name <name>`, then install again |
| "The AWS CLI is not installed" or "could not sign in" | Install the AWS CLI and run `aws configure` |
| Certificate stays pending | The DNS validation record is missing or has a typo; AWS checks again within minutes |
| A window closed in the middle of an install | Run the same install command again: it waits for or picks up the stack AWS was building and carries on |
| `npx` runs an old version even though a newer release exists | npx remembers a downloaded address. The `latest` link is only for a first install on a computer; for anything else use the pinned address of the version you want (`.../download/v<version>/class-platform-teacher-<version>.tgz`), or clear the cache once: `npm cache clean --force` |
| Plan "Blocked" | The update would replace data-holding resources. Nothing changed; open an issue with the plan |
