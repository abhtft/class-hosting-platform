# Trace schema (T0)

What a trace looks like, how it is stored, what each privacy level keeps, and a sample. The plan and the reasons are in [TRACEABILITY_PLAN.md](TRACEABILITY_PLAN.md); the code is `lambda/trace_core.py` (format and privacy rules, no AWS) and `lambda/traces.py` (store, settings, recording).

## 1. The idea in four lines

- One **trace** is one answer. It has one **summary** and one **span** per step.
- A span is shaped like an OpenTelemetry/OpenInference span (name, kind, time, duration, status, attributes), so the data can be exported to Phoenix or any OpenTelemetry tool later without conversion.
- What goes into a span's attributes depends on the student's **privacy level**. The default stores **no text at all**.
- Every item expires by itself after the student's **retention** (30, 60 or 90 days; default 90).

## 2. Identifiers

| Name | Shape | Notes |
|---|---|---|
| Trace id | `tr_` + 16 lowercase hex digits (`tr_0123456789abcdef`) | Made by the platform. An app may send its own in the `x-trace-id` header **only in exactly this shape**; anything else is replaced. The same id joins the app's steps and the platform's steps into one trace |
| Student (app) | the student's slug | Always taken from the key that made the call, never from the request |
| Partition | `t#<slug>#<trace id>` | One partition per trace; a student can never be reached through another student's key |

## 3. What is stored (table `TracesTable`)

| Item | Sort key | Fields |
|---|---|---|
| **Summary** (one per trace) | `summary` | `slug`, `app` (= slug), `day` (UTC date), `started` (ms), `level`, `spans`, `tokens`, `cost_micro`, `errors`, `ttl` |
| **Span** (one per step) | `<ts_ms:013d>#<source>#<4 hex>` | `name`, `kind`, `source`, `ts`, `dur_ms`, `status`, `attrs`, `slug`, `ttl` |

Only summaries carry `app`, `day` and `started`, so the two indexes (`by-app`, `by-day`) list answers without ever holding step text. Counters on the summary are added to atomically, so parallel steps cannot lose updates. Sorting by the time-first key returns the steps in order.

## 4. A span

| Field | Values |
|---|---|
| `name` | up to 60 characters: `retrieval`, `llm` today; `prompt`, `guard`, `tool`, `answer` come with T2 and the guards |
| `kind` | `retriever`, `llm`, `guard`, `tool`, `agent`, `chain`, `other` (anything else becomes `other`) |
| `source` | `platform` (written by the platform's own functions) or `app` (sent by the student kit, T2) |
| `ts`, `dur_ms` | start in epoch milliseconds, duration in milliseconds |
| `status` | `ok`, `error`, `skipped` |
| `attrs` | free keys, limited to 40; text values are cut at 4,000 characters; a whole span is kept under 60 KB |

Attributes written today:

| Span | Attributes |
|---|---|
| `retrieval` | `query`\*, `k`, `collections` (per shelf: `confident`, `version`, `documents`), `confident`, `hit_count`, `embed_tokens`, `hits` (per passage: `collection`, `doc`, `page`, `section`, `score`, `vector`, `bm25`, `coverage`), `passages`\* |
| `llm` | `model`, `provider`, `prompt_tokens`, `completion_tokens`, `cost_micro`, `finish_reason`, `temperature`, `max_tokens`, `messages`\*, `answer`\*, `error` |

\* **text keys**: `query`, `question`, `prompt`, `messages`, `answer`, `output`, `input`, `passages`, `text`, `reason`, `arguments`, `result`.

## 5. Privacy levels (set per student by the teacher)

| Level | Text keys | Everything else | Use |
|---|---|---|---|
| **metadata** (default) | **removed**; only the length (`query_chars`) or count (`passages_count`) is kept | numbers, ids, scores, model names, short labels (up to 40 characters inside lists and objects) | normal running; nothing a visitor typed is ever stored |
| **masked** | kept, cut at 4,000 characters, and **masked** | as above | finding bad answers; emails, Indian mobile numbers, Aadhaar-like numbers, PAN, long card-like numbers and key prefixes (`ks_`, `ga_`, `cs_`, `mds_`, `sk-`, `AKIA…`) are replaced with `[email]`, `[phone]`, `[id]`, `[pan]`, `[number]`, `[key]` |
| **full** | kept, cut at 4,000 characters, **not masked** | as above | debugging a project with consenting users |

An unknown level behaves as `metadata`. The level is chosen when a step is **written**; lowering it later does not hide older text (use delete), and the student view (section 7) can only hide, never reveal more than was stored.

## 6. Settings (on the student's token item, `traces`)

| Setting | Values | Default |
|---|---|---|
| `enabled` | true / false | true (when the platform feature is on) |
| `level` | `metadata`, `masked`, `full` | `metadata` |
| `retention_days` | 30, 60, 90 | 90 |
| `student_view` | `off`, `metadata`, `stored` | `metadata` |
| `daily_limit` | 0 to 100,000 new traces a day | 2,000 |

The daily limit stops **recording**, never an answer. Missing or odd values read as the defaults.

## 7. Who may read what

| Who | Reads |
|---|---|
| Teacher | every trace, at the stored level (`GET /api/admin/traces`, `/api/admin/traces/{slug}/{trace_id}`; delete with `DELETE`) |
| Student (T4, not built yet) | only their **own** app's traces, and only as `student_view` allows: `off` nothing; `metadata` the numbers only, even if text was stored; `stored` what was stored |
| Visitors | nothing; they may be shown the short trace reference (T2) |

## 8. Guarantees the tests check

- With the feature off, answers, headers and storage are exactly as before.
- Recording never raises: a broken or missing trace table cannot break or slow an answer beyond one failed write.
- The student's slug comes from the authenticated key; a trace id from outside is accepted only in the exact shape.
- Every item has an expiry equal to the student's retention at the time of writing.
- The two writing functions can reach only the `t#` prefix of the trace table (`tests/test_template_traces.py`); the portal can read and delete, not write.

## 9. A sample (metadata level, one answer with a search and a model call)

Summary:

```json
{"trace_id": "tr_9f2c1a7be03d5481", "slug": "alice", "day": "2026-11-06", "started": 1793960400123,
 "level": "metadata", "spans": 2, "tokens": 1480, "cost_micro": 212, "errors": 0}
```

Spans, in order:

```json
[
 {"name": "retrieval", "kind": "retriever", "source": "platform", "ts": 1793960400123, "dur_ms": 41, "status": "ok",
  "attrs": {"query_chars": 38, "k": 5, "confident": true, "hit_count": 3, "embed_tokens": 12, "passages_count": 3,
            "collections": {"library/biology": {"confident": true, "version": 4, "documents": 2}},
            "hits": [{"collection": "library/biology", "doc": "d1", "page": 12, "section": "Cells", "score": 0.031, "vector": 0.71, "bm25": 6.2, "coverage": 0.8}]}},
 {"name": "llm", "kind": "llm", "source": "platform", "ts": 1793960400190, "dur_ms": 1630, "status": "ok",
  "attrs": {"model": "gemini-3.5-flash-lite", "provider": "gemini", "prompt_tokens": 1320, "completion_tokens": 148, "cost_micro": 200,
            "finish_reason": "stop", "temperature": 0.2, "max_tokens": 600, "messages_count": 2, "answer_chars": 412}}
]
```

At the **masked** level the same trace also holds `query`, `passages`, `messages` and `answer` with personal details replaced.

## 10. Changing this document

The format is versioned by the code that writes it. A change that removes or renames a field needs a note in the changelog and, once exports exist (T7), a version number in the export file.
