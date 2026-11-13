# Chapter 13: Observability, traces, alerts, background jobs and the optional compute worker

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> Code: `lambda/traces.py`, `lambda/trace_core.py`, `student/class_trace` (helpers inside the kit), `lambda/jobs.py`, `lambda/job_runner.py`, `lambda/worker.py`, `worker/runner.py`, alarms in `template.yaml`.
> Docs: `docs/TRACE_SCHEMA.md`, `docs/BACKGROUND_JOBS.md`, `docs/COMPUTE_WORKER.md`.

## A. Observability fundamentals

**Q425 [B] What are the "three pillars" of observability?**
Logs (events), metrics (numbers over time), traces (the path of one request through steps). Together they answer "what happened, how often, and why".
Study: [OpenTelemetry: Signals](https://opentelemetry.io/docs/concepts/signals/), *Observability Engineering* (Majors, Fong-Jones, Miranda; O'Reilly)

**Q426 [B] Monitoring vs observability?**
Monitoring tells you *that* something is wrong (known failure modes: alarms). Observability lets you ask new questions about *why* using rich data.

**Q427 [I] What is CloudWatch and which parts are used?**
AWS's monitoring service: Logs (Lambda output), Metrics (built-in Lambda/API Gateway metrics), Alarms (thresholds → SNS email). The template defines alarms for Lambda concurrency near limit, throttles, portal/sweeper/gateway/storage/knowledge function errors, and backend 5xx.
Study: [CloudWatch user guide](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/WhatIsCloudWatch.html)

**Q428 [I] Why is `TreatMissingData: notBreaching` used?**
If no data is reported (no invocations), the alarm shouldn't go to ALARM/insufficient state. For error-count alarms, "no data" means no errors.

**Q429 [I] How does an alarm reach the teacher?**
Alarm → SNS topic (`AlertsTopic`) → email subscription to the address the installer set (`/student-deploy/alert-email`). Optional via `UseAlerts` condition.
Study: [SNS email notifications](https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html)

**Q430 [I] What do the account-wide Lambda concurrency alarms protect?**
The shared pool (Chapter 2). Near the limit, other functions (including the teacher portal) get throttled; the alarm warns before that.

**Q431 [I] Why use structured JSON log lines (e.g. the gateway's `{"slug","status","ms"}`)?**
Machine-parsable: CloudWatch Logs Insights can query/aggregate them. Plain text needs fragile parsing.
Study: [CloudWatch Logs Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AnalyzingLogData.html)

**Q432 [I] Why must logs never contain tokens, codes or secrets?**
Logs are retained, readable by many roles, and exported. A leaked token in a log is a leaked credential. The platform masks student env values in the log view and sign-in design says "logs never contain a token or a code".

**Q433 [I] What is a runbook / "what to do when it breaks" and where is it?**
`docs/TEACHER_SETUP.md` section 13 & 13a, `docs/TECHNICAL_SPECIFICATION.md` section 10 (operations, alarms, emergencies).
Study: [Google SRE Book: Being On-Call / Postmortems](https://sre.google/sre-book/table-of-contents/)

## B. Traces (the project's own tracing)

**Q434 [B] What is a trace and a span?**
A trace = the story of one operation (one answer). A span = one step inside it (a search, a model call) with name, kind, start, duration, status and attributes. Spans nest to show cause and timing.
Study: [OpenTelemetry: Traces](https://opentelemetry.io/docs/concepts/signals/traces/)

**Q435 [I] Why a custom trace format "shaped like OpenTelemetry and OpenInference" instead of a hosted product?**
A tracing server running all month costs about $10–30; this costs under $5 at 100,000 answers, stays in the teacher's account, and maps one-to-one to Phoenix/Langfuse/LangSmith later.
Study: [OpenInference](https://github.com/Arize-ai/openinference), [Langfuse](https://langfuse.com/docs), [Arize Phoenix](https://docs.arize.com/phoenix)

**Q436 [I] What is the DynamoDB layout of a trace?**
Table `TracesTable`: partition `s = t#<slug>#<trace_id>`; sort `summary` (one per trace, the only item with index attributes) and `<ts_ms>#<source>#x` (one per span). GSIs `by-app` and `by-day`. Every item has a TTL (30/60/90 days).

**Q437 [I] What are the three privacy levels and the default?**
`metadata` (numbers only, default), `masked` (text with sensitive parts masked), `full` (text kept but capped). Text-bearing keys (`TEXT_KEYS`) are removed at metadata, masked at masked. What a visitor typed is personal data, so the safe default stores none.

**Q438 [I] Why "tracing must never break or slow an answer"?**
Observability is secondary. `record()` swallows its own errors; a daily limit (2,000) and per-trace step limit (200) bound it.

**Q439 [I] What is `student_view` and why may a student see their own app's traces?**
The teacher chooses off / metadata / stored. Students are developers and traces explain wrong answers. Lookup uses the signed-in student's slug, so another app's trace is "not found".

**Q440 [I] What is a rollup and why are there daily numbers?**
Aggregated counts per day (answers, "books do not say", errors, tokens, cost) kept ~400 days though raw traces expire at 90. Cheap trend data after raw data is gone; drives drift alerts (refusal jump 0.15, error rate 0.2).

**Q441 [D] What is "drift" in an LLM app?**
Behaviour changes over time (model update, data change, user mix): refusals or errors creep up. Detect by comparing a rolling baseline (7 days) with now, after a minimum number of answers (20).
Study: [Evidently: ML monitoring](https://www.evidentlyai.com/ml-in-production/data-drift)

**Q442 [I] How are traces archived and why a monthly reminder?**
Download an archive from the Traces tab (paged export up to 150 traces per call); a banner and monthly email warn before expiry. Nothing disappears unannounced.

**Q443 [D] Why is "replay of stored questions with new settings" not built?**
Low value until quality work needs it, and it requires storing text (privacy). A deliberate non-goal for now.

## C. Background jobs

**Q444 [B] Why do background jobs exist?**
A web request ends at ~27 s. Jobs let a student's code run up to the teacher's limit (default 5 min, max 15) and let the page poll for the result.

**Q445 [B] What is the page API?**
`POST /api/_jobs/<name>` (JSON body = input) → `202 {id, status: queued}`; `GET /api/_jobs/<id>` → `queued | running | done | failed` (+ `result`/`error`); `GET /api/_jobs` → latest jobs. These paths never reach the student's web function.

**Q446 [I] Draw the architecture.**
Browser → CloudFront → API Gateway → gateway Lambda (answers `/_jobs` itself) → writes a job item to DynamoDB → invokes **job runner** (platform, 15 min, async, no retries) → runner invokes **`sd-<slug>_job`** (the student's same zip, longer timeout) synchronously → runner writes done/failed + result.

**Q447 [I] Why a separate runner function instead of calling the job function directly from the gateway?**
The gateway has a 29 s limit; the runner can wait up to 15 minutes. It also gives one place to record outcome and release slots.

**Q448 [I] Why is async retry turned off for the runner?**
A retry would run the student's code again (double work, double cost). Failures are recorded, not retried.

**Q449 [I] What is the job state machine?**
`queued → running → done | failed`; if nobody finished it within `minutes×60+300` seconds it is shown (and recorded) as failed "The job did not finish". Reading never starts anything but may correct a stale record.

**Q450 [I] What limits apply and in what order are they taken?**
Slot for this student, slot for the platform (`jobs.concurrent` 1, `jobs.globalInflight` 2), then the day count (`jobs.perDay` 20). Slots first so a "busy" refusal doesn't burn a daily job; a daily-limit refusal gives slots back.

**Q451 [I] Why is the job's result capped (100 KB) and the input (64 KB)?**
DynamoDB item size (400 KB) and cost; large outputs belong in class storage files, not in the job record.

**Q452 [I] How do job items avoid scans?**
`joblist#<slug>` holds the latest 20 ids; `job#<slug>#<id>` items expire via TTL one day after finishing; counters expire a week after the day ends.

**Q453 [D] Compare this design with AWS Step Functions and SQS.**
Step Functions: durable workflows, retries, visual flow, but extra concepts/cost for beginners. SQS + worker: decoupled queue with scaling and DLQ. The chosen design is simpler for a class: no queue, single-student jobs, tight caps. The design doc `design-async-jobs.md` considered it.
Study: [Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/welcome.html), [SQS](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/welcome.html)

**Q454 [D] What is "at-most-once" vs "at-least-once" and which applies?**
At-most-once: may be lost, never duplicated. At-least-once: never lost, may duplicate (needs idempotent handlers). Jobs here are essentially at-most-once for execution (no retry) with a visible failure state.
Study: *Designing Data-Intensive Applications* (Kleppmann), chapter 11 on delivery guarantees; and the AWS Builders' Library article [Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/).

## D. The compute worker

**Q455 [B] What is the compute worker and why "off by default"?**
One small EC2 machine (`m7i-flex.large`) the teacher can start/stop for heavy checks later. It costs money only while running; today there are no tasks yet. Off by default and teacher-only.

**Q456 [I] How is the machine protected?**
No inbound rules, no SSH/key pair; reach only via Systems Manager Session Manager; encrypted disk; IMDSv2 only; role can reach only `worker/` in the bucket plus SSM; only the portal and a control function can start/stop and only that instance ARN.
Study: [Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html), [IMDSv2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html)

**Q457 [I] What is the watchdog and why does it exist?**
A control Lambda running every 15 minutes (EventBridge schedule) stops a machine that is on without a lease or past its limit. A hung job can't run for a month and run up a bill.
Study: [EventBridge Scheduler](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-rule-schedule.html)

**Q458 [I] What is a "lease" here?**
A record `worker#lease {started, until, mode, by}` explaining why the machine is on and until when. The watchdog compares reality to the lease.

**Q459 [I] Why choose a Free-plan-eligible instance type?**
A `c7g.xlarge` was refused on an AWS Free plan account. Real accounts have constraints that docs don't show; the type became a parameter.

**Q460 [D] When would a long-running machine beat Lambda?**
Jobs >15 minutes, big memory/CPU/GPU, large local disks, long-lived processes, special binaries. Trade-off: you manage the OS and the bill.

## Study materials for this chapter

- **OpenTelemetry concepts**: <https://opentelemetry.io/docs/concepts/>
- **Google SRE Book (free)**: <https://sre.google/sre-book/table-of-contents/> (chapters 4, 6, 12)
- **AWS Builders' Library**: *Instrumenting distributed systems for operational visibility* <https://aws.amazon.com/builders-library/instrumenting-distributed-systems-for-operational-visibility/>
- **Honeycomb blog** (observability thinking): <https://www.honeycomb.io/blog>
- YouTube (search): "What is OpenTelemetry?", "AWS re:Invent Serverless observability", "Step Functions vs SQS".
- Hands-on: read `docs/BACKGROUND_JOBS.md` sections 4–9, then `tests/test_jobs.py`; open `lambda/trace_core.py` and list which keys are text keys.
