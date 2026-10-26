# The teacher side: how it is built, and what each part teaches

> **A learning document.** It explains the architecture of everything a teacher uses to install, check, update, back up, move and remove the platform: the installer, the editor tools (MCP), the packaging and the release pipeline. The operating guide is [TEACHER_SETUP.md](TEACHER_SETUP.md); the source of truth for behaviour is [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md) (section 4.16); for the AWS ideas behind the platform itself see [AWS_LEARNINGS.md](AWS_LEARNINGS.md).

## 1. The problem, and the shape of the answer

The platform is a set of AWS resources (CloudFront, S3, API Gateway, Lambda, DynamoDB) described in one CloudFormation template. A teacher should get **their own copy in their own AWS account** and keep it up to date, without being an AWS expert and without anyone being able to harm their account by accident.

That sounds like "a deploy script". It became five separate problems, each with its own design:

| Problem | The answer | Section |
|---|---|---|
| Get the code to the teacher with no git and no clone | A release file on GitHub that `npx` runs | 9 |
| Create the stack safely, in a fresh and possibly unverified account | The **installer engine** | 3 |
| Change it later without breaking a class | **Plan, then apply** with guards | 4 |
| Know it really works | **verify**: a throwaway student publishes a site | 5 |
| Remove it, back it up, move it to another account | **destroy**, **export / import**, **transfer** | 6, 7 |
| Let the teacher do all this by talking to an AI editor, safely | The **teacher MCP** | 8 |

The common design rule: **one engine, several thin front ends**. All behaviour lives in functions that take a context and return plain data. The terminal, the editor tools and the tests are only different callers.

```
 teacher                    front ends (thin)                 engine (all the logic)             AWS
 -------     +-----------------------------------------+    +---------------------------+    +-------------------+
 types   --> | setup.mjs  (terminal: wizard + commands)|    | installer/engine.mjs      |    | AWS CLI           |
 talks   --> | teacher-mcp/server.mjs (editor tools)   | -> | installer/verify.mjs      | -> | (a child process) |
 CloudShell  | bin.mjs  (the npx entry point)          |    | installer/data.mjs        |    |  CloudFormation   |
             +-----------------------------------------+    | installer/transfer.mjs    |    |  S3  DynamoDB     |
                                                            | installer/credentials.mjs |    |  CloudFront  ACM  |
                                                            | installer/prompts.mjs     |    |  SSM  Secrets     |
                                                            +---------------------------+    +-------------------+
```

**Why shell out to the AWS CLI instead of using the AWS SDK?** The teacher already needs credentials, and the CLI is already installed or (in CloudShell) already signed in. It keeps the package at zero dependencies: nothing to install, nothing to audit, and a package of about 650 KB. The cost is that every call is a process and output is text; the engine wraps that once (`awsCall`) and everything else is data. If one day the dependency-free rule matters less than speed, the SDK can replace `awsCall` without touching any caller.

## 2. Where everything lives

| Path | What it is |
|---|---|
| `setup.mjs` | The terminal front end: argument parsing, the guided install, every command (`install`, `verify`, `destroy`, `transfer`, ...) |
| `installer/engine.mjs` | The core: preflight, install, two-phase domain install, status, plan and apply, feature switches, inventory, destroy |
| `installer/verify.mjs` | The outside-in checks and the sample site |
| `installer/data.mjs` | Export and import of a class's data |
| `installer/transfer.mjs` | The move between accounts and the DNS file |
| `installer/credentials.mjs` | Saving the teacher's AWS access as its own profile |
| `installer/prompts.mjs` | Questions, hidden answers, a password asked twice |
| `teacher-mcp/` | The editor tools: `server.mjs` (the MCP server), `setup.mjs` (adds it to VS Code and Antigravity), `jsonc.mjs` |
| `teacher-package/bin.mjs` | The single entry point of the npm package: routes a command to the right place |
| `build-teacher-package.mjs` | Builds the package from the repository |
| `.github/workflows/release.yml` | Builds, attests and publishes a release when a tag is pushed |
| `VERSION`, `CHANGELOG.md` | The one version number, and what each version means for a teacher |
| `tests/test_installer.mjs` and friends | Tests that run against a *fake* AWS CLI |

Two files in the teacher's own computer matter, and **neither holds a secret** except the saved key section: `~/.class-platform-teacher/install.json` (what was installed and how) and `~/.class-platform-teacher/config.json` (which AWS profile to use). The install state can always be rebuilt from the AWS stack (`adoptState`), so a lost computer costs nothing.

## 3. The installer engine

### 3.1 The context: how one engine serves many callers

Every engine function takes a `ctx`:

```
ctx = { repo, profile, stack, dryRun, would[], aws(args), progress(text), now(), sleep(ms), fetch, node, publish, stateDir }
```

`aws` is *the function that runs an AWS CLI call*. In production it is `realAws` (spawns `aws ... --profile P --region us-east-1`). In tests it is a fake that answers from memory. `fetch`, `sleep`, `node` (the admin script) and `publish` (the sample-site publisher) are injectable for the same reason. This is **dependency injection without a framework**: a parameter. It is why a 15 minute CloudFormation deploy and a CloudFront verification can be tested in milliseconds.

`awsCall(ctx, args, {json})` adds two things in one place:
- **dry run:** anything whose command name says it changes something (`create-*`, `put-*`, `delete-*`, `batch-write*`, `s3 sync`, `cloudformation deploy`, ...) is *recorded in `ctx.would` and not run*. `--dry-run` therefore shows every command an install would run, and a test proves a dry run writes nothing.
- **parsing:** with `json` it asks the CLI for JSON and parses it; otherwise it returns text.

### 3.2 Preflight: fail early, say what to do

Before anything is created, `preflight` checks: Node is 20 or newer; the CLI can sign in (`sts get-caller-identity`); CloudFront is allowed (`list-distributions`); whether the stack already exists. Each failure is an `InstallError(message, hint)`: the **message says what is wrong, the hint says what to do**. Examples that really occurred: an unverified new account ("AWS has not verified this account for CloudFront yet" with the support-case hint), a user with no policy (`AccessDenied`), a stack stuck in `ROLLBACK_COMPLETE`.

### 3.3 Install, step by step

```
preflight -> (domain? certificate) -> artifact bucket -> package -> deploy -> afterDeploy -> publish portal -> smoke
```

1. **Artifact bucket** `<stack>-artifacts-<account>`: a private bucket that holds the code zip. Created once, with public access blocked.
2. **Package:** `aws cloudformation package` uploads the `lambda/` folder and rewrites `template.yaml` to point at it. The Lambda code is plain Python with no dependencies, so no build step and no SAM CLI is needed. (This was the *spike* that made the whole one-command idea possible.)
3. **Deploy:** `aws cloudformation deploy` with `CAPABILITY_IAM` and the parameters built by `parametersFor(config)`. Features are CloudFormation **parameters that default to off**; the engine only maps a chosen feature set to `BackendEnabled=true ...`.
4. **Portal publish:** `admin.mjs portal-publish` uploads the portal pages, the student kit and the student MCP package to the portal prefix of the sites bucket and refreshes the CDN. It runs with `STACK_NAME` and `AWS_PROFILE` in its environment, so it acts on the same account as the engine.
5. **Smoke:** fetches the portal home page and `mcp-version.json`.

### 3.4 Resumable on purpose

A CloudFront distribution takes 10 to 15 minutes to create, and a teacher's terminal (especially AWS CloudShell, which times out when idle) may close. The engine **writes its state before it deploys** (`phase: 'deploying'`). When run again:
- if the stack is `CREATE_IN_PROGRESS`, it waits for it (`cloudformation wait stack-create-complete`);
- if it is `CREATE_COMPLETE` and the state says `deploying`, it skips the deploy and carries on to publishing.

The lesson: **any step longer than a minute needs a saved state and a way to continue**, or the user's only option is to start over.

### 3.5 The domain install is two phases because DNS is not ours

With your own domain (`apps.example.com`) the platform needs three DNS records over time, because each must wait for the previous step:

| # | Record | Why it must wait |
|---|---|---|
| 1 | The certificate validation CNAME | AWS issues the TLS certificate only after it can see this record (`ensureCertificate` requests it and returns the record) |
| 2 | `*.<domain>` -> sites distribution | Exists only after the first deploy |
| 3 | `deploy.<domain>` -> portal distribution | Same. And CloudFront **refuses an alias while DNS points that name at another distribution**, so the first deploy runs with `PortalAlias=false`; only after the records exist does `finishDomain` redeploy with `PortalAlias=true` |

That is why a domain install returns `needs: 'certificate'`, then `needs: 'dns'`, and the wizard (or the teacher, running again) continues. `needs` is the engine telling its caller *"I cannot go on until the human does X"*, the same pattern as the MCP's background jobs.

### 3.6 Status and adoption

`status` reads the stack's parameters to learn the domain and the switched-on features. `adoptState` rebuilds `install.json` from a stack that already exists, so a fresh computer (or a fresh clone) works with no saved state. The truth lives in AWS; the local file is a cache.

## 4. Updating: plan, then apply

An update must never replace a table or bucket (that would delete student data). CloudFormation can tell us before it changes anything, through a **change set**:

```
plan:   deploy --no-execute-changeset  ->  list-change-sets  ->  describe-change-set
        -> items [{action, id, type, replacement}]  ->  risky = stateful type AND (Remove OR replacement)
        -> planId = sha256(changeSetId + items)[:16]        (saved in the install state)
apply:  refuse unless planId matches the saved plan  ->  re-describe and re-hash  ->  refuse if it changed
        ->  refuse if risky  ->  execute-change-set  ->  wait  ->  portal-publish  ->  record the new version
```

Three safety ideas in a few lines:
1. **The plan id binds what was shown to what runs.** If the change set changed or expired between "plan" and "apply" (or a prompt-injected "just apply" supplies another id), it is refused.
2. **Data-holding resource types are a named list** (`STATEFUL`: tables, buckets, SSM parameters, secrets, distributions). A plan that would replace or remove one is *blocked*, not merely warned.
3. **Features are switched by the same path:** `featurePlan(ctx, 'ai', true)` is a plan with `AiEnabled=true` (and the features it depends on), so there is exactly one way to change the stack.

Updates are **never automatic**. A teacher is told a release exists (`status` asks GitHub's `releases/latest`, reads only a version number, and respects the teacher's choice at install), and chooses to apply it. Auto-applying code into someone's AWS account would need broad deploy permissions inside that account and a bad release could break a class mid-term; the cost of one command is far lower than that risk.

## 5. verify: proving it works from the outside

`verify` acts like a student and a visitor, through the portal's public address only:

| Group | Checks |
|---|---|
| Public | home page opens; the student MCP package and kit are published; the teacher list and a student page answer **401** without a login |
| Signed in (teacher login, used once, never saved) | teacher can sign in; a student token can be created; the student can sign in |
| Features | every feature that is switched on is offered to students |
| The sample site | a student publishes a small page **with the student's own CLI** (`student/portal-deploy.mjs`); it must come up live; a site that does not exist must be refused (403/404) |

Design points worth studying:
- **The throwaway student is always removed**, even when a later check fails: the token id is remembered *immediately after creation*, and a `finally` revokes it and calls `verify-cleanup` (which deletes the token rows and the sample site's files, and refuses any name that is not `zz-verify-...`).
- **A new teacher login takes up to a minute to be picked up** (the portal caches the login), so the sign-in retries for about 90 seconds before declaring failure.
- **`runVerify` takes its HTTP and publish functions as parameters**, so its logic is tested against a fake portal, while `verifyInstall` wires in the real ones.
- **A real-AWS run found two bugs the fake could not:** the portal answers **201 Created** (not 200) for a new token, and the early exit on that mismatch left the throwaway student behind. Both are fixed and each has a test that mirrors the real behaviour. *Lesson: a fake is only as honest as your knowledge of the real system; run the real thing at least once.*

The same `verify` runs at the end of the guided install, after an update, and after a transfer.

## 6. destroy: removing everything, and showing what goes first

Why a separate, careful `destroy`? Because `delete-stack` alone **fails** on this platform: the sites bucket is *versioned*, and S3 refuses to delete a bucket that holds any object version or delete marker (`aws s3 rm` removes only current objects). The engine's `emptyBucket` lists every version and delete marker in pages of 100 and deletes them in batches before deleting the stack.

| Step | What and why |
|---|---|
| `inventory` | Read-only: counts students, distributions, buckets, functions, tables, so the confirmation is concrete |
| Teacher login | The terminal asks for the teacher's user and password and checks them against the portal's login (`checkTeacherLogin`: `ok` / `denied` / `unreachable`). It guards against slips and tricks, not against someone who already holds AWS admin keys (they could delete the stack anyway), so a typed phrase is still required, and is the only guard when the portal is down |
| Empty buckets, delete stack, wait | CloudFront distributions take 15 to 25 minutes to remove |
| Leftovers | Resources the stack keeps on purpose (`DeletionPolicy: Retain`: the student data table) are *kept and listed*, unless `--everything` |
| Staging bucket, local state | Always removed |
| `--everything` | Also the data table, parameters and the AI keys secret: the account ends as it was before the install. For test accounts; the confirmation phrase is `delete everything` |

`destroy` is **terminal only**: it is deliberately not an editor tool.

## 7. Data: export, import and transfer

### 7.1 What a class's data is

| Where | Content | In a backup? |
|---|---|---|
| DynamoDB tokens table | student tokens (hashes), slug locks, settings, counters, AI settings (including student AI keys) | yes |
| DynamoDB student data table (if class storage is on) | stored student data | yes |
| S3 `sites/`, `archive/`, `userdata/` | published sites, archived sites, student files | yes (`portal/` is republished; `uploads/` is temporary) |
| SSM parameters | the student key map and the teacher login (a hash and a session secret) | yes |
| Secrets Manager | the AI keys | **no**: re-entered with `secrets` |
| Lambda functions (student backends), CloudFront tenants (custom domains) | live resources of the old account | **no**: they cannot be copied as data |

### 7.2 Export and import

- `exportData` scans both tables (the CLI follows pages), `s3 sync`s the three prefixes to a folder, reads the two parameters, writes a `manifest.json` (format number, version, counts) and `chmod 700`s the folder. A used folder is refused so an old backup is never mixed with a new one.
- `importData` checks the manifest's **format number** (a newer format is refused with the reason), then writes items in **batches of 25** (DynamoDB's limit) through a temporary file (`file://`, because a batch is too long for a command line), **retries `UnprocessedItems`**, syncs files, and writes parameters. It never deletes and is safe to repeat. Secrets (the teacher's hash and secret) go through a `0600` temporary file so they **never appear on a command line** (visible to other users in a process list).

### 7.3 Transfer: the old account is only read

```
confirm both account numbers typed + teacher login
  1 install the target (same features, same domain)        -> may stop: needs the certificate record
  2 export the source -> import into the target             (forTransfer: drop what only exists in the old account)
  3 verify the target (the sample site)
  4 write class-transfer-dns.csv                            (every DNS change)
  later: run again with --finish -> attach the domain to the new platform -> then destroy the old one
```

`transformForTransfer` is the interesting function: it keeps what the **teacher chose** (backend on/off and its allowance, the teacher's custom-domain switch, AI settings) and drops what only the **old account's platform made** (function names and states, tenant ids, the `domain#` locks), and returns the list of students' custom domains for the CSV. Student AI keys stay: they belong to the AI provider, not to the old AWS account.

The CSV (`who, name, type, old_value, new_value, note`, with a byte-order mark and CRLF so spreadsheets open it correctly) lists, in order: the certificate record (do first), the portal and wildcard switch with old and new values, and one row per student's own domain. There is **no live replication and no automatic DNS change**: a short, announced pause in publishing is the safe design, and the old account is deleted only by the teacher, later, with `destroy`. The transfer keeps a small state file, so running it again continues from where it stopped (for example after the certificate record).

## 8. The teacher MCP

### 8.1 What MCP is, and what this server is

MCP (Model Context Protocol) lets an AI editor start a small program and call its **tools**. Ours speaks JSON-RPC over stdin/stdout, one message per line: `initialize`, `tools/list`, `tools/call`. It also uses MCP's **roots** feature (the editor tells it which folder is open). The server is `teacher-mcp/server.mjs`, about 300 lines, and it contains **no AWS logic**: every tool runs `node setup.mjs <command> --json` and returns the single JSON answer. So whatever the AI can do, the teacher can do by hand, and the tests do not need an editor.

### 8.2 The tools

| Tool | Does | Changes something? | Asks first? |
|---|---|---|---|
| `check_environment`, `status`, `verify` (public checks), `plan_update`, `job_status`, `teacher_login` (explains) | read or explain | no | no |
| `install`, `finish_domain`, `apply_update`, `publish_portal`, `update_editor_tools` | change AWS or the editor settings | yes | **yes** (`confirm: true`) |

Not available through MCP **on purpose:** deleting the stack, typing a password or API key, anything with IAM users, `transfer`, `import`. The rule: *whatever is destructive or secret stays in the teacher's own terminal.* A malicious instruction hidden in a web page or a file cannot make the AI delete an account, because the tool does not exist.

### 8.3 Safety mechanics

- **Confirm gating:** tool descriptions tell the AI to ask the teacher, and the tool refuses without `confirm: true`. (The description is *advice to the model*; the refusal is *enforcement*.)
- **Plan id validation:** `apply_update` accepts only a hex plan id (never a free string that could reach a shell) and the engine binds it to the shown plan.
- **Background jobs:** an install takes 10+ minutes, longer than an editor waits for one answer. The tool starts the job and returns at once; `job_status` reports progress (the tail of the output) and the result. One job at a time.
- **Output hygiene:** anything shaped like a key (`sk-or-...`, `AQ....`, `AIza...`, `AKIA...`) is replaced by `***` before the AI sees it.
- **Which folder:** an explicit `path` that is not the platform is *refused* (never silently replaced by another folder); otherwise the editor's open folder, `--repo`, or the package's own folder.

### 8.4 Connecting it: `setup`, `editor`, `update_editor_tools`

`npx ... setup` is the teacher's one-time step (like the students'): it asks which AWS profile to use or saves an access key, shows the account, then adds the entry to VS Code and Antigravity. The key section is written by `setSection` into **one** section of the standard credentials file (`[class-platform]`) after a timestamped backup, never touching other sections, and the key is validated before it is written (a `AKIA...` shape, a 40-character secret). `setup` also keeps `config.json` with just the profile name.

The editor entry is **pinned to an exact release file** (for example `.../download/v1.1.0/class-platform-teacher-1.1.0.tgz mcp`). Pinning means an update never changes behaviour behind a teacher's back, but it also means the editor stays on the old version until moved: that is the `update_editor_tools` tool (and the `editor-update` command), which rewrites the entry after a yes and a backup. Settings files with comments are **never rewritten** (JSONC cannot be saved without losing the comments); the exact entry is printed to paste instead.

### 8.5 Compared with the student MCP

| | Student MCP | Teacher MCP |
|---|---|---|
| Served from | the teacher's own portal | GitHub Releases |
| Power | publish one student's own site | change an AWS account |
| Secret | a revocable student token, saved once | an AWS access key, saved once as its own profile |
| Updates | `status` + `update_kit` | `status` + plan/apply + `update_editor_tools` |
| Never offered | anything outside the student's project | deleting, passwords/keys, IAM, transfer, import |

## 9. Packaging and releases

### 9.1 One tarball that carries the platform

`build-teacher-package.mjs` assembles `dist/teacher-package/`: the platform's own files (`template.yaml`, `lambda/`, `web/`, `starter/`, `student/`, `installer/`, `admin.mjs`, `setup.mjs`), the MCP (`mcp/`), the entry point `bin.mjs`, `package.json` (version = the `VERSION` file, no dependencies, no scripts), `release.json`, and a `.packaged` marker. It takes **only files git tracks (plus new, not-ignored ones)**, so no local or personal file can slip in; a test asserts that nothing like a `*.local` file, `samconfig`, `tests/` or key-shaped text is present, and that the **student kit and student MCP built from the package are byte-identical to the repository's**.

Two small files do a lot:
- **`.packaged`** tells the code it is running from a read-only npx cache, so state goes to `~/.class-platform-teacher` instead of next to the code.
- **`release.json`** (`{repo, version, tag, url}`) is stamped at build time. It lets the package build its own pinned editor entry, ask GitHub for a newer release, and print the exact upgrade command.

### 9.2 The pipeline

```
git tag vX.Y.Z  ->  release.yml:  tag == VERSION?  ->  lint  ->  whole test suite  ->  build package (--repo owner/name --pack)
                ->  attest build provenance  ->  GitHub Release with
                       class-platform-teacher-X.Y.Z.tgz     (the pinned file)
                       class-platform-teacher.tgz           (alias for the "latest" link)
                       sha256sums.txt
                ->  (optional) npm publish --provenance      if an NPM_TOKEN secret exists
```

The teacher's command is just `npx -y <release file> <command>`; `npx` downloads the tarball and runs `bin.mjs`, which routes: `mcp` -> the MCP server; `editor` -> the editor setup; `eject <folder>` -> copies the platform into a normal folder for people who want to change it with git; anything else -> `setup.mjs`.

**A lesson from real use:** `npx` remembers a downloaded address. The *pinned* link is safe forever; the **`latest` link is cached by npx**, so a computer that used it once keeps running that copy. Hence: `latest` only for a first install, pinned addresses everywhere after (every message the tool prints is built from `release.json`'s pinned URL), and `npm cache clean --force` in the troubleshooting table.

### 9.3 One version, one source of truth

`VERSION` -> package.json, the MCP's version, `release.json`, the release tag, the changelog heading. A test asserts the repository's `VERSION` equals `teacher-mcp/package.json`; the workflow fails a release whose tag differs from `VERSION`.

## 10. Security model

| Threat | Control |
|---|---|
| A secret (password, AI key, AWS key) ends up in a chat or log | Hidden prompts in the terminal only; the MCP never accepts them; output is masked; the AWS key is saved to the credentials file only; secrets pass to AWS through `0600` temporary files, not command lines |
| The AI (or an injected instruction) changes the account without consent | Every changing tool needs `confirm: true`; updates are bound to a shown plan id; destructive actions are not tools |
| An update deletes data | `STATEFUL` types: a plan that replaces or removes one is blocked; `destroy` keeps the data table unless `--everything` |
| The wrong AWS account | The account number and user are shown before install; transfer requires **typing both numbers**; the profile's origin (env, saved, flag) is explained in prompts |
| A compromised or mistaken release | Pinned versions; checksums; build provenance attestation; releases come from a tag built by CI after the full suite |
| A teacher who is locked out | Passwords are typed twice; the install state can be rebuilt; the login can be reset with `teacher <name>` |
| A settings file is corrupted | Backup first; merge one entry; files with comments are not rewritten |
| A backup leaks | The folder is `0700`; the docs say it holds student AI keys and the teacher hash; delete it when done |

## 11. How it is tested

The pattern repeated in every test file: **a fake AWS CLI** (a function that answers `aws ...` calls from memory, with just enough state), **injected HTTP**, and **temporary folders**. A fake is written to *record* what was called, so a test can assert both the result and that **nothing mutating ran** (dry runs, refusals, the source account in a transfer).

| Test | What it proves |
|---|---|
| `test_installer.mjs` | install (try mode and domain mode), early failures with hints, resume, plan/apply guards, feature switches, destroy cleanliness, adoption, release notices |
| `test_prompts.mjs` | the exact sequence that crashed on a real terminal (question, hidden answer, question); passwords never shown; typed twice |
| `test_verify.mjs` | every check passes on a healthy portal, each failure is named, the throwaway student is always removed, 201 is accepted |
| `test_credentials.mjs` | only one section of the credentials file is touched, backups, bad keys refused, editor entries move to a release |
| `test_data.mjs`, `test_transfer.mjs` | backup format, batches and retries, no secret on a command line, the transfer's refusals and steps, the DNS file |
| `test_teacher_mcp.mjs`, `test_teacher_package.mjs` | the protocol, which tools exist and which do not, confirm gating, jobs, the package contents and byte-identical student artifacts |

**The real-AWS tests** (a throwaway stack in a real account: install, verify, destroy, then list what is left) found what no fake could: the bucket-versions problem, the 201 status, a hidden prompt leaving the terminal paused, line endings changing a hash on Windows, a function that referenced its own API (a circular dependency that only the change set revealed). Each was turned into a permanent test. The rule that came out of it: **write the fake from what the real system does, then check the fake against the real system once**.

## 12. Decisions and trade-offs

| Decision | Chosen | Not chosen, and why |
|---|---|---|
| How a teacher gets the code | A release file on GitHub, run by `npx` | `git clone` (needs git, invites local edits and merge conflicts); npm only (needs a registry account; still possible, optional) |
| Talking to AWS | The AWS CLI as a subprocess | The AWS SDK (adds dependencies; speed is not the problem here) |
| Updating | Notify, plan, apply by a human | Automatic updates (needs broad rights in the teacher's account; a bad release hits a live class) |
| Who may delete | Terminal only, with a typed phrase and the teacher login | An editor tool (a hidden instruction could trigger it) |
| Moving accounts | Guided, resumable, old account only read, DNS by a CSV | Live replication or automatic DNS (complex, risky, hides the one step that needs a human decision) |
| Features | Parameters that default to off, switched by plan/apply | Code branches per feature (harder to test and to reason about during updates) |
| State | A small cache that can be rebuilt from AWS | A database of its own (a second source of truth that can disagree) |

## 13. Extending it

- **A new command:** write the function in `installer/` taking `ctx`, returning plain data; add a case in `setup.mjs`; test with the fake AWS; describe it in `TEACHER_SETUP.md` and `CHANGELOG.md`.
- **A new editor tool:** add an entry to `TOOLS` in `teacher-mcp/server.mjs` that runs the command with `--json`; decide whether it changes anything (then `confirm`); keep it out of the MCP if it is secret or destructive; add it to the tool-list tests.
- **A new platform feature:** add a CloudFormation parameter that defaults to off, add it to `FEATURES` in `engine.mjs`, and the plan/apply, verify and feature switch handle the rest.
- **A release:** change `VERSION`, `teacher-mcp/package.json` and `CHANGELOG.md`, run the suite, tag `vX.Y.Z`.

## 14. Exercises (to check you understand it)

1. Find where a dry run is decided (`isMutating`). Add a command name to the pattern and write a test that proves a dry run skips it.
2. Read `apply` in `engine.mjs`. List the four conditions under which it refuses. Which one protects against a replaced table?
3. In `verify.mjs`, find the `finally` block. What would go wrong if the token id were remembered *after* the "token created" check?
4. Follow `transformForTransfer`. Why are student AI keys kept but function names dropped?
5. Why does the editor entry pin an exact release file? What is the cost, and which tool removes it?
6. Trace what happens if the window closes during an install and the command is run again (`install`, `earlier`, `resuming`).
7. In the real-AWS run, `verify` failed with "HTTP 201". Write the test that would have caught it without AWS (hint: what must the fake return?).
