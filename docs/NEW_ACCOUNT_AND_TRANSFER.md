# Setting up in a new AWS account, and moving a project between accounts

Status: written 2026-10-25. This document records what was **done and measured** (sections 1 to 4) and what is **designed but not built yet** (sections 5 to 7). Every statement says which of the two it is. Account IDs, domains and addresses are placeholders: the repository never contains the maintainer's real values (`tests/test_no_identifiers.py` enforces this).

Related: [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md) (the system), [AWS_LEARNINGS.md](AWS_LEARNINGS.md) (the AWS ideas), `opensource_plan.md` (the release plan; private copy only).

## 1. Why this exists

Two needs came up together:

1. Any teacher should be able to run **their own copy** in **their own AWS account**, so the project can be open source.
2. A teacher with many deployed apps must be able to **move to a different AWS account** (a new account, or because the old account's credits or billing ran out) without students noticing.

Principle: **the portal address is the identity students see; the AWS account is a replaceable host.** Keep the same domain across a move and students change nothing; only two DNS records move.

## 2. Two accounts on one computer (done)

Both accounts are controlled from one machine with **named AWS CLI profiles**, and nothing is ever pasted into code or the repository.

| Profile | Account | Use |
|---|---|---|
| `default` | the current production account | unchanged |
| `new` | the new account (placeholder `<NEW_ACCOUNT_ID>`) | tests and the future home |

Setup, in order:

1. In the new account, create an IAM user and attach **AdministratorAccess**. A user with no policy can sign in but every call fails with `AccessDenied` (this happened once; the first symptom was `s3:CreateBucket` refused).
2. Create an access key with use case "Command Line Interface (CLI)". The secret is shown once.
3. `aws configure --profile new` (access key, secret, region `us-east-1`, output `json`).
4. Check: `aws sts get-caller-identity --profile new` shows the new account ID.
5. Pick the account per terminal with `AWS_PROFILE=new` (PowerShell: `$env:AWS_PROFILE = "new"`). `admin.mjs` and the `aws` calls it makes then use that account.

Safety: turn on MFA for the root user and the IAM user; delete the access key when the work is done; add a budget (section 3).

Windows note: in Git Bash set `MSYS_NO_PATHCONV=1` before CLI calls whose arguments start with `/` (for example SSM parameter names), or the shell rewrites them into file paths.

## 3. What a new account needs before anything else (measured)

| Item | What was found |
|---|---|
| **Plan** | The account was on the **paid plan** with USD 100 sign-up credits, so it does not close when credits run out (the free plan closes after 6 months or when credits are used). |
| **More credits** | Five activities worth USD 20 each: launch an EC2 instance, create an RDS database, build a Lambda web app, set up a cost budget, use a Bedrock model in the playground. They are visible with `aws freetier list-account-activities`; the balance with `aws freetier get-account-plan-state`. Status updates lag by minutes to hours (EC2 flipped to COMPLETED long after the instance was launched and even after it was terminated). Four of five completed (balance USD 180); Bedrock is blocked by the account check below. |
| **Budget** | A monthly cost budget (USD 20) was created without notification; add an email alert in the console if wanted. Creating a budget is itself one of the credit activities. |
| **CloudFront verification** | A new account may refuse its first distribution: *"Your account must be verified before you can add new CloudFront resources"* (HTTP 403, `AccessDenied`). The stack rolls back. Fix: an AWS Support case (Account and billing) quoting the request ID. The support API cannot create the case on the Basic plan (`SubscriptionRequiredException`); use the console. |
| **Bedrock** | The same account check showed first as "Your account is currently being verified", then as `ValidationException: Operation not allowed` in the CLI and the console playground. It clears when AWS finishes verification (normally hours, but not guaranteed); mail to the AWS verification address named in the error message can speed it up. |
| **Lambda concurrency** | New accounts start with a low account-wide limit. The template's alarm threshold is a parameter (`ConcurrencyAlarmThreshold`, default 8, sized for a limit of 10). The installer should read the quota and warn (planned). |

Cost of the test resources: an EC2 `t3.micro` (about a cent per hour), an RDS `db.t4g.micro` and a Lambda function. All were deleted immediately afterwards (RDS with no final snapshot and no automated backups); a list of buckets, functions, tables, parameters, secrets, distributions, volumes and snapshots was empty afterwards.

## 4. The install without the SAM CLI (spike: done, passed)

Question: can `template.yaml` be deployed with only the AWS CLI? **Yes.**

```bash
aws s3 mb s3://<artifact-bucket>                      # any private bucket for the code zip
aws cloudformation package --template-file template.yaml \
    --s3-bucket <artifact-bucket> --output-template-file packaged.yaml
aws cloudformation deploy --template-file packaged.yaml --stack-name <stack> \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND
```

Why it works: the Lambda functions are plain Python from one folder (`CodeUri: lambda/`), with no dependencies and no build step, and `package` uploads that folder and rewrites the reference. `sam build` adds nothing here.

Measured in the production account with a throwaway stack (try mode: empty `RootDomain`, optional modules off): the stack reached `CREATE_COMPLETE`, taking longer than 10 minutes because CloudFront is slow to create; the outputs were the portal and site `cloudfront.net` addresses, bucket, table and API URL. The portal address returned 403 before any pages were published (expected: no `portal/` objects yet). The stack, its buckets and its SSM parameters were then deleted.

Two details the installer must handle:

- **SSM parameter names collide inside one account.** `KeysParameterName` and `TeacherParameterName` default to fixed names; a second stack in the same account must override them. In a fresh account the defaults are fine.
- **Dangling state:** `aws cloudformation deploy` leaves a stack in `ROLLBACK_COMPLETE` after a failed first deploy; it must be deleted before retrying.

The same deploy in the **new account** failed at the CloudFront distribution (section 3), so the clean-account install is **not yet verified end to end**. The check list to run once AWS clears the account: the deploy above with all-default parameters, then `implement.md` phase 6 (key, sample deploy, `200`s through the distribution, `403/404` for an unknown site, `403` for the private bucket, `401` without a key, re-deploy cleanup), then the portal path (`teacher-setup`, `portal-publish`, token, upload through the kit), then full teardown.

## 5. Target architecture of the one-command installer (designed, not built)

```
node setup.mjs [--profile <name>] [--config classhost.json]
```

| Step | What it does |
|---|---|
| 1. Preflight | AWS CLI present, profile resolves, region `us-east-1`, quotas (Lambda concurrency) read and warned about, CloudFront verification detected (the deploy error text) with the support-case steps printed |
| 2. Artifact bucket | creates a private bucket, remembers its name in a git-ignored `samconfig.local`-style file |
| 3. Try mode | `package` + `deploy` with an empty `RootDomain`; portal and sites on `cloudfront.net` (about 10 to 20 minutes) |
| 4. Domain mode (optional) | wildcard ACM certificate in `us-east-1` (prints the one validation record and waits); first deploy with `PortalAlias=false`, then the DNS records, then `PortalAlias=true` |
| 5. Optional modules | backend, AI, storage: asked, default off |
| 6. Finish | `teacher-setup`, `portal-publish` (builds the kit with the real portal address; see below), a short smoke check, a cost note, `node setup.mjs destroy` |

`--dry-run` prints every command, and tests snapshot that output instead of faking the AWS CLI.

**The portal address is substituted at build time.** Source code carries the placeholder `https://deploy.example.edu` (`starter/portal-url.mjs`). `portal-publish` knows the real address (stack output `PortalUrl`) and passes it to `buildKit`, `buildKitZip` and `buildMcpPackage`, which replace the placeholder in every text file, so a teacher's published kit and MCP package point at that teacher's own portal. Built without a portal address (tests, local), the placeholder stays.

## 6. Moving a project to another account (designed, not built)

Rule: **export needs read access only**, so it still works if the old account is about to be suspended.

`node setup.mjs export <folder> --profile old` writes to a local folder:

| Data | Source | Notes |
|---|---|---|
| Student tokens | DynamoDB tokens table (hashes) | hashes unchanged, so student tokens keep working |
| Teacher login | SSM parameter | recreating it with `teacher-setup` is the alternative |
| CLI key map | SSM parameter | |
| Sites and archives | S3 `sites/` and `archive/` | `aws s3 sync` to disk, no cross-account roles |
| Class storage data | storage table, if enabled | |
| Manifest | counts and sizes | used to verify the import |

`node setup.mjs import <folder> --profile new`:

1. Installs the stack (section 5).
2. Loads the tokens, parameters and storage table; syncs sites back to S3.
3. Re-creates each student's backend from its archived code (**open question**: how backends are stored today and whether `archive/` is enough; a half-day spike comes first).
4. Verifies: every site answers `200` on the temporary `cloudfront.net` address.

Cutover, the only step students notice: with the **same domain**, move the portal and wildcard DNS records to the new distribution (a CloudFront alias lives on one distribution at a time, so remove it from the old one first), accept minutes of downtime, keep the old stack for a week as rollback.

| Not moved | Why |
|---|---|
| AI keys | live at the AI provider; only the management key is re-entered |
| Students with custom domains | one CNAME changes (custom domains are out of v1) |
| Stored deploy tokens in students' tools | unchanged when the portal address is unchanged |

If the domain **changes**, every student's kit must point at the new portal address: avoid it.

## 7. Open items

- Finish the clean-account verification (section 4) after AWS clears CloudFront; re-run the credit activity for Bedrock.
- Build `setup.mjs` (section 5), then export and import (section 6), and test a move between two stacks in one account before a real one.
- Neutralise `samconfig.toml` for the public repository and write the example config; rename the project; create the fresh single-commit public repository (see `opensource_plan.md`).
