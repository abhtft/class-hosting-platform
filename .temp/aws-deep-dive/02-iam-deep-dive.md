# 02. IAM deep dive: identity, permissions, and how to prove them

Goal: read any IAM policy fluently, explain why each role in your stack has exactly the permissions it has, and **prove** a permission or a denial with the policy simulator instead of trusting the policy text.

---

## Part 1. Theory

### 1.1 Three layers of identity in your platform (do not mix them up)

| Layer | Who | Credential | Checked by | Example |
|---|---|---|---|---|
| **AWS IAM** | You, and each Lambda function | IAM user keys; or a role's temporary credentials | AWS itself, on every API call | `DeployFunction` calls `s3:PutObject` |
| **Application sessions** | Teacher, student (in the portal) | HMAC-signed session token (8 h / 12 h) | `portal.verify_session` | A student calls `/api/student/publish` |
| **Per-student API keys** | A student's backend, calling platform APIs | `cs_<slug>.<secret>` (storage), `ga_<slug>.<secret>` (Gemini proxy) | `storage.authenticate`, `ai_proxy.authenticate` | A student function calls `StorageApi` |

IAM protects your **AWS resources**. The other two layers protect **your application**. A request from a student's browser never carries AWS credentials: IAM is only about what your *own code* may do in your account.

### 1.2 Anatomy of an ARN

`arn:aws:lambda:us-east-1:123456789012:function:sd-alice`

`arn : partition : service : region : account-id : resource-type : resource-name`. IAM policies match ARNs with `*` (any run of characters) and `?` (exactly one character). Some services have no region or no account in the ARN (S3 bucket ARNs have neither: `arn:aws:s3:::bucket/key`; IAM is global).

### 1.3 The elements of a policy statement

```json
{
  "Effect": "Allow",                         // Allow or Deny
  "Action": ["s3:GetObject"],                // service:Operation  (wildcards allowed)
  "Resource": "arn:aws:s3:::b/sites/*",      // what it applies to
  "Principal": {"Service": "cloudfront.amazonaws.com"},   // ONLY in resource-based and trust policies: who
  "Condition": {"StringEquals": {"AWS:SourceArn": "arn:..."}}   // extra rules that must be true
}
```

- **Identity-based policy** (attached to a user/role): no `Principal`, because the principal *is* whoever it is attached to.
- **Resource-based policy** (bucket policy, SNS topic policy, Lambda resource policy, KMS key policy): has a `Principal`.
- **Trust policy** (a role's "who may assume me"): a resource-based policy on the role.

### 1.4 The evaluation logic (memorise this order)

For one request AWS decides **Allow** or **Deny** like this:

1. **Start from: deny** (everything is denied by default).
2. If **any applicable policy has an explicit `Deny`** that matches: **final answer Deny**. An explicit deny always wins.
3. Check the **guard rails** that can only *remove* power: Organizations **SCPs**, **resource control policies**, **permission boundaries**, **session policies**. If one of them does not allow the action: Deny.
4. Check for an **Allow** in an identity-based policy and/or a resource-based policy. Within one account, an allow in either is enough (cross-account needs both sides).
5. No allow found: Deny.

Key consequence: **permission boundaries and SCPs never grant anything. They only set a ceiling.** The effective permission of a role with a boundary is the **intersection** of (what its identity policies allow) and (what its boundary allows).

### 1.5 Roles, trust and temporary credentials

A **role** has two documents:

1. **Trust policy** ("AssumeRolePolicyDocument"): *who* may become this role. For a Lambda execution role: `Principal: {Service: lambda.amazonaws.com}`, `Action: sts:AssumeRole`.
2. **Permission policies** (inline and/or managed): *what* the role may do once assumed.

When a function starts, the Lambda service calls **STS** `AssumeRole`, gets **temporary credentials** (access key id + secret + session token, valid for hours and rotated automatically), and injects them into the execution environment as `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`. boto3 reads them automatically. **There are no long-lived keys on any function.**

> Learning point: that also means *student code running in a student function can read those three variables.* That is why the student role must be almost powerless (section 4.7).

### 1.6 `iam:PassRole`: the quiet privilege-escalation door

To create a Lambda function with role R, the caller needs `lambda:CreateFunction` **and** `iam:PassRole` on R. Without PassRole control, anyone who can create functions could attach a powerful role to their own code and run it: that is an escalation. Defence: allow PassRole **only for specific role ARNs**, and add the condition `iam:PassedToService = lambda.amazonaws.com`. Your `DeployFunction` does exactly this for `StudentSandboxRole` (template.yaml lines 787 to 792).

### 1.7 The confused deputy problem and `AWS:SourceArn`

A bucket policy that says "the CloudFront **service** may read this bucket" (`Principal: {Service: cloudfront.amazonaws.com}`) would, on its own, let **any** CloudFront distribution in **any** AWS account read your bucket, because they are all the same service principal. CloudFront would be a "confused deputy" acting for a stranger.

The fix is a condition tying the permission to *your* distribution:

```
"Condition": {"StringEquals": {"AWS:SourceArn": "arn:aws:cloudfront::<your-account>:distribution/<your-distribution-id>"}}
```

Your `SitesBucketPolicy` has this on all three statements (template.yaml lines 210 to 245). The SNS policy uses the sibling key `aws:SourceAccount` for the same reason (line 893).

### 1.8 Fine-grained condition keys you use

| Condition key | Where | What it does |
|---|---|---|
| `iam:PassedToService` | DeployFunction PassRole | The role may only be handed to Lambda |
| `AWS:SourceArn` | Bucket policy | Only *your* distribution/tenants read the bucket |
| `dynamodb:LeadingKeys` | StorageFunction on TokensTable | Item-level access control: PutItem/DeleteItem allowed **only** on partition keys that begin with `scoll#` |
| `s3:prefix` | StorageFunction ListBucket | May list only keys under `userdata/` |
| `aws:SourceOwner` / `aws:SourceAccount` | SNS topic policy | Only this account's alarms/subscribers |

### 1.9 Secrets Manager ARN suffix `-??????`

Secrets Manager appends a **6-character random suffix** to every secret ARN (`...:secret:openrouter-aB3dEf`). An IAM policy cannot know the suffix in advance, so the policy writes `openrouter-??????` (exactly six characters). This is better than `openrouter*`, which would also match an unrelated secret named `openrouter-staging`. (A caveat: `openrouter-??????` still matches *any* six characters, so a different secret named e.g. `openrouter-abcdef` would match: keep unrelated secrets out of that name pattern.)

### 1.10 Least privilege is a spectrum, not a switch

Perfect least privilege per function is costly (more roles, more policies, more things to keep in sync). Good practice is: scope **resources** tightly (named ARNs), scope **actions** to what the code really calls, and spend your effort where an attacker would profit most (here: the student sandbox and the PassRole door).

---

## Part 2. What we built: every identity in the stack

### 2.0 The roles (SAM generates one execution role per function)

SAM's `Policies:` property on a function creates an **inline policy** on an auto-generated role named like `student-hosting-DeployFunctionRole-AbC123XyZ`, with the trust policy for `lambda.amazonaws.com` and the AWS managed policy `AWSLambdaBasicExecutionRole` (CloudWatch Logs: create log group/stream, put events). Find them: `aws iam list-roles --query "Roles[?contains(RoleName,'student-hosting')].RoleName"`.

### 2.1 `DeployFunction` role (the control plane; the most powerful)

| Statement | Actions | Resource | Why | Risk if compromised |
|---|---|---|---|---|
| Sites bucket | `s3:Get/Put/DeleteObject`, `s3:ListBucket` | whole bucket `/*` | Publish sites, uploads, userdata deletes | Can overwrite any site, the portal pages (`portal/`), and student data |
| Invalidate | `cloudfront:CreateInvalidation` | the one distribution | Refresh caches after publish | Low (cost only) |
| Teacher/keys | `ssm:GetParameter` | the two parameters | Load teacher login and legacy keys | **Reads the teacher hash and the session-signing secret** |
| Tokens | `dynamodb:Get/Put/Update/DeleteItem`, `Scan` | TokensTable | All application state | Can forge/alter tokens, extend expiry, read stored student keys |
| Custom domains | `cloudfront:Create/Get/Update/DeleteDistributionTenant`, `GetManagedCertificateDetails`, `CreateInvalidation*` | the tenant distribution and `distribution-tenant/*` | Student domains | Can attach domains to the tenant distribution |
| ACM | `acm:RequestCertificate`, `Describe`, `List`, `AddTags` | `*` | Request certificates | Can request certificates (low) |
| Student functions | `lambda:CreateFunction`, `UpdateFunctionCode`, `UpdateFunctionConfiguration`, `Get*`, `DeleteFunction`, `Put/DeleteFunctionConcurrency`, `TagResource` | `function:sd-*` | Manage student backends | Can change or delete any `sd-*` function; **cannot** touch other functions |
| AI secret | `secretsmanager:GetSecretValue` | `openrouter-??????` (only if AI is on) | Create per-student keys | Reads your **OpenRouter management key** and the Google key |
| Storage cleanup | `dynamodb:Query`, `BatchWriteItem`, `DeleteItem` | StudentDataTable (only if storage on) | "Clear this student's data" | Delete student data |
| PassRole | `iam:PassRole` | **only** `StudentSandboxRole`, with `iam:PassedToService=lambda.amazonaws.com` | Create student functions | Cannot attach any other role |
| Logs | `logs:CreateLogGroup`, `PutRetentionPolicy`, `DeleteLogGroup`, `FilterLogEvents` | `/aws/lambda/sd-*` | Create and read a student's log group | Read student logs |

**Read this role as a threat model:** it is the keys to the kingdom *of the platform*, but even a total compromise of this function **cannot**: create IAM roles/users, pass another role to Lambda, touch non-`sd-*` functions, read other secrets, or reach other AWS services.

### 2.2 `GatewayFunction` role (the front door)

| Actions | Resource | Why |
|---|---|---|
| `dynamodb:GetItem/PutItem/UpdateItem` | TokensTable | Read settings; counters (rate limit, in-flight, allowance) |
| `lambda:InvokeFunction` | `function:sd-*` | Run a student function |

Notably **no S3, no SSM, no Secrets Manager.** It cannot even delete an item (`DeleteItem` is not granted). If someone hijacked it they could invoke student functions and tamper with counters, and nothing else.

### 2.2b Why "Get/Put/Update but not Delete" matters

Counters use `UpdateItem` with `ADD`. The gateway never needs `DeleteItem`. Granting only what the code calls is the cheapest, highest-value least-privilege habit.

### 2.3 `StorageFunction` role

| Actions | Resource | Condition | Why |
|---|---|---|---|
| `GetItem`, `UpdateItem` | TokensTable | none | Key lookup; counters |
| `PutItem`, `DeleteItem` | TokensTable | `ForAllValues:StringLike dynamodb:LeadingKeys = scoll#*` | Maintain the list of a student's collection names **and nothing else** |
| `GetItem/PutItem/DeleteItem/Query` | StudentDataTable | none | The data itself |
| `s3:Get/Put/DeleteObject` | bucket `/userdata/*` | none | Student files (never `sites/` or `portal/`) |
| `s3:ListBucket` | bucket | `s3:prefix = userdata/*` | List only student files |

This is **attribute-based fine-grained access control on DynamoDB**: the function can read/update any token item (it must, to find the key), but may only *create or delete* items whose partition key starts with `scoll#`. So even a bug in storage code cannot create a fake `tok#...` token item.

### 2.4 `AiProxyFunction` role

`dynamodb:GetItem/UpdateItem` on TokensTable, and `secretsmanager:GetSecretValue` on `openrouter-??????`. It holds the Google key for the duration of a call, which is why it is a **separate function with a separate role**: a bug in the portal cannot expose it through this path, and vice versa.

### 2.5 `SweeperFunction` role

S3 bucket access (archive/restore), `cloudfront:CreateInvalidation`, `dynamodb:UpdateItem/DeleteItem/Scan` on TokensTable, optional `secretsmanager:GetSecretValue`, and tenant `Get/Delete/Invalidate`. It needs `Scan` to find expired tokens.

### 2.6 `StudentSandboxRole` + `StudentSandboxBoundary` (the untrusted code)

```
StudentSandboxBoundary (managed policy, the CEILING):
   Allow logs:CreateLogStream, logs:PutLogEvents   on log-group:/aws/lambda/sd-*:*

StudentSandboxRole:
   trust:      lambda.amazonaws.com may assume
   permission: write-own-logs (the same two actions, same resource)
   boundary:   StudentSandboxBoundary
```

Effective permissions = intersection = **write log events into `/aws/lambda/sd-*` log groups, and nothing else.** A student function cannot read S3, DynamoDB, SSM, Secrets Manager, call other Lambdas, or even create a log group (the platform creates the group). Two lines of defence say the same thing (role policy and boundary), so even an accidental edit of one cannot widen it.

> **Observation to keep in mind (see `09`):** the resource pattern is `log-group:/aws/lambda/sd-*:*`, i.e. *all* students' log groups, not only the caller's. All student functions share this one role, so one student's function could **write** a log line into another student's log group (it cannot read). That is a log-integrity weakness, not a confidentiality one. Per-student roles would close it, at the cost of one role per student.

### 2.7 The humans and the installer

- **You** (IAM user with `AdministratorAccess`): runs CloudFormation, which creates the roles above. CloudFormation uses *your* permissions; it needs `CAPABILITY_IAM` (you acknowledge that the stack creates IAM resources) and `CAPABILITY_AUTO_EXPAND` (SAM macro).
- **CloudFormation**: no service role is configured, so it acts as the caller.
- **No IAM users exist for students or for the app.** Nothing in the platform holds a long-lived AWS key (except the one you may save with `setup` in your own `~/.aws`).

### 2.8 Resource-based policies in the stack

| Where | What it says |
|---|---|
| `SitesBucketPolicy` | CloudFront (your distributions only) may `GetObject` on `sites/*` and `portal/*` |
| `AlertsTopicPolicy` | Your account may publish/subscribe; CloudWatch alarms in your account may publish |
| Lambda **resource policies** (added by SAM) | API Gateway may invoke each function, scoped to its own API (`aws lambda get-policy --function-name ...`) |
| Secrets Manager / SSM | no resource policy (access is identity-based) |

---

## Part 3. Console steps (Mode B)

### 3.1 Read a role

1. **IAM** -> **Roles** -> search `student-hosting`. You should see five execution roles plus `StudentSandboxRole`.
2. Open `...-DeployFunctionRole-...`:
   - **Permissions** tab: the inline policy (`DeployFunctionRolePolicy1` or similar) and `AWSLambdaBasicExecutionRole`. Click the inline policy -> **JSON** to read it.
   - **Trust relationships** tab: confirms only `lambda.amazonaws.com`.
   - **Access Advisor** tab: **last accessed** per service. *Anything that says "not accessed" for a service the role is allowed to use is a candidate for tightening.* (Needs some usage and a few hours of data.)
3. Open `StudentSandboxRole`: the **Permissions boundary** box shows `StudentSandboxBoundary`. Click it to read the ceiling.
4. **IAM** -> **Policies** -> `StudentSandboxBoundary`: JSON view.

### 3.2 IAM Access Analyzer (free for external access findings)

**IAM** -> **Access Analyzer** -> create an analyzer (zone of trust: your account) -> **Findings**. It reports resources shared outside your account (for example an S3 bucket or role open to others). For your stack you expect **no external-access findings**. The newer *unused access* analyzer (paid) lists unused permissions: **verify** pricing before enabling.

### 3.3 CloudTrail: who did what

**CloudTrail** -> **Event history** (last 90 days of management events, free). Filter *Event name* = `CreateFunction20150331`, `PassRole` shows under the caller's call to `CreateFunction`, `AssumeRole`. You can watch the portal create a student function and see the *user identity* (the DeployFunction role session).

---

## Part 4. CLI verification

Set `ACCOUNT=$(aws sts get-caller-identity --query Account --output text)` first.

### 4.1 List and read the roles `[READ]`

```bash
aws iam list-roles --query "Roles[?contains(RoleName,'student-hosting')].[RoleName,Arn]" --output table

ROLE=<paste the DeployFunctionRole name>
aws iam get-role --role-name $ROLE --query 'Role.AssumeRolePolicyDocument'          # the trust policy
aws iam list-role-policies --role-name $ROLE                                         # inline policy names
aws iam get-role-policy --role-name $ROLE --policy-name <name from above> --query PolicyDocument
aws iam list-attached-role-policies --role-name $ROLE                                # managed policies

SBX=<paste the StudentSandboxRole name>
aws iam get-role --role-name $SBX --query 'Role.PermissionsBoundary'                 # the boundary ARN
aws iam get-policy-version --policy-arn <boundary arn> --version-id v1 --query PolicyVersion.Document
```

**Expected:** the trust policy only names `lambda.amazonaws.com`; the sandbox role shows a `PermissionsBoundary` of type `Policy`; the boundary allows only two `logs:` actions.

### 4.2 Prove permissions with the **policy simulator** `[READ]`

The simulator evaluates policies **without performing the action**.

```bash
DEPLOY_ARN=$(aws iam get-role --role-name $ROLE --query Role.Arn --output text)

# 1) May DeployFunction create a function named sd-test?        EXPECT: allowed
aws iam simulate-principal-policy --policy-source-arn $DEPLOY_ARN \
  --action-names lambda:CreateFunction \
  --resource-arns arn:aws:lambda:us-east-1:$ACCOUNT:function:sd-test \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table

# 2) ... a function NOT named sd-*?                             EXPECT: implicitDeny
aws iam simulate-principal-policy --policy-source-arn $DEPLOY_ARN \
  --action-names lambda:CreateFunction \
  --resource-arns arn:aws:lambda:us-east-1:$ACCOUNT:function:my-other-function \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table

# 3) PassRole to the sandbox role, for Lambda                   EXPECT: allowed
SBX_ARN=$(aws iam get-role --role-name $SBX --query Role.Arn --output text)
aws iam simulate-principal-policy --policy-source-arn $DEPLOY_ARN \
  --action-names iam:PassRole --resource-arns $SBX_ARN \
  --context-entries ContextKeyName=iam:PassedToService,ContextKeyValues=lambda.amazonaws.com,ContextKeyType=string \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table

# 4) PassRole to a DIFFERENT role (use DeployFunction's own role) EXPECT: implicitDeny
aws iam simulate-principal-policy --policy-source-arn $DEPLOY_ARN \
  --action-names iam:PassRole --resource-arns $DEPLOY_ARN \
  --context-entries ContextKeyName=iam:PassedToService,ContextKeyValues=lambda.amazonaws.com,ContextKeyType=string \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table
```

**Proves:** the PassRole door admits exactly one role, and student functions can only ever be named `sd-*`.

```bash
# 5) The sandbox role: can it read S3 / DynamoDB / SSM?          EXPECT: implicitDeny for each
for a in s3:GetObject dynamodb:Scan ssm:GetParameter secretsmanager:GetSecretValue lambda:InvokeFunction; do
  aws iam simulate-principal-policy --policy-source-arn $SBX_ARN --action-names $a --resource-arns '*' \
    --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
done

# 6) ...and can it write to a student log group?                 EXPECT: allowed
aws iam simulate-principal-policy --policy-source-arn $SBX_ARN --action-names logs:PutLogEvents \
  --resource-arns "arn:aws:logs:us-east-1:$ACCOUNT:log-group:/aws/lambda/sd-alice:*" \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text

# 7) ...a log group that is not sd-*?                            EXPECT: implicitDeny
aws iam simulate-principal-policy --policy-source-arn $SBX_ARN --action-names logs:PutLogEvents \
  --resource-arns "arn:aws:logs:us-east-1:$ACCOUNT:log-group:/aws/lambda/${STACK}-DeployFunction-x:*" \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
```

**Proves:** the effective permission set (intersection with the boundary) is logs-only, and that it covers *all* `sd-*` groups (the observation in 2.6).

```bash
# 8) Fine-grained DynamoDB: StorageFunction may Put an item whose key starts with scoll# but not tok#
STORAGE_ARN=$(aws iam get-role --role-name <StorageFunctionRole> --query Role.Arn --output text)
TABLE_ARN=$(aws dynamodb describe-table --table-name $(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='TokensTableName'].OutputValue" --output text) --query Table.TableArn --output text)

aws iam simulate-principal-policy --policy-source-arn $STORAGE_ARN --action-names dynamodb:PutItem --resource-arns $TABLE_ARN \
  --context-entries ContextKeyName=dynamodb:LeadingKeys,ContextKeyValues=scoll#alice,ContextKeyType=stringList \
  --query 'EvaluationResults[].[EvalDecision]' --output text         # EXPECT allowed

aws iam simulate-principal-policy --policy-source-arn $STORAGE_ARN --action-names dynamodb:PutItem --resource-arns $TABLE_ARN \
  --context-entries ContextKeyName=dynamodb:LeadingKeys,ContextKeyValues=tok#abc,ContextKeyType=stringList \
  --query 'EvaluationResults[].[EvalDecision]' --output text         # EXPECT implicitDeny
```

### 4.3 CloudTrail, last-used, and the full IAM picture `[READ]`

```bash
# who created student functions, and as whom?
aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=CreateFunction20150331 \
  --max-results 3 --query 'Events[].[EventTime,Username]' --output table

# what has this role actually used? (async: generate, then read)
JOB=$(aws iam generate-service-last-accessed-details --arn $DEPLOY_ARN --query JobId --output text)
aws iam get-service-last-accessed-details --job-id $JOB \
  --query 'ServicesLastAccessed[?TotalAuthenticatedEntities>`0`].[ServiceNamespace,LastAuthenticated]' --output table

# everything IAM knows, in one JSON (read it with jq): users, roles, policies, boundaries
aws iam get-account-authorization-details --filter Role LocalManagedPolicy > /tmp/iam.json
```

---

## Part 5. Blast-radius table (memorise)

"If an attacker gets **arbitrary code execution** inside X, what can they do with AWS?"

| X | Can | Cannot |
|---|---|---|
| A **student function** (the most likely target: students write the code) | Write log lines to any `sd-*` log group; make outbound internet calls; call platform APIs with that student's own `cs_`/`ga_` keys and OpenRouter key; burn that student's own allowances | Read any AWS data; invoke other functions; read other students' keys; change IAM |
| **GatewayFunction** | Invoke `sd-*` functions; read/update/create counter and token items | Touch S3, secrets, SSM, delete items |
| **StorageFunction** | Read/update tokens table items; read/write student data and `userdata/` | Create `tok#` items; touch `sites/`/`portal/`; read secrets |
| **AiProxyFunction** | Read the Google key; read/update token items | Anything else |
| **SweeperFunction** | Move sites to/from `archive/`; delete token items; tenant deletion | Create functions; read teacher login |
| **DeployFunction** | Everything in 2.1: overwrite sites and the portal, read the teacher hash and signing secret, forge tokens, manage `sd-*` functions | Create IAM entities; pass another role; manage non-`sd-*` functions; reach services not listed |
| **Your IAM user** | Anything in the account | n/a |

---

## Part 6. Exercises (do them on paper first, then verify)

1. Draw the **effective permission set** of `sd-alice` as a Venn diagram: role policy circle, boundary circle, their intersection.
2. Explain in two sentences why removing the `Condition` on the bucket policy would be dangerous.
3. A developer adds `s3:GetObject` on `*` to the **sandbox role** (not the boundary). What is the effective permission? (Answer: still logs only: the boundary caps it.) Now they add it to the boundary too. What changes? (Answer: now it is real: both layers allow it.)
4. Why is `iam:PassRole` needed at all if `lambda:CreateFunction` is already restricted to `sd-*`?
5. The gateway needs `UpdateItem` but not `DeleteItem`. Where in `gateway.py` can you confirm it never deletes? (Search for `db().delete`: there is none; it calls `counter_add`/`counter_sub`, which are `update_item`.)
6. Which role would you split first if you wanted to shrink the blast radius of a portal bug, and into what? (A good answer: split domain management (ACM, tenants) and student-function management out of `DeployFunction`.)
