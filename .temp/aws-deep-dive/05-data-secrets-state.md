# 05. Data, secrets and state: DynamoDB, SSM, Secrets Manager, passwords, sessions

Goal: understand where every piece of state lives, why it lives there, how it is protected, and the cryptography behind logins, sessions and tokens, well enough to defend each choice and to know its limits.

---

## Part 1. DynamoDB

### 1.1 Theory you need

- A **table** holds **items** (rows, up to **400 KB** each). Every item has a **primary key**: a **partition key** (hash), optionally plus a **sort key** (range). `TokensTable` uses a partition key only (`pk`); `StudentDataTable` uses `s` (partition) + `id` (sort).
- DynamoDB spreads items across **partitions** by hashing the partition key. Each partition handles about **1,000 writes/s and 3,000 reads/s**. A **hot key** (one item hammered constantly) is the classic scaling trap. Your hottest item is `inflight#global` (every backend call updates it), which is fine at classroom scale (a few writes per second) but would not be at thousands.
- **Capacity modes:** yours is **on-demand** (`PAY_PER_REQUEST`): no capacity planning, you pay per read/write request unit, and it scales automatically (a *new* table starts at roughly 4,000 writes/s and 12,000 reads/s of burst headroom; **verify** the numbers). An idle on-demand table costs only storage: that is why the platform's idle cost is near zero.
  - 1 **write request unit** = one write of up to 1 KB. 1 **read request unit** = one strongly consistent read up to 4 KB (an eventually consistent read costs half).
  - Approximate cost (us-east-1, **verify**): about $1.25 per million writes, $0.25 per million reads, $0.25 per GB-month.
- **Consistency:** `get_item` is **eventually consistent by default** (a read right after a write may briefly return the old value, normally well under a second). Your `DynamoStore.get` does not set `ConsistentRead=True`, so reads are eventually consistent: acceptable here (the effect is a sub-second window after, for example, revoking a token). **Writes** (`update_item`, `put_item`) are always applied strongly consistently at the item's leader: that is why the counters are correct.
- **Conditional writes and update expressions** let you do "check and change" atomically in one request (see `04`, 2.4). This replaces locks.
- **TTL (time to live):** you name an attribute (here `ttl`, an epoch-seconds number); DynamoDB deletes expired items **in the background, typically within minutes to 48 hours** after expiry, free of charge. An expired item **can still be read until it is actually deleted**, so TTL is a **cleanup mechanism, never a correctness mechanism**. Your code gets correctness from the **key** (counters include the period in the key: `runs#<slug>#<period>`), and uses TTL only to discard old counters.
- **PITR (point-in-time recovery):** continuous backup; restore to any second in the last **35 days** into a **new table**. Enabled on `TokensTable` (template.yaml:277). It also contains whatever secrets are in the items (see 1.3).
- **Encryption at rest:** always on. Default key type is **AWS owned key** (no cost); you can choose *AWS managed key* (`aws/dynamodb`) or a customer managed KMS key for audit/control. **Verify** in *Additional settings*.
- **Scan** reads the *whole table* (and you pay for every item read, then filter): fine for hundreds of tokens, wrong for millions. `DeployFunction` and `SweeperFunction` use `Scan` with `begins_with(pk, 'tok#')` to list tokens and find expired ones. **Scaling limit to remember.**
- **Retention:** `StudentDataTable` has `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain`: deleting or replacing the stack **keeps the data table** (the installer's `destroy` keeps it unless `--everything`). That is the "never lose student data by accident" rule written into CloudFormation.

### 1.2 What is in `TokensTable` (single-table design)

Every row has `pk`. The prefix says what the row is.

| `pk` | Fields (selection) | Meaning |
|---|---|---|
| `tok#<sha256(token)>` | `id` (the hash), `slug`, `hint` (last 4 chars), `student_name`, `created_at`, `expires_at`, `revoked`, `archived`, `regen` (state), `last_deploy_at`, `domain`, `domain_state`, `tenant_id`, `domain_enabled`; nested maps `backend{...}`, `ai{...}`, `storage{...}` | One student. **The token itself is never stored, only its hash.** |
| `slug#<slug>` | `token` (the id) | A **lock**: a name can map to only one token. Created with a conditional put (`if_absent`), so two students can never get the same site name |
| `domain#<domain>` | `token` | Same lock for custom domains |
| `fail#<ip>` | `count`, `first_at`, `locked_until`, `ttl` | Failed teacher logins per IP |
| `ip#...`, `inflight#...`, `runs#...`, `sops#...` | `count`, `updated_at`, `ttl` | Counters (see `04`) |
| `scoll#...`, `sbytes#...` | collection names / byte usage | Class storage bookkeeping |

### 1.3 Sensitive data that **does** live in the table (know this)

| Where | What | Plaintext? |
|---|---|---|
| `tok#...` -> `ai.key` | The student's **OpenRouter key** (`sk-or-...`), created by the platform and shown to the student function | Yes (it is capped and revocable at OpenRouter) |
| `tok#...` -> `ai.gkey` | `ga_<slug>.<secret>`, the student's Gemini-proxy key | Yes |
| `tok#...` -> `storage.key` | `cs_<slug>.<secret>`, the student's class-storage key | Yes |

These must exist in a usable form because the platform has to **hand them to the student's function** (as environment variables) and **compare incoming keys to them**. Contrast with student *tokens* and the teacher password, which are stored only as hashes. The consequence: **anyone with `dynamodb:GetItem/Scan` on this table can read every student's keys** (DeployFunction, Sweeper, Gateway, Storage and AiProxy roles, and you). They are protected by IAM and encryption at rest, and by being capped/scoped per student. A hardening idea is to store hashes and show the key once, at the cost of a more complex hand-off (the key must be injected into the function at creation).

---

## Part 2. SSM Parameter Store and Secrets Manager

### 2.1 Theory: which one for what

| | SSM Parameter Store | Secrets Manager |
|---|---|---|
| Purpose | Configuration and small secrets | Secrets with lifecycle (rotation, versions) |
| Types | `String`, `StringList`, `SecureString` (KMS-encrypted) | always encrypted |
| Size | 4 KB (standard), 8 KB (advanced) | 64 KB |
| Cost | Standard tier free | about $0.40 per secret per month + about $0.05 per 10,000 API calls (**verify**) |
| Rotation | no built-in | built-in (Lambda-based) |
| Versions | history kept (up to 100 versions) | version stages `AWSCURRENT`, `AWSPREVIOUS` |
| In CloudFormation | can create `String`; **cannot create `SecureString`** | can create and auto-generate secrets |

Your platform uses **both**:

- **SSM `String`** for `TeacherAccount` (`/student-deploy/teacher`: username, salt, scrypt hash, session-signing secret), `StudentKeys` (`/student-deploy/keys`: the legacy deploy-key map; one 4 KB value holds about 100 students), and the alert email (`{{resolve:ssm:...}}` dynamic reference: the address never appears in the repo).
- **Secrets Manager** for the AI provider keys (`openrouter` -> `{"OPENROUTER_MANAGEMENT_KEY": "...", "GOOGLE_API_KEY": "..."}`), created by the installer's `secrets` command, **not** by the template (so no secret value ever passes through CloudFormation).

**Things to notice**

1. The teacher hash and the **session-signing secret** sit in an SSM **`String`** parameter (the template can only create `String`). They are protected by IAM (only `DeployFunction` may `GetParameter` on the exact ARN) but not by a KMS key policy. A `SecureString` created out-of-band would add KMS control and audit. (See `09`.)
2. **Parameter history:** every overwrite creates a **new version** and keeps the old ones. After `teacher-setup`, previous password **hashes** remain readable in the parameter's history by anyone with `ssm:GetParameterHistory`. Fine for a single-owner account; worth knowing.
3. **Why two separate stores for two kinds of secrets:** the teacher login is *derived* material you can regenerate; the AI keys are *delegated* credentials with money attached, so they get the better store and a narrower reader list (only the portal, sweeper and AI proxy).
4. **Caching:** `portal.teacher_config` caches the parameter for **60 s**; `openrouter.management_key` caches the secret for **300 s** (`KEY_CACHE_SECONDS`), to avoid a Secrets Manager call (and its cost and latency) on every request. Rotating the secret therefore takes up to 5 minutes to take effect everywhere.

### 2.2 The OpenRouter delegation pattern (you will meet it everywhere)

The platform holds **one powerful key** (the *management* key) that can **create and revoke sub-keys but cannot call models**. For each student it creates a **capped, revocable** sub-key (spending limit per day/month/total). The student's function gets only the sub-key. Properties: **blast radius = that student's cap**; revocation is instant at the provider; the powerful key never leaves the portal/sweeper/AI-proxy roles. This is the same idea as STS sessions, scoped tokens and delegated OAuth.

---

## Part 3. Authentication: the cryptography, one primitive at a time

### 3.1 Teacher password: scrypt

`scrypt_hash(password, salt)` = `hashlib.scrypt(n=16384, r=8, p=1, dklen=32)`.

- **Why not SHA-256?** Fast hashes let an attacker try billions of guesses per second. A **key derivation function (KDF)** like scrypt is deliberately slow and **memory-hard** (needs about `128 * N * r` bytes = 16 MB per attempt), which makes GPU/ASIC cracking expensive. (bcrypt and Argon2 are the other well-known choices.)
- **Salt:** random per password (stored in SSM next to the hash). It defeats precomputed tables and makes identical passwords hash differently.
- **Constant-time comparison:** `hmac.compare_digest` does not stop at the first differing byte, so timing does not leak how many bytes matched.
- **Equalising time for unknown usernames:** the code *always computes the scrypt hash* even when the username is wrong, then combines both comparisons with a single `&`, so an attacker cannot learn which usernames exist from response time.
- **Online guessing:** 5 failures from one IP -> locked for 15 minutes (`MAX_FAILED_LOGINS`, `LOCKOUT_SECONDS`), recorded in `fail#<ip>` with a 30-minute TTL. This relies on the **real client IP** (from `x-viewer-ip`, trusted only with the origin secret). An attacker rotating IPs is bounded only by the API throttle (20 rps) and the scrypt CPU cost: **use a long random teacher password.**

### 3.2 Sessions: HMAC-signed bearer tokens

`sign_session(role, subject, ttl, now)`:

```
payload  = base64url( {"r": role, "s": subject, "e": expiry_epoch} )
signature= base64url( HMAC-SHA256( signing_key(role), payload ) )
session  = payload + "." + signature
signing_key(role) = HMAC-SHA256( teacher_secret, role + "-session" )      # a different key per role
```

- **HMAC** proves *integrity and origin*: only someone holding the key can produce a valid signature, so nobody can edit the payload (change the role, the subject or the expiry) without detection.
- **Not encrypted:** the payload is only base64-encoded; anyone can read it (it contains the role, a subject id and the expiry, nothing sensitive).
- **Bearer:** whoever holds the session can use it. It lives in the browser's **`sessionStorage`** (`web/common.js`), which is **per-origin** and cleared when the tab closes. A student's published site runs on a *different origin* (`alice.apps.example.com`), so it **cannot** read the portal's storage (`deploy.apps.example.com`): that origin separation is a real security property.
- **Lifetimes:** teacher **8 h**, student **12 h** (`TEACHER_SESSION_SECONDS`, `STUDENT_SESSION_SECONDS`).
- **No server-side revocation list:** a session is valid until it expires, **unless** the underlying thing it points to disappears: `student_token` re-reads the token item on every call and rejects `revoked`/missing tokens, so **revoking a token takes effect immediately**. Invalidating *all* sessions means changing the teacher secret (a re-run of `teacher-setup`, which regenerates the signing secret).
- **Verification** (`verify_session`): recompute the HMAC, compare in constant time, check the role matches (a student session can never be used as a teacher session because the keys differ), check expiry.

### 3.3 Student tokens: why SHA-256 is enough here

A student token is `mds_` + `secrets.token_urlsafe(16)`: **128 bits of randomness**. Only `sha256(token)` is stored (`tok#<hash>`) plus the last 4 characters as a **hint** for the teacher's list. Why a plain fast hash is acceptable (unlike the password): brute-forcing a 128-bit random value is infeasible, so a slow KDF adds nothing; the hash only protects against **database disclosure** (a leaked table does not reveal usable tokens). Lookup is by the hash, so there is no timing leak to worry about (a miss is a miss).

### 3.4 Per-student API keys (`cs_`, `ga_`)

Format `cs_<slug>.<secret>`: the slug prefix **names the student** so the server can look up the right item, then the whole key is compared in constant time with the stored value. 24 random bytes of secret (`token_urlsafe(24)`). Stored in plaintext (see 1.3) because the platform must inject it into the student's function.

### 3.5 Token lifecycle (a state machine)

```
            expires_at                 expires_at + 3 days (OFFLINE_GRACE_DAYS)
   active ----------------> expired ----------------------------> offline (archived)
     |  (deploy allowed)     (read-only: site stays up, deploy refused,      (site moved to archive/,
     |                        student may request regeneration)               never deleted)
     |
     +--> revoked  (checked first: any request with it is refused, and its sessions stop working at once)
```

`status_of(item, now)` computes the state from time and two flags every time: **the state is never stored**, so it cannot go out of sync. Precedence: `revoked` > `offline` (archived flag) > `active` > `expired` (within grace) > `offline`. Extending or approving regeneration of an offline site restores it from `archive/` first (`reactivate`).

---

## Part 4. Console steps (Mode B)

1. **DynamoDB** -> **Tables** -> `...TokensTable...`:
   - **Overview / Additional settings**: Capacity mode *On-demand*, **TTL** (attribute `ttl`, enabled), **Point-in-time recovery** (On), **Encryption** (which key type).
   - **Explore table items**: filter (Scan): attribute `pk`, `begins with`, `tok#` -> a few students; `slug#` -> locks; `ip#` -> live counters. **Do not copy items that contain `ai`/`storage` keys.**
   - **Monitor**: consumed read/write units, throttled requests (should be 0).
2. **Systems Manager** -> **Parameter Store** -> `/student-deploy/teacher`: **Overview** (type *String*, tier), **History** tab (versions). Do not click *Show decrypted value* in a shared screen.
3. **Secrets Manager** -> `openrouter`: **Overview** (encryption key, last changed), **Versions**. *Do not click* **Retrieve secret value**.
4. **CloudWatch** -> **Metrics** -> *DynamoDB* -> *Table Metrics* -> `ConsumedWriteCapacityUnits`, `ThrottledRequests`, `ConditionalCheckFailedRequests` (this last one counts the limit hits of your counters!).

---

## Part 5. CLI verification

```bash
T=$(aws cloudformation describe-stacks --stack-name $STACK --query "Stacks[0].Outputs[?OutputKey=='TokensTableName'].OutputValue" --output text)

# Table posture  [READ]
aws dynamodb describe-table --table-name $T --query 'Table.[BillingModeSummary.BillingMode,KeySchema,TableSizeBytes,ItemCount,SSEDescription]'
aws dynamodb describe-time-to-live --table-name $T
aws dynamodb describe-continuous-backups --table-name $T --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription'

# Look at students WITHOUT touching secrets (projection of safe attributes only)  [READ]
aws dynamodb scan --table-name $T --filter-expression "begins_with(pk, :p)" \
  --expression-attribute-values '{":p":{"S":"tok#"}}' \
  --projection-expression "slug, expires_at, revoked, archived" --output table

# A live counter: how many backend calls are in flight right now?  [READ]
aws dynamodb get-item --table-name $T --key '{"pk":{"S":"inflight#global"}}' --consistent-read

# Parameters and secrets: METADATA ONLY (never print values)  [READ]
aws ssm describe-parameters --parameter-filters "Key=Name,Option=BeginsWith,Values=/student-deploy" \
  --query 'Parameters[].[Name,Type,Tier,Version,LastModifiedDate]' --output table
aws secretsmanager describe-secret --secret-id openrouter --query '[Name,KmsKeyId,LastChangedDate,DeletedDate]'
```

**Expect:** on-demand billing, `ttl` enabled, PITR `ENABLED`, `SSEDescription` absent or `ENABLED` (owned key shows no description), parameters of type `String`, a secret using the default key.

### 5.1 Experiments

**E1. A tampered session is rejected.** Sign in as a student; in the browser developer tools open **Application -> Session Storage** and copy the session (it looks like `eyJ...xyz.AbCd...`). Change one character in the first part (before the dot) and send it:

```bash
curl -i -H "authorization: Bearer <tampered session>" https://deploy.<your-domain>/api/student/me
# EXPECT 401 {"error": "Please sign in again."}  (the signature no longer matches the payload)
curl -i -H "authorization: Bearer <the real session>" https://deploy.<your-domain>/api/student/me      # EXPECT 200
```

**E2. A student session cannot be used as a teacher session** (different signing keys):

```bash
curl -i -H "authorization: Bearer <the real STUDENT session>" https://deploy.<your-domain>/api/admin/tokens   # EXPECT 401
```

**E3. Lockout** (**CHANGES THINGS**, test platform only, you will be locked out of the teacher page for 15 minutes from this IP): send 6 wrong logins:

```bash
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w "%{http_code} " -X POST -H "content-type: application/json" \
  -d '{"username":"x","password":"wrong"}' https://deploy.<your-domain>/api/teacher/login; done; echo
# EXPECT 401 401 401 401 401 429   then look for the item:  aws dynamodb get-item --table-name $T --key '{"pk":{"S":"fail#<your ip>"}}'
```

**E4. A name lock:** try to create two tokens with the same site name from the teacher page: the second is refused (`409 The name 'x' is already taken`), because `slug#x` already exists and the write is conditional.

**E5. Token revocation is immediate:** sign in as a student, then revoke the token on the teacher page, then reload the student page: the **existing session** stops working (`401 This token is no longer valid`), because every call re-reads the token item.

---

## Part 6. What you should now be able to explain

1. Why are student tokens stored as SHA-256 but the teacher password as scrypt? (Entropy: 128 random bits vs a human-chosen secret.)
2. What does the HMAC signature in a session prove, and what does it **not** do? (Integrity/origin; not confidentiality; not revocable by itself.)
3. How can a revoked token's session stop working instantly with no revocation list? (The token item is re-read and checked on every request.)
4. What are three kinds of secret in DynamoDB, and why can they not be hashed? (The `ai.key`, `ai.gkey`, `storage.key`: the platform must give them to the function and compare incoming ones.)
5. Why is TTL not used for correctness? (Deletion can lag by hours; correctness comes from the key including the period.)
6. Why is `StudentDataTable` `Retain` but `TokensTable` not? (Student-created data is irreplaceable; tokens can be re-issued; PITR protects tokens anyway.)
7. Why is the teacher hash in SSM `String` and the AI keys in Secrets Manager? (CloudFormation cannot create SecureString; AI keys need a separate, narrower, better-managed store; and they are created out-of-band so values never touch CloudFormation.)
8. What limits scaling in the data layer? (`Scan` of the tokens table; the single hot `inflight#global` item; fixed-window counters.)
