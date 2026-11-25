# Chapter 7: Infrastructure as code, the installer, releases and supply-chain trust

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> Everything here is about getting software *into someone else's AWS account safely*: CloudFormation/SAM, the plan→apply engine, the npm/GitHub package, CI, signing and kit updates.

## A. CloudFormation and SAM

**Q219 [B] What is Infrastructure as Code (IaC) and why use it?**
Describing infrastructure in files that are reviewed, versioned, diffed and repeatable instead of clicking in a console. For this platform it means a teacher gets the same ~100 resources every time, an update is a visible diff, and teardown removes everything.
Study: [AWS: What is IaC?](https://aws.amazon.com/what-is/iac/)

**Q220 [B] What is CloudFormation and what is a "stack"?**
AWS's IaC engine. A *template* lists resources; a *stack* is one deployed instance of it. CloudFormation creates resources in dependency order, rolls back on failure, and can later update or delete the whole stack.
Study: [CloudFormation user guide](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/Welcome.html)

**Q221 [B] What is SAM and how does it differ from plain CloudFormation?**
SAM (Serverless Application Model) is a CloudFormation *transform* with shortcuts: `AWS::Serverless::Function`, `AWS::Serverless::HttpApi` expand into the underlying Lambda, role, permission and API resources. The template starts with `Transform: AWS::Serverless-2016-10-31`.
Study: [What is SAM?](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/what-is-sam.html)

**Q222 [I] Why does the installer not need the SAM CLI?**
The Lambda code is pure Python with no build step, so `aws cloudformation package` (zip + upload + rewrite `CodeUri`) followed by `aws cloudformation deploy` suffices. That keeps teacher prerequisites to Node + AWS CLI (or just CloudShell).

**Q223 [I] What are template Parameters and Conditions, with examples?**
Parameters are inputs (`BackendEnabled`, `AiEnabled`, `StorageEnabled`, `RootDomain`, `PortalAlias`...). Conditions (`UseWorker`, etc.) turn resources on or off. The installer turns *features* into parameters that default to off, so a plan is always "change these parameter values".
Repo: `template.yaml` top; `installer/engine.mjs` `parametersFor`.

**Q224 [I] What is a change set and how does it make updates safe?**
A preview of what an update will do (Add/Modify/Remove, and whether a resource is *replaced*). You can inspect it before executing. This project's `plan` creates one with `--no-execute-changeset`; `apply <planId>` executes exactly that.
Study: [Updating stacks using change sets](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-updating-stacks-changesets.html)

**Q225 [I] What is a "replacement" and why is it dangerous for tables/buckets?**
Some property changes can't be done in place; CloudFormation creates a new resource and deletes the old one. For a table or bucket that means losing the data. The plan flags `replacement` and the engine blocks plans that replace or remove a STATEFUL type.
Study: [Update behaviors of stack resources](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-updating-stacks-update-behaviors.html)

**Q226 [I] How is the `planId` computed and what attack/mistake does it prevent?**
`sha256(changeSetId + items)[:16]`, saved in the install state. `apply` refuses an unknown id, re-describes the change set and re-hashes it (refuses if it changed), and refuses if risky. It binds *what was shown to the human* to *what runs*, preventing "apply something else" (including a prompt-injected AI supplying a different id).

**Q227 [I] Why do updates never apply automatically?**
Auto-applying would need broad deploy permissions inside the teacher's account and a bad release could break a class mid-term; the cost of one command is lower than that risk. `status` only *tells* the teacher a release exists.

**Q228 [I] What is a circular dependency in CloudFormation and what happened here?**
Resource A needs B and B needs A, so there is no creation order. A function that referenced its own API was found only when a real change set was created, not by lint or fakes.

**Q229 [D] What is drift and how can the "stack is the truth" rule reduce harm?**
Drift = live resources differ from the template/state. The engine treats the live stack's parameters as authoritative (`currentState`), and the local `install.json` is only a cache. A bug in 2.1.0: a stale saved state from another profile produced a plan to remove features of a live platform; `currentState` fixed it.

**Q230 [D] What is "adoptState"?**
Rebuilds the local state from an existing stack (domain, features) so a new computer works with no saved state. Feature detection reads parameters like `BackendEnabled=true`.

**Q231 [I] What does `DeletionPolicy: Retain` interact with `destroy --everything`?**
Retained resources survive stack deletion. By default `destroy` keeps and lists them; `--everything` also deletes the data table, parameters and the AI secret so a test account ends as before the install.

**Q232 [I] Why is the artifact bucket (for code) named with the account id?**
S3 bucket names are global; adding the account id makes collisions unlikely and shows ownership.

## B. The installer engine design

**Q233 [B] What is the "one engine, several thin front ends" rule?**
All behaviour lives in `installer/*.mjs` functions that take a `ctx` and return data. `setup.mjs` (terminal), `teacher-mcp/server.mjs` (editor tools) and tests are different callers. The MCP server contains no AWS logic: each tool runs `node setup.mjs <cmd> --json`.

**Q234 [I] What is dependency injection and how is it done here without a framework?**
Pass dependencies as parameters. `ctx.aws` is the function that runs an AWS CLI call; in production it spawns the CLI, in tests it's a fake. `fetch`, `sleep`, `now`, `publish`, `node` are injectable too. A 15-minute deploy is therefore tested in milliseconds.
Study: [Martin Fowler: Inversion of Control and DI](https://martinfowler.com/articles/injection.html)

**Q235 [I] Why shell out to the AWS CLI instead of using the SDK?**
Zero dependencies (nothing to install or audit; ~650 KB package), CloudShell already has the CLI signed in, and teachers already need credentials for it. The cost is text parsing and a process per call; all wrapped in `awsCall`.

**Q236 [I] How does dry-run work?**
`awsCall` classifies commands by verb (`create-*`, `put-*`, `delete-*`, `s3 sync`, `cloudformation deploy`...) and records them in `ctx.would` instead of running them. A test proves a dry run writes nothing.

**Q237 [I] What is `InstallError(message, hint)` and why two parts?**
The message says what's wrong; the hint says what to do. Good errors are actionable: e.g. "AWS has not verified this account for CloudFront yet" + support-case instructions.

**Q238 [I] Why does the installer write state *before* deploying?**
A CloudFront distribution takes 10–15 min; a terminal (especially CloudShell) can time out. State `phase: deploying` lets the next run wait for `CREATE_IN_PROGRESS` or skip to publishing. Rule: any step over a minute needs saved state and a way to continue.

**Q239 [I] What does `needs: 'certificate' | 'dns'` mean?**
The engine telling its caller "I can't continue until the human does X". It's the same pattern as the MCP's background jobs and a cooperative way to model workflows that cross a human boundary (DNS).

**Q240 [I] What does `verify` prove and why from the outside?**
It acts as a visitor and a student through the public portal address only: home loads, kit/package published, protected routes answer 401, teacher login works, a throwaway student is created, publishes a sample site with the real CLI, the site comes up, a missing site is refused. Outside-in checks catch what internal checks miss (DNS, CDN, CORS).

**Q241 [I] Why does `verify` always delete the throwaway student (even on failure)?**
The token id is remembered immediately after creation and a `finally` revokes it and calls `verify-cleanup` (which refuses names not starting `zz-verify-`). "Clean up after yourself in `finally`" is a core test hygiene habit.

**Q242 [I] What lessons did real AWS runs add that fakes could not?**
Bucket versions block deletion; the portal returns 201 not 200; hidden prompts leaving the terminal paused; CRLF changing a hash on Windows; the circular dependency; CloudFront account verification; `PortalAlias` ordering; MSYS path rewriting. "Write the fake from what the real system does, then check the fake against the real system once."

**Q243 [I] How do `export`/`import` avoid leaking secrets?**
Secrets go through `0600` temporary files (never command lines visible in process lists); the backup folder is `chmod 700`; `importData` refuses newer formats; it never deletes and is safe to repeat.

**Q244 [I] What does `transfer` do in terms of steps and why is the old account only read?**
Install target with same features → export source → import into target (dropping what exists only in the old account) → verify → write `class-transfer-dns.csv` → later `--finish`. Read-only on the source means a mistake can't harm the working platform.

## C. Packaging and releases

**Q245 [B] How does a teacher get the code with no git?**
`npx -y <release tgz URL> <command>`. `npx` downloads the tarball from GitHub Releases and runs `bin.mjs`, which routes to the right front end. The package carries `template.yaml`, `lambda/`, `web/`, `starter/`, `installer/`.

**Q246 [I] Why pin the release URL and avoid the `latest` alias after the first install?**
`npx` caches by address; a `latest` alias gets stuck on an old copy. A pinned URL never changes under you. All messages are built from the pinned URL in `release.json`.

**Q247 [I] What is `.packaged` for?**
A marker telling the code it's running from a read-only npx cache, so state goes to `~/.class-platform-teacher` instead of beside the code.

**Q248 [I] Why does the package builder take only files git tracks?**
So local/personal files cannot slip into a release. A test asserts nothing like `*.local`, `samconfig`, `tests/` or key-shaped text is inside.

**Q249 [B] What is semantic versioning and how is it applied?**
MAJOR.MINOR.PATCH: breaking / feature / fix. One `VERSION` file feeds `package.json` (several), `platform.json` kitVersion, the changelog heading and the git tag; a test and the workflow fail if they disagree.
Study: [semver.org](https://semver.org/), [Keep a Changelog](https://keepachangelog.com/)

**Q250 [I] Walk through what `release.yml` does on a tag push.**
Checks tag == VERSION → template lint → full test suite → build package → **attest build provenance** → create GitHub Release (versioned tgz, `latest` alias, `sha256sums.txt`; notes cut from the CHANGELOG section) → optional npm publish with provenance → **cosign** signs each file with Sigstore (`.sigstore.json` bundles) → attach signatures.
Repo: `.github/workflows/release.yml`.

**Q251 [I] What is build provenance and what does Sigstore add?**
Provenance is a signed statement of *where and how* an artifact was built (source commit, workflow). Sigstore/cosign provides keyless signing tied to the CI identity and a public transparency log, so users can verify a file came from this repo's workflow.
Study: [Sigstore docs](https://docs.sigstore.dev/), [SLSA](https://slsa.dev/), [GitHub artifact attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations)

**Q252 [I] Why are GitHub Actions pinned by commit SHA?**
Tags can be moved; a compromised action could change behaviour. A full SHA is immutable. Dependabot updates them. Part of OpenSSF Scorecard checks (`scorecard.yml`).
Study: [OpenSSF Scorecard](https://securityscorecards.dev/), [GitHub: Security hardening for Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)

**Q253 [I] What are CodeQL and Dependabot doing in this repo?**
CodeQL = static analysis that finds code-level vulnerabilities; Dependabot = automatic PRs for outdated/vulnerable dependencies and actions. Both are in `.github`.
Study: [About CodeQL](https://codeql.github.com/docs/codeql-overview/about-codeql/)

**Q254 [I] Why does the tests folder include `kit-versions.json`?**
It stores a content hash per released kit version. A test fails if you change kit files without bumping the version (or vice versa), so "same version, different content" can't ship, and old versions stay verifiable.

**Q255 [D] The 2.1.1 `.gitignore` bug: what happened and what does it teach?**
npm never packs a file named `.gitignore`/`.npmignore`, so kits built from the *published package* lacked it. Fix: ship `gitignore.txt` and rename at kit build time. Lesson: test the artifact users receive (not just the source tree), and real-account testing finds packaging problems.

## D. Kit signing and student updates

**Q256 [B] How does the student's computer get updates?**
The portal serves `/starter-kit.zip` and `kit-version.json`. `npm run deploy` auto-syncs managed kit files (rules, tools, helpers) from the teacher's portal; the teacher's portal is the *only version authority* (no student choice/freeze). `platform update` is a dry run unless `--apply`.

**Q257 [I] What does the KMS signature protect that HTTPS doesn't?**
HTTPS proves you talked to the portal; it doesn't prove the content is the teacher's if someone else can write `portal/` (a leaked credential, a bucket-policy mistake). The signature needs `kms:Sign`, a permission no platform function has.

**Q258 [I] Describe the verification the student side performs.**
Download `kit-version.json` and `.sig`; verify ECDSA-SHA256 with the pinned public key; only then trust `zipSha256` and the managed hashes; refuse a lower `serial`; once pinned, refuse an unsigned manifest. Failure exits with code 3 and the student carries on with the existing kit.
Repo: `starter/template/tools/signing.mjs`, `tools/platform.mjs`.

**Q259 [D] What does signing NOT cover?**
The MCP tarball fetched by `npx` (no signature check on URL packages; phase 2), the portal's browser pages (browsers can't verify signatures), and a full-admin attacker (who can also call KMS).

**Q260 [I] What is ECDSA P-256 and why is it a good fit?**
Elliptic-curve signatures with small keys and fast verification, supported natively by KMS and by Node's `crypto` (no dependency).
Study: [Practical Cryptography for Developers: ECDSA](https://cryptobook.nakov.com/digital-signatures/ecdsa-sign-verify-messages)

**Q261 [D] What happens in `transfer` for the signing key?**
A new platform has a new KMS key, so students must re-pin (`npm run trust`). The design documents this as a rare manual step.

**Q262 [I] Why compare the kit zip hash instead of trusting file lists?**
One signature over `kit-version.json` covers `zipSha256` and every managed file hash, so verifying one small file authenticates the whole kit.

**Q263 [D] What is a supply-chain attack and where could this project be hit?**
Compromise of something you depend on: a malicious npm package, a hijacked GitHub Action, a poisoned release, a tampered kit. Controls: zero runtime dependencies in the packages, pinned action SHAs, provenance + Sigstore, curated add-on catalogue with exact approved packages, kit signing, vendored pypdf hashed.
Study: [OpenSSF supply-chain guide](https://openssf.org/resources/guides/), [npm: Auditing package dependencies](https://docs.npmjs.com/auditing-package-dependencies-for-security-vulnerabilities)

## Study materials for this chapter

- **CloudFormation best practices**: <https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html>
- **AWS SAM**: <https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/>
- **Terraform vs CloudFormation** (compare approaches): <https://developer.hashicorp.com/terraform/intro>
- **The Twelve-Factor App**: <https://12factor.net/>
- **Sigstore + SLSA** intro talks on YouTube (search "Sigstore explained", "SLSA framework").
- Book: *Infrastructure as Code* (Kief Morris); *Accelerate* (Forsgren et al.) for CI/CD thinking.
- Hands-on: read `installer/engine.mjs` `plan()`, `apply()` and `tests/test_installer.mjs` plan/apply tests side by side.
