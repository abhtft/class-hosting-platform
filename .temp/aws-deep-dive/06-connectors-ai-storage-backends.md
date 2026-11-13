# 06. The connectors: student backends, class AI, class storage

Goal: understand the three "optional features" as **security designs**, not just features: how untrusted code runs, how a powerful credential is delegated safely, how data is isolated between students on shared infrastructure, and how every one of them is capped so it cannot cost you money without limit.

---

## Part 1. The shared design pattern (learn this once)

Every connector in the platform follows the same six rules. If you add a seventh connector later, make it follow them.

| # | Rule | How the three connectors do it |
|---|---|---|
| 1 | **Off by default, per student.** | `enabled` flag per token, set only by the teacher. A student cannot switch anything on. |
| 2 | **Enforced on the server, on every call.** | The gateway, storage API and AI proxy each re-check the flag; a client cannot skip it. |
| 3 | **A hard cap, checked atomically.** | Run allowance, storage operations/bytes/items, AI dollars. Counters use conditional writes (no overshoot). |
| 4 | **The student gets a scoped credential, never the platform's.** | A function role with logs only; a per-student `cs_`/`ga_` key; a capped OpenRouter sub-key. |
| 5 | **The data key is derived from the credential, not from the request.** | Storage keys are built from the caller's own slug; the caller never supplies it. |
| 6 | **Everything is revocable and visible.** | Teacher toggles; the platform can revoke a sub-key; usage is shown on the teacher page. |

---

## Part 2. Student backends (untrusted code on your account)

### 2.1 The threat

A student uploads **arbitrary Python or JavaScript** and you run it inside **your** AWS account. Assume it is hostile or buggy. The design question is: *what is the worst it can do?*

### 2.2 The defences, in layers

1. **A dedicated, powerless identity.** The function runs as `StudentSandboxRole`: it can write log events and **nothing else** (permission boundary + role policy, see `02`). It cannot read S3/DynamoDB/SSM/Secrets, invoke other functions, or create anything.
2. **No inbound path except the gateway.** The function has no URL of its own (no Function URL, no API route). Only `GatewayFunction` has `lambda:InvokeFunction` on `sd-*`, and the gateway authenticates and counts every call first.
3. **Time and memory limits.** 256 MB and a **10 s** timeout (25 s with class AI): a runaway loop is killed by Lambda itself.
4. **Request/response limits** enforced by the gateway (1 MB each way).
5. **Run allowance** (default 5,000 per month) and **in-flight caps** (2 per student, 3 global): a student cannot consume your Lambda concurrency or your bill.
6. **Code limits at upload:** 5 MB zipped, 50 MB unzipped, 500 files, one entry file; env var rules; a name pattern `sd-<slug>`.
7. **Secrets hygiene:** the function's **error messages never reach the visitor** (they may contain the student's own secrets); they stay in the function's own log group.
8. **Kill switches:** the teacher toggle (checked on every request) and **reserved concurrency 0** set on the function itself (so even a direct invoke is refused, where AWS allows).

### 2.3 What the function **can** still do (be honest)

| Capability | Risk | Mitigation |
|---|---|---|
| **Outbound internet** (no VPC) | Call any public API; send out data it has (its own env vars); act as a small proxy; crypto-mining attempts | 10 s timeout, tiny memory, run allowance, in-flight caps: low value for abusers |
| **Read its own AWS credentials** (`AWS_ACCESS_KEY_ID`, etc. env vars) | Useless: the role allows only log writes to `sd-*` groups | Boundary; nothing to steal |
| **Write log lines to other students' log groups** | Log tampering (not reading) | Per-student roles would close it (see `09`) |
| **Call platform APIs with its own keys** | Only for its own slug | Keys scoped by slug |
| **Burn its own allowances / AI cap / storage quota** | Self-denial of service only | Per-student caps |

### 2.4 How a student function is created and updated (`backends.publish`)

1. Teacher enables the backend for the student (`enabled`, `runs_limit`, `runs_period`).
2. Student requests an upload URL (`POST /api/student/backend/upload-url`): a presigned PUT to `uploads/<slug>-backend.zip` (600 s).
3. Student publishes: the portal reads the zip, `inspect_zip` validates it and picks the runtime from the entry file (`app.py` -> `python3.12`, `index.mjs`/`index.js` -> `nodejs22.x`), then `lambda.create_function` (first time) or `update_function_code` + `update_function_configuration` with `Role=SANDBOX_ROLE_ARN`, tags, memory, timeout.
4. State machine: `creating` -> `ready` (the CLI polls `GET /api/student/backend` every 3 s). **Eventual consistency:** a just-created function is not instantly invocable, so `advance` calls `lambda:GetFunction` and only marks the backend `ready` when `State == Active` and `LastUpdateStatus` is `Successful` (or `failed` with Lambda's own reason).
5. Logs: the portal **creates the log group itself and sets retention to 7 days** (`LOG_RETENTION_DAYS = 7`; without that, Lambda's auto-created group would keep logs forever). `FilterLogEvents` reads it for the portal's "Show recent logs".

### 2.5 Environment variables (secrets for the student's code)

The student sets backend secrets in the portal (`PUT /api/student/backend/env`), stored as **the function's Lambda environment variables** (encrypted at rest by Lambda with the default KMS key `aws/lambda`; visible in the console to anyone with `lambda:GetFunctionConfiguration`). Rules: name `^[A-Za-z][A-Za-z0-9_]{0,63}$`; must not start with `AWS_`, `LAMBDA_`, `_`; `TZ` is reserved; at most 20 variables and 4 KB total; while a connector is on, **its variable names are reserved** (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AI_MODEL`, `CLASS_STORAGE_URL`, `CLASS_STORAGE_KEY`) so a student cannot overwrite or delete them.

---

## Part 3. Class AI

### 3.1 Two providers, two designs

| | OpenRouter | Gemini (Google) |
|---|---|---|
| Provider supports per-user keys? | **Yes** (management key creates sub-keys with spending limits) | **No** |
| What the student function gets | `OPENAI_API_KEY` = its **own capped sub-key** (`sk-or-...`), `OPENAI_BASE_URL` = OpenRouter, `AI_MODEL` | `OPENAI_API_KEY` = `ga_<slug>.<secret>`, `OPENAI_BASE_URL` = your **AiProxyApi** `/v1`, `AI_MODEL` |
| Who enforces the cap? | **OpenRouter** (limit + reset period on the key) | **Your proxy** (counter in DynamoDB; cost of each answer added) |
| Where is the platform's real key? | OpenRouter **management key** in Secrets Manager; used only to create/update/revoke sub-keys | The Google API key in the **same secret**; read by the **AiProxyFunction only** at call time |
| Student can pick another model? | The model env var is the teacher's choice (a student could call other models allowed for the sub-key) | **No**: the proxy forces the model the teacher set and caps output tokens |

Both speak the **OpenAI-compatible API**, so the student's code is identical (`class_ai.py` / `class_ai.mjs` just call `OPENAI_BASE_URL`).

### 3.2 Why the management key never goes near students

The management key can **mint keys** but **cannot call models**. If it leaked, an attacker could create keys on *your* OpenRouter account (and you would revoke them), but it is never in a student function, never returned by an API, never logged, and read only by three roles (portal, sweeper, AI proxy).

### 3.3 The cost envelope (compute it)

Worst case spend for N students = `N x per-student cap` (default **$0.25 per month** per student) + the Lambda/API costs of their calls (bounded by the run allowance). With 30 students and the default cap, the AI worst case is **$7.50 per month**, and OpenRouter will refuse beyond each key's limit. That bound is the point of delegation.

### 3.4 Observability

- Teacher page: per-student `used_usd`, `remaining_usd` (read from OpenRouter on demand, throttled to protect the management key's rate limit; the proxy keeps its own counter for Gemini).
- CloudWatch: `AiProxyFunction` logs; `AiProxyApi` 4xx/5xx metrics.

---

## Part 4. Class storage

### 4.1 What it offers

A small **key-value database** (`StudentDataTable`) and a **file area** (S3 `userdata/<slug>/`) with hard caps:

| Cap | Default | Max |
|---|---|---|
| Data (all records) | 5 MB | 100 MB |
| Files total | 50 MB | 500 MB |
| Value size | 64 KB | fixed |
| Items | 5,000 | fixed |
| File size | 5 MB | fixed |
| Files | 100 | fixed |
| Collections ever used | 50 | fixed |
| Operations per period | 10,000 per **day** | 1,000,000 |

### 4.2 Isolation by construction (the key idea)

Three independent mechanisms stop student A reading student B's data:

1. **The key names the slug and is checked** (`storage.authenticate`): `cs_<slug>.<secret>`; compare in constant time with the value stored for that slug.
2. **The data key is built from the authenticated slug**: the partition key is `<slug>#<collection>`; the caller supplies only the collection and id, never the slug. There is **no code path** that reads a key the caller composed.
3. **IAM backs the code:** `StorageFunction` may *create or delete* `TokensTable` items only if their partition key starts with `scoll#` (the `dynamodb:LeadingKeys` condition), and may touch S3 only under `userdata/`. A bug in the storage code still cannot forge a token item or touch a site.

### 4.3 Files: presigned POST and GET

- **Upload:** `POST /v1/files/upload-url` -> `generate_presigned_post` with **conditions**: `Content-Type` equals the declared type and `content-length-range = [1, size]`, valid for **300 s**. S3 enforces the size **before** accepting bytes (unlike a presigned PUT).
- **Download:** `GET /v1/files/<name>` -> a presigned GET link valid **300 s** (suitable for `<img src>`).
- **File names are validated** (`^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`: no slashes, so no path tricks), and the folder is fixed to `userdata/<slug>/`.
- **Accounting without drift:** file counts and sizes are computed by **listing the folder**, not by a counter that could go wrong ("counted by listing, so nothing can drift").

### 4.4 Cost and growth

Mostly DynamoDB on-demand and S3: the hard caps bound it per student. The table is `Retain`: **clear a student's storage from the teacher page** when a class ends (otherwise the data stays).

---

## Part 5. Console steps (Mode B)

1. **Lambda** -> **Functions** -> filter `sd-`: one per enabled student. Open one:
   - **Configuration** -> **Permissions**: role `StudentSandboxRole`.
   - **Configuration** -> **General**: memory 256 MB, timeout 10 s, runtime.
   - **Configuration** -> **Environment variables**: *the keys are secrets: do not capture them.* Note which names exist.
   - **Tags**: `managed-by`, `slug`.
   - **Concurrency**: *Reserved concurrency* = 0 means **OFF** (if the teacher switched the backend off and AWS allowed it).
2. **CloudWatch** -> **Log groups** -> `/aws/lambda/sd-<slug>`: the student's own logs; *Retention* should read **1 week** (set by the portal).
3. **IAM** -> **Roles** -> `StudentSandboxRole`: *Permissions boundary* present.
4. **API Gateway** -> the Storage and AI-proxy APIs: routes `ANY /{proxy+}`, throttle settings.
5. **S3** -> bucket -> `userdata/`: one folder per student using storage.
6. **DynamoDB** -> `...StudentDataTable...` -> explore items: partition key `<slug>#<collection>`.

## Part 6. CLI verification and experiments

```bash
# Student functions and their roles  [READ]
aws lambda list-functions --query "Functions[?starts_with(FunctionName,'sd-')].[FunctionName,Runtime,MemorySize,Timeout,Role]" --output table
aws lambda get-function-concurrency --function-name sd-<slug>                        # {} = unreserved; 0 = switched off
aws lambda get-function --function-name sd-<slug> --query 'Tags'
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/sd- --query 'logGroups[].[logGroupName,retentionInDays]' --output table
```

### E1. The sandbox probe (the most instructive lab: **CHANGES THINGS**, test platform, a throwaway student)

Create a throwaway student with **Backend on**, and deploy this `backend/app.py`. It tries things a hostile student would try and reports which AWS calls are refused. **It never prints credentials.**

```python
import json, os, urllib.request

def handler(event, context):
    import boto3
    from botocore.exceptions import ClientError
    results = {}
    tries = {
        "sts.get_caller_identity": lambda: boto3.client("sts").get_caller_identity()["Arn"],
        "s3.list_buckets":         lambda: boto3.client("s3").list_buckets(),
        "dynamodb.list_tables":    lambda: boto3.client("dynamodb").list_tables(),
        "ssm.describe_parameters": lambda: boto3.client("ssm").describe_parameters(MaxResults=1),
        "secretsmanager.list":     lambda: boto3.client("secretsmanager").list_secrets(MaxResults=1),
        "lambda.list_functions":   lambda: boto3.client("lambda").list_functions(MaxItems=1),
        "iam.list_roles":          lambda: boto3.client("iam").list_roles(MaxItems=1),
    }
    for name, fn in tries.items():
        try:
            out = fn()
            results[name] = "ALLOWED" if out else "ok"
        except ClientError as e:
            results[name] = e.response["Error"]["Code"]
        except Exception as e:
            results[name] = type(e).__name__
    try:
        with urllib.request.urlopen("https://example.com", timeout=3) as r:
            results["outbound_internet"] = r.status
    except Exception as e:
        results["outbound_internet"] = type(e).__name__
    # names of env vars only, never values
    results["env_var_names"] = sorted(k for k in os.environ if not k.startswith(("AWS_", "LAMBDA_", "_")))
    return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(results)}
```

**Expected:** `sts.get_caller_identity` **works** (it needs no permission: it just shows the role session ARN, which proves the identity is `StudentSandboxRole`); `s3`, `dynamodb`, `ssm`, `secretsmanager`, `lambda`, `iam` all return **`AccessDenied`** (or `AccessDeniedException`); `outbound_internet` is **200** (no VPC); `env_var_names` shows only the student's own and the connector names. **This is the practical proof of the whole sandbox.** Delete the throwaway student's backend afterwards.

### E2. Storage isolation

With two throwaway students A and B (storage on): from A's backend `storage.put("notes","x",{"a":1})`; from B's backend `storage.get("notes","x")` -> `not_found`. Then try a call to the Storage API with a malformed key from your terminal:

```bash
curl -i -H "authorization: Bearer cs_alice.wrongsecretwrongsecret12" "$(aws cloudformation describe-stacks --stack-name $STACK \
   --query "Stacks[0].Outputs[?OutputKey=='StorageApiUrl'].OutputValue" --output text)/v1/data/notes/x"
# EXPECT 401 {"error": "bad_key", ...}
```

### E3. The cap in action

Set a throwaway student's backend `runs_limit` to **3** (teacher page -> Backend card), then call their `/api/health` five times:

```bash
for i in 1 2 3 4 5; do curl -s -o /dev/null -w "%{http_code} " https://<slug>.<your-domain>/api/health; done; echo
# EXPECT 200 200 200 429 429   (the 4th call: allowance_exhausted)
aws dynamodb scan --table-name $T --filter-expression "begins_with(pk, :p)" --expression-attribute-values '{":p":{"S":"runs#"}}' --output table
```

---

## Part 7. What you should now be able to explain

1. Walk through the layers that stop a hostile student function from reading S3. (No permission on the role; boundary ceiling; no network path to your data except via keys scoped to its slug.)
2. What can a student function do that you *cannot* prevent, and why is that acceptable? (Outbound internet and log tampering; bounded by tiny resources, allowances and no read access.)
3. Why does the Gemini design need a proxy but OpenRouter does not? (Per-user key support at the provider.)
4. What is the worst-case monthly AI bill for 30 students at the default cap? ($7.50.)
5. What are the three mechanisms that isolate storage between students? (Authenticated slug; slug-derived keys; IAM LeadingKeys/prefix conditions.)
6. Why a presigned POST for storage files but a presigned PUT for site zips, and what is the trade-off? (POST can enforce size and type before accepting; PUT is simpler but checks size after.)
7. Where do you look when a student says "my backend returns 429"? (`runs#<slug>#<period>` counter vs `runs_limit`; `inflight#...`; `ip#...`; the response body's `error` code says which.)
