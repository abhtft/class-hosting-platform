# Real-AWS test plan for 2.1.0 (new / throwaway account)

Private (.temp is git-ignored). Goal: prove on a real account everything that so far passed only against fakes.
Never run any of this on the production profile. Every AWS command carries `--profile kit-test` (use your own profile name; replace `kit-test` below).

Time: about 2 to 3 hours of work, of which about 60 minutes are CloudFront waiting (create ~15 min, delete ~25 min). Cost: a few dollars at most
(CloudFront and Lambda idle cost is near zero; the KMS key is $1 per month, prorated, and stays 7 days after deletion).

Two cycles:
- **Cycle 1 (the real teacher path): install 2.0.4, then upgrade to 2.1.0.** This proves the update path existing teachers will take, including students who already have a project.
- **Cycle 2 (optional, after cleanup): a fresh 2.1.0 install.** This proves the key is created by the template on a first install.

Mark each line `PASS` / `FAIL` / `SKIP` and paste the evidence (the output) into section 12.

---

## 0. Before you start

0.1 **The account is the test account.** The profile must be a dedicated one:

```bash
aws sts get-caller-identity --profile kit-test
```
Write down the account number. It must NOT be the production account. (Last time the KMS check ran with no profile; do not repeat that.)

0.2 **CloudFront is verified for this account.** Without it the install fails at the distribution step. If it fails with "Your account must be verified", open the AWS Support case (Account and billing) and wait.

0.3 **Old leftovers from the failed attempt.** A stack `student-hosting` in `ROLLBACK_COMPLETE` blocks a new install. Look first:

```bash
aws cloudformation describe-stacks --stack-name student-hosting --query "Stacks[0].StackStatus" --profile kit-test
```
If it shows `ROLLBACK_COMPLETE` (or the stack is gone, this step is done), remove it with the installer, never by hand:

```bash
npx -y https://github.com/abhtft/class-hosting-platform/releases/download/v2.1.0/class-platform-teacher-2.1.0.tgz destroy --everything --profile kit-test
```
(Use the 2.1.0 package for destroy: it has the sd-* cleanup.) Then list what is left, section 11.

0.4 **The throwaway KMS key from the Part A check** (`kit-signing-test`, id `84218ff1-19d2-4bab-9e4f-0d688adfd782`, created in whatever account the default profile pointed at). Schedule its deletion in THAT account:

```bash
aws kms schedule-key-deletion --key-id 84218ff1-19d2-4bab-9e4f-0d688adfd782 --pending-window-in-days 7 --profile <the profile of that account>
```

0.5 **CI is green for v2.1.0. DONE (checked after the tag): CI on the tag and on main, CodeQL and the Release workflow all show success.** If the CI job includes a Checkov step, it passed, so no KMS skip is needed. (If you later see Checkov complain about key rotation, it does not apply to asymmetric keys: add a documented skip.)

0.6 **Tools on your computer:** Node 20+, AWS CLI, a terminal with a TTY (needed for `npm run trust`). Two folders: `~/aws-test/teacher` (nothing needed) and `~/aws-test/student1`, `~/aws-test/student2`.

0.7 **Set two shell variables for the session** (a new terminal loses them):

```bash
export P=kit-test
export OLD=https://github.com/abhtft/class-hosting-platform/releases/download/v2.0.4/class-platform-teacher-2.0.4.tgz
export NEW=https://github.com/abhtft/class-hosting-platform/releases/download/v2.1.0/class-platform-teacher-2.1.0.tgz
```
(PowerShell: `$env:P='kit-test'` and `$OLD='...'`, then `npx -y $OLD ...`.)

---

## Cycle 1

## 1. Baseline: install 2.0.4

1.1 `npx -y $OLD install --profile $P` (answer: no custom domain; a teacher login name and password you will remember; no notices). Wait about 15 minutes.
 - PASS if it ends with the full check passing (a throwaway student publishes a sample site that comes up live).
1.2 `npx -y $OLD verify --user <teacher> --profile $P` shows all checks green. Save the portal address it prints.
1.3 Note the stack outputs. There must be NO `KitSigningKeyId` yet (this is 2.0.4):

```bash
aws cloudformation describe-stacks --stack-name student-hosting --query "Stacks[0].Outputs" --profile kit-test
```

## 2. Two students on the old kit

2.1 Open the teacher page (`<portal>/teacher.html`), sign in, create token **student1** (30 days) and **student2**. Copy each token when shown.
2.2 For student1: switch **Backend** on (give 100 runs per day). For student2: Backend on too (used later by the Usage tab).
2.3 Student1 does exactly what a real student does: sign in to the portal home with the token, follow the **Start here** card, create the project folder `~/aws-test/student1`, `npm run login` with the token, `npm run deploy` for a small site.
 - PASS if the site is live at its address.
2.4 Student1 also publishes a **backend** (a tiny function that prints a line and returns JSON) and calls `/api/...` from the browser. PASS if it answers.
2.5 In `student1/platform.json` there is no `signingKey` yet (old kit). `npm run platform status` shows the 2.0.4 kit.

## 3. Upgrade to 2.1.0 (the key and the permissions)

3.1 **See the plan first:**

```bash
npx -y $NEW plan --profile $P
```
 - EXPECT: a change that adds the KMS key and alias, an output, and changes the IAM permissions of the Deploy and Sweeper functions. NOTHING else (no replacement of buckets, tables or distributions). If anything is marked "replace", STOP and tell me.
3.2 `npx -y $NEW apply <plan id>` (or `upgrade --yes`). Wait for it to finish (a few minutes; no CloudFront rebuild expected).
 - PASS if it finishes without an error. FAIL if "not authorized to perform kms:CreateKey": the installing user needs KMS permission (AdministratorAccess has it).
3.3 The output now has the key:

```bash
aws cloudformation describe-stacks --stack-name student-hosting --query "Stacks[0].Outputs[?OutputKey=='KitSigningKeyId'].OutputValue" --output text --profile kit-test
```
 - PASS if it prints a key id. In the console (KMS, Customer managed keys): key spec `ECC_NIST_P256`, usage `Sign and verify`, alias `.../KitSigningKeyAlias`, and the key policy has only the account root.
3.4 The signed kit was published. Fetch the three files from the portal address:

```bash
curl -s <portal>/kit-version.json
curl -s <portal>/kit-version.json.sig
curl -s <portal>/signing-key.pub
```
 - PASS if `kit-version.json` has `"serial"` and `"keyId"`, and the other two return content (not 403/404).
3.5 `npx -y $NEW verify --user <teacher> --profile $P`
 - PASS if every check is green, including **The student kit is signed by the platform key**.

## 4. Signing, as the student sees it

4.1 **Existing student pins the key (the `needsPin` path).** In `student1`: `npm run deploy` (or `npm run platform -- update`). Then:

```bash
npm run platform status
```
 - PASS if it says the kit is updated to 2.1.0 and the key is pinned (a fingerprint is shown), and `platform.json` now has `signingKey` and `serial`.
4.2 **New student on the signed kit.** `student2`: set up the project the Start here way. PASS if `platform.json` already has the `signingKey`.
4.3 **Tamper test A: a changed kit is refused.** Using your teacher (admin) credentials only, upload a modified `kit-version.json` WITHOUT a new signature:

```bash
aws s3 ls --profile kit-test      # find the sites bucket (name contains the stack name)
aws s3 cp <bucket-copy-of-kit-version.json-with-one-changed-character> s3://<bucket>/portal/kit-version.json --profile kit-test
```
(Download the current file with `aws s3 cp s3://<bucket>/portal/kit-version.json .`, change one character, upload.)
Then in student1: `npm run deploy`.
 - PASS if it prints the refusal (bad signature), says it carries on with the kit it has, and the deploy itself still succeeds. Exit code of the sync is 3.
4.4 **Tamper test B: signature removed.** `aws s3 rm s3://<bucket>/portal/kit-version.json.sig --profile kit-test`; student1 `npm run deploy`.
 - PASS if refused as "unsigned" for a pinned project, deploy carries on.
4.5 **Restore:** `npx -y $NEW publish --profile $P` (re-signs and publishes the kit). Student1 `npm run deploy` again: PASS if there is no refusal.
4.6 **Replay / rollback test.** Publish the OLD package's kit: `npx -y $OLD publish --profile $P` (it publishes an unsigned 2.0.4 kit). Student1 `npm run deploy`.
 - PASS if student1 refuses it (the project is pinned; an unsigned or older kit is not accepted). Then restore with `npx -y $NEW publish --profile $P` and confirm no refusal.
4.7 **Deliberate rollback that must work.** Bump nothing; just run `publish` of 2.1.0 twice. PASS if the second `serial` is larger than the first (curl `kit-version.json` both times) and the student accepts both.
4.8 **`npm run trust`.** In `student2/platform.json` change one character of `signingKey` (simulating a new account's key), then run `npm run trust` in a TTY.
 - PASS if it shows the new fingerprint, asks for its first 4 characters, and re-pins when you type them correctly; and refuses when you type them wrong. Restore the file afterwards.
4.9 Exit-code check: `npm run platform -- update; echo $?` in a refused state prints `3` and never touches the student's files (look at `.kit-backup/` unchanged).

## 5. The permission change (F1): nothing legitimate broke, the bad thing is blocked

5.1 **Find the pieces:**

```bash
aws cloudformation list-stack-resources --stack-name student-hosting --query "StackResourceSummaries[?ResourceType=='AWS::IAM::Role'].[LogicalResourceId,PhysicalResourceId]" --output text --profile kit-test
aws cloudformation list-stack-resources --stack-name student-hosting --query "StackResourceSummaries[?ResourceType=='AWS::Lambda::Function'].[LogicalResourceId,PhysicalResourceId]" --output text --profile kit-test
```
5.2 **Simulate the writes (deterministic, no side effects).** For the Deploy role, the Sweeper role and the Portal role (get each role ARN with `aws iam get-role --role-name <name> --query Role.Arn --output text --profile kit-test`):

```bash
aws iam simulate-principal-policy --policy-source-arn <role arn> --action-names s3:PutObject s3:DeleteObject s3:GetObject --resource-arns arn:aws:s3:::<bucket>/portal/kit-version.json arn:aws:s3:::<bucket>/sites/x/index.html arn:aws:s3:::<bucket>/userdata/x --query "EvaluationResults[].[EvalActionName,EvalResourceName,EvalDecision]" --output text --profile kit-test
```
 - EXPECT Deploy role: `portal/...` PutObject and DeleteObject = `implicitDeny`; `sites/...` allowed; `userdata/...` only DeleteObject allowed.
 - EXPECT Sweeper role: `portal/...` denied; `sites/...` delete allowed.
 - EXPECT Portal role: `userdata/...` DeleteObject allowed, Get/Put denied; `portal/...` Put denied.
 - FAIL if any role may write `portal/`. That is the F1 hole.
5.3 **No function can sign.** Simulate `kms:Sign` for every role (all Lambda roles, including the student sandbox role) against the key ARN: EXPECT `implicitDeny` for all.

```bash
aws iam simulate-principal-policy --policy-source-arn <role arn> --action-names kms:Sign kms:GetPublicKey --resource-arns <key arn> --query "EvaluationResults[].[EvalActionName,EvalDecision]" --output text --profile kit-test
```
5.4 **The real flows still work (the point of the change):**
 - Student1 redeploys a site after deleting a page: the deleted page disappears (Deploy function delete in `sites/`). PASS/FAIL.
 - Student1 takes the site offline and online (archive/restore). PASS/FAIL.
 - A file upload through the portal flow if the student kit uses one (`uploads/`). PASS/FAIL or SKIP.
 - Teacher page: **clear** student2's stored data (needs class storage on; switch it on for student2, have the backend write one record, then Clear). PASS if it succeeds (Portal function delete on `userdata/`).
 - Run the sweeper by hand and read its log for AccessDenied:

```bash
aws lambda invoke --function-name <sweeper physical id> --profile kit-test out.json
aws logs tail /aws/lambda/<sweeper physical id> --since 5m --profile kit-test
```
 - PASS if there is no `AccessDenied` / `not authorized`.
5.5 Teacher page still loads, the tokens list works, extend/revoke work (a quick click-through).

## 6. Logs

Student1's backend (small Python or Node function). Add three behaviours: `print` a normal line with a made-up value of one of the student's variables, raise an exception (traceback), and a slow path that sleeps longer than 10 seconds (to get "Task timed out"). Set one env var with a value like `SECRETVALUE123` (via the project's env config) and print it.

6.1 `npm run logs`: the newest lines are last; START/END lines are absent; the value `SECRETVALUE123` appears as `***`. PASS/FAIL.
6.2 `npm run logs -- --errors`: shows the traceback (with its LAST line, the real error) and the timeout line; shows none of the normal lines. PASS/FAIL. **This verifies the error filter pattern on real CloudWatch.**
6.3 `npm run logs -- --minutes 5 --limit 10`: at most 10 lines, only the last 5 minutes.
6.4 A long message (print 5000 characters, then an error): the cut keeps the END and shows `[...cut]`.
6.5 Portal, student's Backend card: the Errors-only checkbox and the minutes list work and match the CLI.
6.6 AI editor `logs` tool (optional): in the editor, ask "why is my backend failing?" and see it call `logs`.
6.7 A student without a backend gets a clear message, not a stack trace.

## 7. Usage tab (teacher page)

7.1 Make 5 requests to student1's backend and 2 to student2's.
7.2 Teacher page, **Usage** tab: both rows show the right "runs used / allowed"; totals show 2 students with the backend on; the AI part says "no AI caps yet" (AI is off). PASS/FAIL.
7.3 Set student1's run allowance to 6 and make 1 more request: the row moves to the top and counts toward "near a limit" (80% or more).
7.4 AI part (SKIP unless you have a real OpenRouter or Gemini key): `npx -y $NEW secrets --profile $P`, switch AI on for student1 with a $1 cap, check the row, the total "the most the AI can cost", and make one call.

## 8. Cache

8.1 Publish a small site; `curl -sI <site address>/index.html`: EXPECT `cache-control` with `s-maxage=86400` (one day) and the browser part revalidating; NOT a year (31536000). PASS/FAIL.
8.2 Change a word, `npm run deploy`, `curl -s <site address>/` right away: the new text is shown (the invalidation worked). PASS/FAIL.
8.3 The failed-invalidation warning (SKIP unless you want to prove it on AWS): the unit test covers it. To prove it live, temporarily remove `cloudfront:CreateInvalidation` from the Deploy role in the console, publish, expect the publish to SUCCEED with a visible warning, then restore the permission by running `apply` of the same plan (or `upgrade`). Only do this on the test account.

## 9. Interview (manual, 10 minutes)

9.1 In the AI editor on student2's project, say "I want to make an app for my class". EXPECT: the assistant asks 3 to 6 numbered plain questions with choices and a default, no technology words, then writes `SPEC.md`, shows a short summary and asks "Is this right?" before coding. PASS/FAIL (this is AI behaviour: try two different editors if you have them).
9.2 Say "just build it, a quiz about space with 10 questions, dark": EXPECT it skips the interview and writes `SPEC.md` from your words.

## 10. Roll the teacher back and forward (optional but valuable)

10.1 `npx -y $OLD plan --profile $P`: it should refuse or warn about going back (a pinned project must not break). Read the output; do NOT apply unless you want to test it.
10.2 Skip if time is short.

## 11. Destroy and verify nothing is left

Before destroying, make sure at least one `sd-*` function exists (student1 backend) and the logs above ran, so the cleanup has something to find.

11.1 `npx -y $NEW destroy --everything --profile $P` (type the teacher login and the stack name when asked). The preview should count the student backend functions. Wait for it (CloudFront deletion about 25 minutes).
 - PASS if it reports ok with no "kept" and no "failed functions".
11.2 **Leftovers check, each of these must be empty (or only the allowed item):**

```bash
aws lambda list-functions --query "Functions[?starts_with(FunctionName,'sd-')].FunctionName" --output text --profile kit-test
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/sd- --query "logGroups[].logGroupName" --output text --profile kit-test
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/student-hosting --query "logGroups[].logGroupName" --output text --profile kit-test
aws cloudformation describe-stacks --stack-name student-hosting --profile kit-test
aws s3 ls --profile kit-test
aws dynamodb list-tables --profile kit-test
aws cloudfront list-distributions --query "DistributionList.Items[].Id" --output text --profile kit-test
aws secretsmanager list-secrets --query "SecretList[].Name" --profile kit-test
aws ssm describe-parameters --query "Parameters[].Name" --profile kit-test
aws kms list-aliases --query "Aliases[?contains(AliasName,'Kit')]" --profile kit-test
```
 - EXPECT: nothing from the stack; the KMS key is `PendingDeletion` (check with `aws kms describe-key --key-id <id>`; it deletes itself after 7 days). The alias may linger until the key is gone.
 - Any leftover: write it down. That is a bug to fix before 2.1.1.
11.3 After 7 days the key is gone; check once (cost stops).

---

## Cycle 2 (optional): fresh 2.1.0 install

C2.1 `npx -y $NEW install --profile $P` on the clean account. EXPECT: the stack is created WITH the key, the signed kit is published at the end, the final check includes the signature check, all green.
C2.2 `verify`, create one student, `npm run deploy`: the project is pinned from its first sync (4.2 proof on a fresh platform).
C2.3 `destroy --everything`, then repeat 11.2.

---

## 12. Record of results

| # | Check | PASS/FAIL/SKIP | Evidence / note |
|---|---|---|---|
| 0.1 | test account confirmed | | |
| 0.5 | CI + Checkov green | | |
| 1.1-1.2 | 2.0.4 install + verify | | |
| 2.3-2.4 | student site + backend on the old kit | | |
| 3.1 | plan shows only key + IAM changes | | |
| 3.2-3.3 | apply + key exists | | |
| 3.4-3.5 | signed files served, verify green | | |
| 4.1 | existing student pins key | | |
| 4.2 | new student pinned at start | | |
| 4.3 | tampered kit refused | | |
| 4.4 | removed signature refused | | |
| 4.5 | restore accepted | | |
| 4.6 | unsigned/old kit refused | | |
| 4.7 | newer serial accepted | | |
| 4.8 | trust command | | |
| 5.2 | portal/ write denied for all functions | | |
| 5.3 | no role may sign | | |
| 5.4 | redeploy, offline, clear data, sweeper OK | | |
| 6.1-6.5 | logs (newest, masked, errors-only, cut) | | |
| 7.2-7.3 | usage tab numbers | | |
| 8.1-8.2 | cache one day, instant refresh | | |
| 9.1-9.2 | interview behaviour | | |
| 11.1-11.2 | destroy leaves nothing | | |
| C2 | fresh 2.1.0 install | | |

## 13. If something fails (likely cause, first move)

| Symptom | Likely cause | First move |
|---|---|---|
| install fails "account must be verified" | CloudFront not yet verified | wait for the AWS Support case |
| `apply` fails on KMS | the installing user lacks KMS rights | use an admin user; document the needed actions in TEACHER_SETUP |
| Checkov fails on the KMS key | rotation does not apply to asymmetric keys | add a skip with a comment in the template (2.1.1) |
| redeploy or offline fails with AccessDenied | an S3 prefix missing from the Deploy grant | read the denied key in the Deploy function log; add the prefix; add it to the template test |
| clear data fails | Portal function lacks DeleteObject on `userdata/` | same |
| signed kit not served / 403 on `.sig` | content type or CloudFront path not covered | check `portal/` objects and the portal behaviours |
| student refuses a good kit | serial or key mismatch (e.g. publish done by different code) | `curl` the three files, compare with `platform.json` |
| logs show nothing for errors | filter pattern does not match the runtime's wording | read raw `aws logs filter-log-events` output, adjust `LOG_ERRORS_PATTERN` in `lambda/backends.py` |
| destroy leaves `sd-*` | the role match missed them | check their Role ARN vs the sandbox role |

Every bug found becomes a permanent test first, then a fix, then a 2.1.1 release (only on your explicit "release 2.1.1").
