# Repeatability testing (plan, not built)

## What it means here
**Repeatability** = the same question, asked of the same live version, gets an acceptable answer **every time**, not just once. The suite and the central safety suite today run each case once (one retry on a failure, a pass on the retry is marked *flaky*). One pass does not show that the next ten would pass too. This plan adds a way to measure that, and to refuse to trust a version whose answers wobble.

## Why now (is it the right step?)
Yes, and it should come before more features that rely on the score (auto-tune, make-live gates, provider comparison):
- The answer now depends on many moving parts: model sampling (temperature 0.2), the retrieved extras and books, **which provider and key answered** (Gemini keys in turns, then OpenRouter, Bedrock as a spare), conversation memory.
- The first real suite runs already showed run-to-run changes: different cases failed each time (HTTP 429 refusals), and `classroom-60` / `unknown-ceo` were flaky.
- The make-live gate on the safety suite uses ONE run. A must-case that passes 3 times out of 5 can pass the gate by luck.
- Auto-tuning (phase T-D) and provider comparison need a score that is not noise.

Risk to keep in view: it costs money (about 2 cents per suite run, so about 10 cents for 5 repeats of the standard suite) and it must not mistake provider refusals (429) for answer instability.

## Definitions
- **Run of a case** = one independent conversation (no shared memory) judged by the case's checks.
- **Pass rate of a case** over N runs, counting only runs that got an answer. A run that got no answer because of a provider error is `no_answer`, reported separately, never a failure of the agent.
- **Verdict per case:** `stable-pass` (all answered runs pass), `flaky` (some pass, some fail), `stable-fail` (none pass), `inconclusive` (too many `no_answer`).
- **Variation:** the number of distinct answers after normalising spaces and case, whether the route changed (rag / fallback / handover), whether a required fact (a `contains_all` check) was missing in some runs, and which provider/key answered each run (`via`).
- **Repeatability score of a version** = cases that are `stable-pass` / cases that ran. Reported next to the plain pass count.

## Design (kept simple)
1. **Repeat = N normal suite runs, aggregated.** A run already fits the worker's time budget; running it N times one after another (never in parallel: that would cause 429s) and combining the per-case records gives the numbers without changing how a run works. No new worker code in the first phase.
2. **Isolate causes.** The report shows the `via` of every answer. An option runs the repeats with one provider only (`--provider`), so variation from sampling can be told from variation caused by a different model answering.
3. **Same settings as production.** Repeats run at the production temperature; an optional second pass at temperature 0 shows how much of the variation is sampling.
4. **Thresholds (proposal):** a `must` case needs all answered runs to pass; a `should` case at least 80 percent. A version passes repeatability when no `must` case is `flaky` or `stable-fail` and at least 90 percent of all cases are `stable-pass`. Numbers are settings, not code.
5. **Two suites:** the business's standard suite and the central safety suite (40 cases); a safety case that is only sometimes refused is a failure.
6. **Output:** a table (case, pass rate, verdict, distinct answers, providers) printed by the command and saved as JSON; later stored on the version's state and shown in Studio.

## Phases (each with a done criterion)
| Phase | What | Done when |
|---|---|---|
| **R1** | `suite-repeat` in the teacher's command: runs a suite N times in sequence, aggregates, prints and saves the table, exit code 1 when the thresholds fail. No platform change, no deploy. | On `shop` (live version) a 5-repeat run of the standard suite prints a per-case table; a unit test covers the aggregation (stable, flaky, stable-fail, no_answer); a `no_answer` never counts as a failure. |
| **R2** | The platform keeps the repeat summary per version (`suitestate`), the make-live gate can require it for a version (optional setting), and `via`/`http_status` are always recorded per answer. | A version with a flaky `must` case is refused (or needs the written reason) and the summary survives in the state. |
| **R3** | Studio's Tests tab shows repeatability per version and highlights flaky cases; a weekly 5x run next to the nightly 1x run, with an alert on a drop. | The owner sees the score and the flaky cases without the terminal. |

Documentation after each phase: `docs/ARCHITECTURE.md`, `DECISIONS.md`, `RUNBOOK.md` (Studio repo) and `CHATWOOT_ADAPTER.md`, `CHANGELOG.md` (teacher repo). Full live testing once after R3, except that the R1 run itself is the live test of the feature.

## Amendment (owner's decision): a small repeatability set, 5 questions times 3 runs
Repeatability is not quality, so the check is small: **5 diverse questions, each asked 3 times** (15 calls, under a cent), not the whole suite. The five are existing cases of the standard suite, chosen for the kinds of variation that matter:
`price-social-media` (a number from the books), `office-hours` (an extra against the books), `unknown-ceo` (the fallback; flaky before), `handover-person` (a hand-over route), `language-hindi` (script and language).
- **What is compared across the 3 runs:** the route, the required facts (the case's checks), the language/script, and how many distinct answers came back. "Consistently wrong" is reported as `stable-fail` (a quality problem), not as unrepeatable.
- **Limit of 3 runs (be honest about it):** a case that passes 90 percent of the time passes 3 of 3 in 73 percent of tries, so a small wobble can slip through; a 50 percent case is caught 87 percent of the time. It is a smoke test for clear instability.
- **Escalation:** any case that is not 3 of 3 is repeated 10 more times, alone, to measure it properly (cost about a tenth of a cent each).
- **Full mode stays available** (`--cases all`, `--times 5`) for after a big change.

## Amendment 2 (owner's decision): the machine gives signals, a person decides
The sample is small enough to read, so no automatic verdict is trusted and no model judges answers. Changes to the plan above:
- **Signals instead of verdicts:** `same`, `differs`, `unexpected` (the same failure every time: a quality or out-of-date-case matter, not repeatability), `no answer`.
- **The real answers are shown** (every different answer, how often, which key) and written to a Markdown review sheet (`--sheet`).
- **Soft result:** LOOKS CONSISTENT / NEEDS A LOOK (exit 0); UNSTABLE (exit 1) only when a question's answered runs agree in fewer than 60 percent; INCONCLUSIVE (exit 2) when nothing was answered.
- **No escalation by default** (`--escalate N` on request).
- **R2 and R3 change:** no automatic make-live gate on repeatability. R2 keeps the review sheet per version on the platform; R3 shows it in Studio for a person to read.
- **Unchanged:** deterministic checks (route, facts, script), provider refusals never count against the agent, 5 questions times 3 runs.

## Status
- **R1 done** (`suite-repeat`, local command, commits 5e5f62e and b0e054e).
- **R2 built:** the check runs on the platform (worker step `repeat`, admin routes `.../repeat`, records `repeat#...` and `repeatstate#...`); the pure rules moved to `lambda/chatwoot_repeat.py`. No gate.
- **R3 next:** Studio shows the repeatability per version (a button, the signals, the answers, the sheet).

## What I need from you before building
1. **N:** 5 repeats (recommended: 10 cents for the standard suite) or 3 (cheaper, weaker) or 10.
2. **Which suite first:** the standard suite of `shop` (recommended) and then the central safety suite.
3. **Thresholds:** the proposal above (all `must` cases stable, 80 percent for `should`), or stricter.
4. **Start with R1 only** (a command, no deploy, results in one command) and decide R2 and R3 after seeing the first real numbers (recommended).
