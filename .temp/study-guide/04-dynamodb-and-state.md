# Chapter 4: DynamoDB and the platform's state

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> The platform has **three tables**: `TokensTable` (key `pk`; almost everything), `StudentDataTable` (hash `s`, range `id`; students' stored data) and `TracesTable` (hash `s`, range `id`, with two secondary indexes `by-app` and `by-day`).

## A. The basics

**Q107 [B] What is DynamoDB and when is it the right tool?**
A managed key-value/document database with single-digit-millisecond reads at any scale, pay-per-request pricing, and no servers. Right when you know your access patterns in advance (get this item, list this partition). Wrong for ad-hoc queries, joins and reporting.
Study: [DynamoDB developer guide](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Introduction.html)

**Q108 [B] What are a partition key and a sort key?**
The partition (hash) key decides where an item lives; the sort (range) key orders items inside a partition. `TokensTable` has only a partition key `pk` (a string). `StudentDataTable` uses partition `s` = `<slug>#<collection>` and sort `id`, so "all notes of student alice" is one efficient `Query`.
Repo: `template.yaml` tables; `lambda/storage.py` docstring.

**Q109 [I] What does `BillingMode: PAY_PER_REQUEST` mean and why choose it?**
On-demand capacity: you pay per read/write, nothing when idle, no capacity planning. For an unpredictable class workload it's the cheapest and safest default.

**Q110 [I] What are RCU/WCU and eventual vs strong consistent reads?**
Read/write capacity units are the pricing/throughput measure. An eventually consistent read may miss a just-completed write (half the cost); a strongly consistent read always sees it. The boto3 `get_item` here uses the default (eventual) except where code reads right after writing in the same item path; know which one you are using.
Study: [Read consistency](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.ReadConsistency.html)

**Q111 [B] What is TTL in DynamoDB?**
A number attribute (epoch seconds) named in the table settings. Items past that time are deleted for free, typically within hours (up to ~48 h). Used for counters, jobs, traces, stale cache entries.
Study: [TTL](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html)

**Q112 [I] What is point-in-time recovery (PITR)?**
Continuous backups that let you restore the table to any second in the last 35 days into a new table. It's enabled on the tokens and student-data tables. It is *not* free (priced per GB-month) and does not cover S3 books or traces.
Study: [PITR](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Point-in-time-recovery.html)

**Q113 [I] Why do two tables have `DeletionPolicy: Retain`?**
CloudFormation would otherwise delete the table (and its data) when the stack is deleted or the resource is replaced. `Retain` leaves the table behind. `destroy` lists retained resources and only deletes them with `--everything`.
Study: [DeletionPolicy attribute](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-attribute-deletionpolicy.html)

**Q114 [I] What is a Global/Local secondary index and where is one used?**
A secondary index gives a different key schema to query by. `TracesTable` has `by-app` (partition `app`, sort `started`) so "latest traces of one app" is a query, and `by-day` for daily rollups. Only the trace *summary* item carries the index attributes, so the many small span items don't pay for index writes.
Repo: `lambda/traces.py` docstring.

## B. Modelling patterns used in this project

**Q115 [I] What is "single-table design" and is this project doing it fully?**
Putting many entity types in one table with key prefixes so related items can be fetched together. The tokens table does this loosely: prefixed keys (`tok#`, `slug#`, `ip#`...), but each entity is a single-item lookup. It doesn't use composite sort keys the way full single-table design does.
Study: Alex DeBrie, [The What, Why, and When of Single-Table Design](https://www.alexdebrie.com/posts/dynamodb-single-table/) and his book [The DynamoDB Book](https://www.dynamodbbook.com/).

**Q116 [I] What is the cost of `list_prefix` (a Scan with a filter) in `DynamoStore`?**
A Scan reads every item and filters afterwards, so cost grows with the table. It's acceptable for small classes (a few hundred tokens) but not for big tables; hence the jobs docs say "nothing is ever scanned" and keep a `joblist#<slug>` item instead.
Repo: `lambda/portal.py` `list_prefix`; `docs/BACKGROUND_JOBS.md` section 7.

**Q117 [I] What is "optimistic concurrency" and where is it used?**
Instead of locking, you write only if the item hasn't changed since you read it (`ConditionExpression "#f = :e"`). `replace_if(item, field, expected)` does this. Project sync uses it so two people can't silently overwrite each other's commit: the second one gets a 409 conflict.
Study: [Optimistic locking with version number](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/DynamoDBMapper.OptimisticLocking.html)

**Q118 [I] What is an atomic counter and why is it better than read-modify-write?**
`ADD count :1` is performed by DynamoDB on its own copy; two concurrent callers can't both read 5 and write 6. Combined with a ConditionExpression it is also a lock-free limiter (limit checks).
Repo: `lambda/portal.py` `counter_add`.

**Q119 [D] Why does `counter_add` take `limit - delta` as `:max`?**
The condition is `count <= :max` *before* the add. For a limit of 10 and a delta of 1, the add is allowed only while the current count is ≤ 9, so the final count never exceeds 10.

**Q120 [I] Why does the token record store only the SHA-256 of the token?**
If the database leaked, an attacker would see hashes, not usable tokens. The token itself has 128 random bits so hashing with plain SHA-256 is safe (it's not a low-entropy password; no salt/slow hash needed). The record's `id` is the hash; `hint` is the last 4 characters for humans.
Repo: `lambda/portal.py` `token_hash`.

**Q121 [I] Why use a slow hash (scrypt) for the teacher's *password* but fast SHA-256 for *tokens*?**
Passwords have low entropy, so attackers brute-force them; a slow, memory-hard hash makes each guess costly. Random 128-bit tokens can't be guessed, so a fast hash is enough.
Study: [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)

**Q122 [I] What is a "lock item" pattern?**
Write an item (`slug#alice`, `domain#shop.com`) with `attribute_not_exists(pk)` to claim a name. The first writer wins. Used for slugs, domains, platform pool creation (`authpool#platform`).

**Q123 [D] How does `auth.py` make pool creation resumable?**
It saves a flag after each external step (`pool_id`, `domain_made`, `google_made`). If step 2 fails, the next call skips step 1 and continues. This "record each step, then re-check" is the lightweight version of a workflow engine.
Repo: `lambda/auth.py` `platform_pool`.

**Q124 [I] How do items grow and what's DynamoDB's item limit?**
400 KB per item. A single growing item (e.g. one big trace) risks hitting it and is rewritten whole on each update. That's why traces are one small item per step with a separate summary, and why the settings map on a token stays small.

**Q125 [I] Why do traces use one item per step?**
Cheap appends (no rewrite), each item expires on its own TTL, and the index-bearing summary stays tiny. See `docs/TRACE_SCHEMA.md` section 11.

**Q126 [D] What would you change if the class grew to 100,000 students?**
Replace Scans with indexes (GSI on `kind`), split hot partitions for global counters (`inflight#global` is a single hot item; shard it), move listings to queries, reconsider DynamoDB item sizes. Knowing where the model breaks is the point of Q116 and Q130.

**Q127 [D] What is a "hot partition"? Which item here could become one?**
A single partition key receiving too many requests (DynamoDB limits ~1000 WCU/3000 RCU per partition). `inflight#global` and `runs#...` are updated on every request; fine for hundreds of req/s but the first bottleneck at scale.
Study: [Partition key design](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-design.html)

**Q128 [I] Why do money values use integer micro-dollars?**
DynamoDB numbers via boto3 are `Decimal`, and floats lose precision. One micro-dollar = $0.000001; 1 token at $1/million tokens costs 1 micro-dollar, so cost math stays integers.
Repo: `lambda/ai.py`, `lambda/gemini.py` docstrings.

**Q129 [D] What does `_plain(item)` do?**
Converts DynamoDB `Decimal` values back to `int`/`float` so JSON serialisation works. A classic boto3 gotcha: `json.dumps(Decimal(1))` fails.

**Q130 [I] Where does the code run in-memory instead of DynamoDB and why?**
`MemoryStore` (same interface) is used by tests so every Lambda test runs without AWS. It's a hand-written fake with the same atomic semantics (including a lock). The cost: fakes diverge from reality; see Chapter 14.

## C. Other state: S3 JSON, SSM, Secrets

**Q131 [I] When is S3 the right "database"?**
For large or blob-like state: knowledge indexes (`passages.json.gz`, `vectors.f16`), project file blobs, student files. S3 is cheap and durable but has no atomic multi-object write, so the knowledge store writes `doc.json` *last*: "a document exists once doc.json does".
Repo: `lambda/knowledge_store.py` docstring.

**Q132 [I] SSM Parameter Store vs Secrets Manager: what goes where?**
SSM SecureString parameters (cheap, free standard tier) hold the teacher account (hash + session secret), the student key map, the Google OAuth client. Secrets Manager (≈$0.40/secret/month) holds the AI keys (JSON with several keys). Both are encrypted and read at runtime, never baked into code.
Study: [Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html), [Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html).

**Q133 [I] Why cache the teacher config for 60 s?**
An SSM call per request adds latency and cost. A minute is a trade-off; it's also why a freshly set teacher password takes up to a minute to work.

**Q134 [B] What lives in each part of the data when exporting a backup?**
Both DynamoDB tables, S3 prefixes `sites/ archive/ userdata/ knowledge/ projects/`, and two SSM parameters. Not backed up: Secrets Manager (re-enter), `uploads/` and `portal/` (temporary / republished), Lambda functions and CloudFront tenants (live resources).
Repo: `docs/TEACHER_ARCHITECTURE.md` section 7.1.

**Q135 [I] Why does `importData` write items in batches of 25 and retry `UnprocessedItems`?**
`BatchWriteItem` accepts at most 25 items and may return some unprocessed under throttling; correct code must loop until none remain.
Study: [BatchWriteItem](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_BatchWriteItem.html)

## Study materials for this chapter

- Alex DeBrie: **The DynamoDB Book** and <https://www.alexdebrie.com/>
- Rick Houlihan's re:Invent talks: search YouTube "Advanced Design Patterns for DynamoDB" (several years; start with the earliest).
- AWS **DynamoDB best practices**: <https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/best-practices.html>
- Martin Kleppmann, *Designing Data-Intensive Applications*, chapters 5, 7 (replication, transactions).
- Hands-on: in `lambda/portal.py` compare `MemoryStore.counter_add` with `DynamoStore.counter_add` side by side (one uses a Python lock, the other a DynamoDB condition), then read `tests/test_portal.py` to see which behaviours are asserted.
