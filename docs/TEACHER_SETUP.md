# Install and update your own copy (teacher guide)

You run the platform in **your own AWS account**, from **one command**; nothing is cloned. This page covers the first install, keeping it up to date, switching features on, and doing all of it from your AI editor with the teacher MCP.

```bash
npx -y https://github.com/abhtft/class-hosting-platform/releases/latest/download/class-platform-teacher.tgz install    # first install (guided)
npx -y https://github.com/abhtft/class-hosting-platform/releases/download/v<new>/class-platform-teacher-<new>.tgz upgrade   # later, when a new release is out
```

`abhtft/class-hosting-platform` is the repository you got this from (its README shows the exact line). The release file is the whole platform, so there is no clone and no npm account; `npx` only needs Node. Pin a version (`.../download/v1.0.0/...`) when you want exactly that version; "latest" is for the first install. Everywhere below, **`<package>`** stands for that address, and `node setup.mjs <command>` (from a git clone) means the same thing. Your install state is kept in `~/.class-platform-teacher` and can always be rebuilt from the AWS stack, so a new computer just works. Technical background: [NEW_ACCOUNT_AND_TRANSFER.md](NEW_ACCOUNT_AND_TRANSFER.md). What changed in each version: [../CHANGELOG.md](../CHANGELOG.md).

## 1. What you need

| | |
|---|---|
| An AWS account | New accounts can refuse their first CloudFront distribution until AWS verifies them (an AWS Support case, "Account and billing"). The installer detects this and tells you |
| The AWS CLI, signed in | `aws configure` (or `aws configure --profile mine`) with an IAM user that has **AdministratorAccess** |
| Node 20 or newer | https://nodejs.org/ |
| Nothing else to download | The package carries the platform. (Contributors and people who want to change the code use `git clone` instead, or `npx class-platform-teacher eject <folder>`) |
| Optional: a domain you control | Without one, the platform works on free `cloudfront.net` addresses (best for a first try) |

You do **not** need the SAM CLI, Docker or Python.

## 2. Install

```bash
npx -y <package> install
```

It asks, in this order: the AWS profile (it lists the profiles it finds; Enter uses your default credentials. If a name already appears in brackets, it is one you set on this computer, either the `AWS_PROFILE` variable or an earlier install: Enter keeps it, or type another name), an optional domain, which optional features to switch on (all default off), your teacher login name and password (hidden), and, if you chose AI, the AI keys (hidden, stored in AWS Secrets Manager, never in a file). **Before it creates anything it shows the AWS account number and user it will use and asks you to confirm**, so a wrong profile is caught early. It then checks the account, deploys (about 10 to 15 minutes, CloudFront is slow), publishes the portal pages, the student kit and the MCP package, and checks that the portal answers. Try `<package> install --dry-run` first to see every AWS command it would run without running any.

**With a domain** (for example `apps.example.com`) there are two pauses, because DNS is yours:
1. It requests a certificate and prints **one DNS record**; add it at your DNS host, wait a few minutes, run the same command again.
2. After the first deploy it prints **two DNS records** (`*.apps.example.com` for student sites, `deploy.apps.example.com` for the portal); add them, press Enter, and it attaches the portal address.

When it ends it prints your portal address. Sign in at `<portal>/teacher.html`, create a student token, and give the student the **Start here** command on their page.

## 3. Keep it up to date

```bash
npx -y <package-of-the-new-version> upgrade
```

The teacher page and `status` tell you when a newer release exists and print this exact command. (With a git clone: `git pull`, then `node setup.mjs upgrade`.) `upgrade` shows what would change in plain words ("adds 1, modifies 3, replaces no data"), asks, applies exactly that plan and republishes the portal, kit and MCP package. Safety rules it enforces:
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

## 5. The teacher MCP (do all of this from your AI editor)

Add it once:

```bash
npx -y <package> editor
```

It finds VS Code and Antigravity, shows exactly what it will add, asks, backs up the settings file and merges one entry, `class-platform-teacher`, pinned to this version (the editor then runs that same release file with the `mcp` command). No folder needs to be open. Restart the editor and say things like:

- "Check my AWS account for the class platform"
- "Install the platform, try mode" (it asks you to confirm the account first, then works in the background)
- "A new version is out: plan an update" then "apply it"
- "Turn on class storage"

| Tool | Does | Asks first |
|---|---|---|
| `check_environment` | Node, AWS CLI, account, CloudFront access | no, read only |
| `status` | version, features, portal, update waiting | no, read only |
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
| Plan "Blocked" | The update would replace data-holding resources. Nothing changed; open an issue with the plan |
