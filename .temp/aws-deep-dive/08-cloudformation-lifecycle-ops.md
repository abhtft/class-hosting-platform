# 08. CloudFormation, the lifecycle, operations, cost and quotas

Goal: understand how the whole stack is created, updated and destroyed safely; how you find out when something breaks; what the platform costs and what bounds that cost; and which AWS limits shape the design.

---

## Part 1. CloudFormation theory

### 1.1 Concepts

- **Infrastructure as code:** a template (`template.yaml`) declares *what should exist*; CloudFormation compares it with what *does* exist and does the difference. You do not run "create this, then that".
- **Stack:** one deployed instance of a template (`student-hosting`). Every resource it creates belongs to it and is deleted with it (unless `DeletionPolicy: Retain`).
- **Template sections you use:**

| Section | What it does | In your template |
|---|---|---|
| `Parameters` | Inputs (`RootDomain`, `CertificateArn`, `BackendEnabled`, ...). `AllowedPattern`/`AllowedValues`/`MinValue` validate them. | lines 7 to 112 |
| `Rules` | Cross-parameter validation | `CertificateRequiredForDomain` (line 114): refuses a domain without a certificate |
| `Conditions` | Booleans built from parameters (`!Equals`, `!And`, `!Not`) | `UseBackend`, `UseAi`, `UseGemini`, `UseStorage`, `UseCustomDomain`, `UseAlerts`... |
| `Resources` | The things to create. A resource with `Condition:` exists only if the condition is true. | 33 resources |
| `Outputs` | Values to read afterwards (URLs, ids) | `PortalUrl`, `BucketName`, `DistributionId`, ... (the installer reads these) |
| `Transform` | A macro that expands the template first | `AWS::Serverless-2016-10-31` (SAM) |

- **Intrinsic functions you use:** `!Ref` (a resource's id or a parameter's value), `!GetAtt` (a resource attribute, e.g. `Distribution.DomainName`), `!Sub` (string interpolation `${...}`), `!If` (choose a value by condition), `!Select`/`!Split` (the origin-secret trick), `!Equals`/`!And`/`!Not`.
- **Pseudo parameters:** `AWS::StackName`, `AWS::StackId`, `AWS::Region`, `AWS::AccountId`, `AWS::Partition`, `AWS::URLSuffix`, and `AWS::NoValue` (removes a property or list item: used inside `!If` so a feature's pieces vanish when it is off).
- **Dynamic reference:** `{{resolve:ssm:/path}}` is resolved at deploy time, so the alert email address is read from SSM and never written in the repository.
- **Dependencies are inferred** from `!Ref`/`!GetAtt` (CloudFormation orders creation and deletion by them). A loop is an error: *circular dependency*.

> **The circular dependency in your template (template.yaml:506 to 511).** `Distribution` needs `BackendApi` (an origin), `BackendApi` needs `GatewayFunction` (its integration), and the shared Globals gave every function `DISTRIBUTION_ID: !Ref Distribution`, so `GatewayFunction` needed `Distribution`: a loop. The fix: the gateway **overrides** `DISTRIBUTION_ID` and `DISTRIBUTION_DOMAIN` with empty strings (it does not need them), which removes its reference. Lesson: Globals are convenient but create hidden dependencies.

### 1.2 SAM (Serverless Application Model)

The `Transform` line makes CloudFormation expand SAM shorthand into real resources:

| You wrote | CloudFormation gets |
|---|---|
| `AWS::Serverless::Function` | `AWS::Lambda::Function` + an `AWS::IAM::Role` (with `AWSLambdaBasicExecutionRole` and your `Policies:` as an inline policy) + `AWS::Lambda::Permission` for each event + event-source wiring |
| `AWS::Serverless::HttpApi` | `AWS::ApiGatewayV2::Api` + a `$default` `Stage` (auto-deploy) |
| `Events: Type: HttpApi` | `AWS::ApiGatewayV2::Route` + `Integration` (+ the Lambda permission) |
| `Events: Type: Schedule` | An EventBridge rule + target + permission |

That expansion is why `CAPABILITY_AUTO_EXPAND` is needed on deploy, and why the logical IDs in the console include generated ones. See the expanded template: `sam validate --lint`, or in the console CloudFormation -> stack -> **Template** -> *View processed template*.

### 1.3 Update behaviour and change sets

When you change a template or parameters, each affected resource is updated one of three ways (the property's documentation says which):

| Behaviour | Meaning |
|---|---|
| **No interruption** | Updated in place |
| **Some interruption** | Updated in place with a brief outage |
| **Replacement** | A **new** resource is created, then the old one deleted. For a database or bucket that is **data loss unless the policy is Retain**. |

A **change set** previews the changes without applying them: a list of `Add` / `Modify` / `Remove`, and for each `Modify` whether **`Replacement`** is `True`, `False` or `Conditional`. **This is exactly what your installer's `plan` is**: `cloudformation deploy --no-execute-changeset` creates the change set; the installer summarises it; **`apply` executes that exact change set** (`execute-change-set`) after checking that its id and contents match the plan you were shown. And the installer **refuses** a plan that replaces or removes a data-holding resource (tables, buckets, secrets, parameters, distributions).

Look at your last upgrade: "modifies 11, removes 0, replaces no data"; the `SweeperFunctionDailyPermission ... replacement: Conditional` line is a Lambda permission (stateless: recreated harmlessly).

### 1.4 Stack states you will meet

| State | Meaning | What to do |
|---|---|---|
| `CREATE_COMPLETE` / `UPDATE_COMPLETE` | Healthy | nothing |
| `CREATE_FAILED` -> `ROLLBACK_IN_PROGRESS` -> **`ROLLBACK_COMPLETE`** | A first create failed; CloudFormation rolled everything back, but **the stack object remains** and can only be **deleted** (you cannot update it) | Read the reason, `destroy`, fix, reinstall (this is what happened with the CloudFront verification error) |
| `UPDATE_ROLLBACK_COMPLETE` | An **update** failed and was rolled back; the stack still works | Fix the cause, retry the update. **Never destroy.** |
| `UPDATE_ROLLBACK_FAILED` | The rollback itself failed | Use *Continue update rollback* (console), skipping the stuck resource if necessary |
| `DELETE_FAILED` | A resource could not be deleted (for example a non-empty versioned bucket, or a CloudFront origin access control still in use) | Fix the blocker and retry |

### 1.5 Other safety features (and which you use)

| Feature | What | Used? |
|---|---|---|
| `DeletionPolicy: Retain` / `UpdateReplacePolicy: Retain` | Keep the resource when the stack deletes it or replaces it | **Yes** on `StudentDataTable` |
| **Termination protection** | Refuses `delete-stack` until turned off | Not enabled (the installer's typed phrase is your guard) |
| **Stack policy** | Blocks updates to specific resources | Not used |
| **Drift detection** | Reports resources changed *outside* CloudFormation (someone edited a policy in the console) | Available: run it (4.1) |
| **Capabilities** | You must acknowledge that the stack creates IAM resources (`CAPABILITY_IAM`) and uses macros (`CAPABILITY_AUTO_EXPAND`) | Yes, in `deployArgs` |

### 1.5b What lives outside CloudFormation (recap, because it matters here)

Secrets Manager secret, ACM certificate, the artifacts bucket, the multi-tenant distribution + connection group + two CloudFront functions, student functions `sd-*` and their log groups, distribution tenants. Consequences:

- **Drift detection will not see them** and `delete-stack` will not delete them: the installer's `destroy` removes the multi-tenant pieces **first** (an OAC cannot be deleted while a distribution still uses it), then the bucket contents, then the stack.
- **Student functions `sd-*` are not in the stack, and `destroy` does not delete them.** I read `installer/engine.mjs` `destroy` (lines 556 to 596): it tears down the tenant pieces, empties and deletes the buckets, deletes the stack, and with `--everything` deletes the tables, SSM parameters and the `openrouter` secret. It never lists or deletes `sd-*` functions, nor any log group. So after `destroy --everything` on a platform where students deployed backends you will still find: the `sd-<slug>` **Lambda functions** (their execution role is gone, so they can no longer run), their `/aws/lambda/sd-*` **log groups** (7-day retention, so they expire), and the **platform functions' log groups** `/aws/lambda/<stack>-*` (created by Lambda at first run, not by the stack, so they are never deleted and have no retention). Also outside AWS: the per-student **OpenRouter sub-keys stay valid** at OpenRouter until their cap or you revoke them there. The README says destroy leaves the account empty: that is **not fully true** (see Lab 14 in `10` to see it yourself). This is on the fix list.

---

## Part 2. The install, update and destroy flows mapped to AWS calls

| Command | What it does in AWS |
|---|---|
| `install` | `sts get-caller-identity` -> `cloudfront list-distributions` (verification probe) -> `acm request-certificate` (if a domain) -> `s3api create-bucket` (artifacts) -> `cloudformation package` (zip `lambda/`, upload, rewrite `CodeUri`) -> `cloudformation deploy` (create stack, wait) -> `portal-publish` (upload `web/` to `portal/`) -> custom-domain pieces -> smoke test. A failure in `deploy` now reads the **stack events** and explains the first real cause. |
| `plan` | `package` + `deploy --no-execute-changeset` -> `describe-change-set` -> summary and a plan id (a hash of the change set id and its items) |
| `apply <id>` | re-check the change set, `execute-change-set`, `wait stack-update-complete`, `portal-publish` |
| `verify` | HTTP checks, a throwaway student publishes a sample site, then the student is revoked and its files removed |
| `export` | `dynamodb scan`, `s3 sync` out, `ssm get-parameter` |
| `import` | `batch-write-item`, `s3 sync` in, `ssm put-parameter` (refuses to mix classes) |
| `destroy` | tear down tenants/multi-tenant distribution/connection group and the two tenant CloudFront functions -> empty the bucket **including all versions** -> `delete-stack` -> delete the artifacts bucket; with `--everything` also the data table, SSM parameters, and the secret. **Does not** delete student `sd-*` functions or any log groups (see 1.5b). |

Plan ids and `--dry-run`: `--dry-run` prints every **mutating** AWS command (`create-`, `put-`, `delete-`, `deploy`, ...) without running it (`isMutating` in `installer/engine.mjs`).

---

## Part 3. Operations: how you find out something is wrong

### 3.1 The seven alarms (all optional: they exist only if you set the alert-email parameter)

Every alarm publishes to one **SNS topic** with an **email subscription**. AWS emails a confirmation link **once**; **alerts only arrive after you click it** (an unconfirmed subscription is the commonest reason "the alarm never emailed").

| Alarm | Metric (namespace) | Condition | Means | First look |
|---|---|---|---|---|
| `lambda-concurrency-near-limit` | `ConcurrentExecutions` (AWS/Lambda), Maximum, 60 s | >= **8** (default; sized for a quota of 10) | Account-wide Lambda concurrency is near the limit: other apps can be throttled | Which function is busy (Lambda Monitor tab) |
| `lambda-throttles` | `Throttles`, Sum, 5 min | >= 5 | Invocations were refused (limit hit) | Concurrency quota |
| `portal-function-errors` | `Errors` for `DeployFunction`, 5 min | >= 1 | The portal crashed | `/aws/lambda/...DeployFunction...` logs |
| `sweeper-errors` | `Errors` for `SweeperFunction`, 24 h | >= 1 | The daily job failed (retries tomorrow) | Sweeper logs |
| `backend-gateway-errors` | `Errors` for `GatewayFunction`, 5 min | >= 1 | Platform bug in the gateway (not a student's function) | Gateway logs |
| `storage-errors` | `Errors` for `StorageFunction`, 5 min | >= 1 | Platform bug in storage | Storage logs |
| `backend-5xx` | `5xx` (AWS/ApiGateway, `ApiId`=BackendApi), 5 min | >= 20 | Many 5xx from student backends | The busiest `sd-*` function's logs |

All use `TreatMissingData: notBreaching` (no data is "fine", which suits a classroom that is idle at night). **What these do not cover:** cost, the CloudFront error rate, DynamoDB throttling, API Gateway 4xx floods, login-failure spikes. See 4.3.

### 3.2 Logs

| Log group | Content | Retention |
|---|---|---|
| `/aws/lambda/<stack>-DeployFunction-...` (and the other platform functions) | Python prints/exceptions; the gateway's one JSON line per call | **Not set in the template -> never expires.** Verify with `describe-log-groups`; consider setting 30 days. (Cost: about $0.50 per GB ingested + $0.03 per GB-month stored; volume is small.) |
| `/aws/lambda/sd-<slug>` | The student's function output | **7 days**, set by the portal |
| API Gateway **access logs** | Per-request logs | **Not enabled** (no `AccessLogSettings`). Add one if you need request-level forensics. |
| CloudFront **standard logs** | Per-request logs to S3 | Not enabled (cost and privacy trade-off) |
| **CloudTrail** | Management API calls | Event history (90 days) is on by default and free; a **trail** to S3 is not created by the platform |

### 3.3 Useful Logs Insights queries `[READ]`

On the **DeployFunction** group:

```
fields @timestamp, @message
| filter @message like /Traceback|ERROR|Exception/
| sort @timestamp desc | limit 50
```

On the **Gateway** group: per-student call counts and latency (see `04`, 4.3). On the **Sweeper** group:

```
fields @timestamp, @message | filter @message like /archived|failed/ | sort @timestamp desc | limit 20
```

### 3.4 Metrics worth a dashboard

CloudFront: `Requests`, `4xxErrorRate`, `5xxErrorRate`, `BytesDownloaded` (global, us-east-1). Lambda: `Invocations`, `Errors`, `Throttles`, `ConcurrentExecutions`, `Duration`. API Gateway: `Count`, `4xx`, `5xx`, `Latency`. DynamoDB: `ConsumedRead/WriteCapacityUnits`, `ThrottledRequests`, `ConditionalCheckFailedRequests` (your limiter's activity).

---

## Part 4. Cost: what costs money and what bounds it

### 4.1 The model

An idle platform costs almost nothing because everything is **pay-per-use** (on-demand DynamoDB, Lambda, API Gateway, CloudFront, S3 requests). Approximate drivers (**prices change: verify on the pricing pages**):

| Service | Pricing shape | Notes |
|---|---|---|
| **CloudFront** | per request + per GB out; an always-free tier of about 1 TB and 10 M requests per month (verify) | Student traffic is the main variable. PriceClass_200 trims expensive regions. |
| **CloudFront Functions** | about $0.10 per million invocations (first 2 M free; verify) | Every request runs the router. |
| **Lambda** | requests + GB-seconds; free tier 1 M requests + 400,000 GB-s per month | arm64 is cheaper |
| **API Gateway HTTP API** | about $1.00 per million requests | free tier for the first 12 months |
| **DynamoDB on-demand** | per million read/write units + GB-month; **PITR about $0.20 per GB-month** | tiny at this scale |
| **S3** | storage + requests | versioning keeps old copies 30 days |
| **Secrets Manager** | about $0.40 per secret per month + API calls | one secret |
| **CloudWatch** | alarms about $0.10 each per month (a free allowance); logs ingestion/storage | 7 alarms |
| **SNS email** | free for a few thousand notifications | |
| **ACM** | free | |
| **CloudFront multi-tenant (custom domains)** | per-tenant fee (**verify**) | The one to check before offering domains to many students |
| **AI** | OpenRouter/Google, outside AWS | bounded by per-student caps |

### 4.2 What bounds the bill

| Control | Bounds |
|---|---|
| Run allowance (5,000/month default) and in-flight caps | Lambda + API Gateway cost of student backends |
| Per-student AI dollar cap | AI spend |
| Storage caps (5 MB data, 50 MB files default) and operations cap | DynamoDB + S3 for storage |
| Throttles on the four APIs | Request storms |
| 30-day version expiry, 1-day upload expiry | S3 growth |
| Concurrency quota | Runaway Lambda cost |

### 4.3 What does NOT bound the bill (gaps to close)

1. **No AWS Budget resource in the template.** The template has *alarms* but no budget. Create one yourself (Billing -> **Budgets** -> *Create budget* -> cost budget, monthly, alert at 80% and 100% to your email).
2. **No Cost Anomaly Detection** (free): Billing -> **Cost Anomaly Detection** -> create a monitor for *AWS services* and an email subscription.
3. **CloudFront traffic is unbounded** (a popular or abused student site, or a bot, can generate data transfer): there is no WAF or per-site cap. The kill switch is **taking the site offline** from the teacher page.
4. **The `slug` tag on `sd-*` functions** lets you see cost per student, but you must **activate it as a cost allocation tag** (Billing -> *Cost allocation tags*) before Cost Explorer can group by it.

### 4.4 Where to look in the console

**Billing and Cost Management** -> **Cost Explorer**: group by *Service*, then by *Usage type*; filter to the last 30 days. **Bills** shows the current month per service. **Free tier** page shows usage against limits.

---

## Part 5. Quotas and hard limits that shaped the design

| Limit | Value | Design consequence |
|---|---|---|
| Lambda concurrent executions (account, per region) | default 1,000; **10 on a new account** | Gateway in-flight cap 3; no reserved concurrency; concurrency alarm at 8. **Request an increase** (Service Quotas) before a large class. |
| Lambda minimum unreserved concurrency | 100 | Reserved concurrency is impossible below this: so `BACKEND_RESERVED_CONCURRENCY=0` |
| API Gateway integration timeout | 30 s | Lambda timeouts 29 s; student functions 10-25 s |
| Lambda synchronous payload | 6 MB | Gateway limits bodies/responses to 1 MB |
| HTTP API payload | 10 MB | Uploads go straight to S3 by presigned URL |
| DynamoDB item size | 400 KB | Values capped at 64 KB |
| SSM standard parameter | 4 KB | The legacy keys map holds about 100 students |
| CloudFront Functions | about 1 ms compute, small code size | Routing logic must stay tiny |
| CloudFront distributions per account | 200 default (verify) | Not a concern here (2 + 1) |
| Environment variables per Lambda | 4 KB total | Student env var cap 20 variables / 4 KB |

**Console:** **Service Quotas** -> *AWS services* -> *AWS Lambda* -> **Concurrent executions** -> **Request increase at account level**. **CLI:** `aws service-quotas list-service-quotas --service-code lambda --query "Quotas[?QuotaName=='Concurrent executions']"`.

---

## Part 6. Resilience and recovery

| Failure | Impact | Recovery |
|---|---|---|
| A student overwrites/deletes their site files | One site | S3 **versioning**: restore the previous version (kept 30 days) |
| A site is taken offline by the sweeper | One site | Files are in `archive/`; extending the token restores it |
| Someone deletes table rows by mistake | Tokens/state | **DynamoDB PITR**: restore to a new table (35 days), then re-point or copy items |
| The stack is deleted accidentally | Everything except `Retain` resources | The data table is retained; the rest is rebuilt by `install`; sites need the S3 bucket (emptied by destroy: restore from an `export`) |
| Region outage (`us-east-1`) | Everything (single region) | `export` regularly; rebuild elsewhere with `install` + `import` (CloudFront certs live in us-east-1: a different region needs a different design) |
| Lambda concurrency exhausted by another app | Throttled platform | Quota increase; run the platform in its own account |

**Your portable backup is `export`** (tokens, stored data, files, teacher login). Test a restore (`install` + `import`) at least once; that is the only real proof.

---

## Part 7. Console steps (Mode B)

1. **CloudFormation** -> `student-hosting`:
   - **Events**: newest first. Learn to read a creation timeline (which resource took 12 minutes: the CloudFront distributions).
   - **Resources**: status and *physical ID* (the link icon jumps to the service console).
   - **Parameters**: what you chose. **Outputs**: URLs and IDs. **Template**: the processed template. **Change sets**: any saved plans. **Stack info**: termination protection, IAM role.
   - **Stack actions** -> **Detect drift**, then **View drift results**.
2. **CloudWatch** -> **Alarms** -> **All alarms**: state `OK` / `Insufficient data` / `In alarm`. Open one: the graph with the threshold line, **History**, **Actions** (the SNS topic).
3. **SNS** -> **Topics** -> `AlertsTopic` -> **Subscriptions**: **Status = Confirmed**? If *Pending confirmation*, no alert will ever email you.
4. **Billing** -> **Budgets**, **Cost Explorer**, **Cost Anomaly Detection** (create what is missing).
5. **Service Quotas** -> **AWS Lambda** -> *Concurrent executions*.

## Part 8. CLI verification and experiments

```bash
# CloudFormation  [READ]
aws cloudformation describe-stacks --stack-name $STACK --query 'Stacks[0].[StackStatus,Capabilities,EnableTerminationProtection]'
aws cloudformation describe-stack-events --stack-name $STACK --max-items 30 \
  --query 'StackEvents[].[Timestamp,LogicalResourceId,ResourceStatus]' --output table
aws cloudformation list-change-sets --stack-name $STACK --query 'Summaries[].[ChangeSetName,Status,CreationTime]'
aws cloudformation get-template --stack-name $STACK --template-stage Processed > /tmp/processed.json    # the SAM-expanded template

# Drift  (asynchronous: start, then read)  [READ-ONLY detection]
DRIFT=$(aws cloudformation detect-stack-drift --stack-name $STACK --query StackDriftDetectionId --output text)
aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id $DRIFT
aws cloudformation describe-stack-resource-drifts --stack-name $STACK --stack-resource-drift-status-filters MODIFIED DELETED

# Alarms and the topic  [READ]
aws cloudwatch describe-alarms --alarm-name-prefix $STACK --query 'MetricAlarms[].[AlarmName,StateValue,Threshold]' --output table
aws sns list-subscriptions-by-topic --topic-arn <AlertsTopicArn> --query 'Subscriptions[].[Protocol,SubscriptionArn]'   # 'PendingConfirmation' = not yet confirmed

# Logs  [READ]
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/$STACK --query 'logGroups[].[logGroupName,retentionInDays,storedBytes]' --output table
```

### Experiments

**X1. Read a real change set.** Run `npx -y "$PKG" plan --disable gemini --profile teacher-test` ([READ]: it creates a change set but changes nothing). Then:

```bash
aws cloudformation describe-change-set --stack-name $STACK --change-set-name <id from list-change-sets> \
  --query 'Changes[].ResourceChange.[Action,LogicalResourceId,ResourceType,Replacement]' --output table
```

Expect `Remove` for `AiProxyApi`/`AiProxyFunction` and `Modify` for functions whose environment changes. Learn to **say what each row means before you apply**. Do not apply unless you want Gemini off.

**X2. Test an alarm and the email path** (**CHANGES THINGS**: sends you an email): `aws cloudwatch set-alarm-state --alarm-name <stack>-portal-function-errors --state-value ALARM --state-reason "test"`. If the email does not arrive, check the SNS subscription status (it is almost always *Pending confirmation*). The alarm returns to OK on its own at the next evaluation.

**X3. Prove the loop-breaking.** In `template.yaml` find the `GatewayFunction` environment override (lines 506 to 511). Explain, with a pen, the cycle that would exist without it (Distribution -> BackendApi -> GatewayFunction -> Distribution).

**X4. Drift.** Run drift detection (above). Expect `IN_SYNC`. (To *see* drift in a test account: edit the `StudentSandboxBoundary` policy in the console, re-run drift, and observe `MODIFIED`: then undo.)

---

## Part 9. What you should now be able to explain

1. What a change set is, how your `plan`/`apply` map to it, and why the installer refuses data-holding replacements.
2. The difference between `ROLLBACK_COMPLETE` and `UPDATE_ROLLBACK_COMPLETE`, and which one you may `destroy`.
3. Which parts of the system are outside CloudFormation, and what that means for drift and destroy.
4. The seven alarms, what each one means, and why none of them covers cost.
5. Three controls that bound the bill and three gaps that do not.
6. Why a new AWS account's Lambda quota of 10 explains four separate design decisions.
7. How you would recover from: a deleted site file, a deleted table row, a deleted stack, a region outage.


---

## Update 2026-11-01 (2.1.0 / 2.1.1), read this before the text above

- F14 (destroy leaves student sd-* Lambda functions and log groups): FIXED. destroy finds the functions whose role is this platform sandbox role, deletes them and their log groups (--everything also the platform function log groups). Proven in tests; the live account was not destroyed.
- F1 (platform functions could write portal/): FIXED and proven on real AWS with the IAM simulator, one resource per call. Deploy: sites/ uploads/ archive/ read-write, userdata/ delete only. Sweeper: sites/ uploads/ archive/. No role can read or write portal/. No role can use the KMS signing key.
- Kit signing (KMS ECC_NIST_P256, about 1 dollar a month) is live: tampered, unsigned, older-serial and other-key kits are refused by pinned students with exit 3, and the student keeps working.
- Real-AWS findings: npm drops .gitignore from packages; a Lambda timeout is only a REPORT line with Status: timeout; a stale local install state could plan removals of a live platform (guard refused it); a site taken offline had no Bring online button. All fixed in 2.1.1.
- Still open: F2 (random origin secret), F3 (SSM String for secrets), signing the editor package and the portal pages, fresh install of 2.1.x on a clean account (blocked by CloudFront verification on the test account).
