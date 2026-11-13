# Chapter 14: Testing strategy, engineering practice and the portal front end

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> Code: `tests/run.mjs` (runs ~70 test files in numbered steps), `tests/fakes.py`, `tests/fake_api.py`, `tests/live_smoke.py`, `tests/dev_server.py`, `web/*.js`, `.github/workflows/ci.yml`.

## A. Testing

**Q461 [B] How do you run the whole test suite and what does it need?**
`node tests/run.mjs` (Node 22+, Python 3 with boto3). No AWS account is needed: tests use fakes. Each `step()` runs one test file and prints ✔/✖; failures are counted and the process exits non-zero.

**Q462 [B] What is the test pyramid and where do this project's tests sit?**
Many fast unit tests at the base, fewer integration tests, very few end-to-end tests at the top. Here: pure-function unit tests (knowledge_core, trace_core), Lambda-module tests with an in-memory store, kit end-to-end (a built kit's dev server), and a small live smoke test on real AWS.
Study: [Martin Fowler: Practical Test Pyramid](https://martinfowler.com/articles/practical-test-pyramid.html)

**Q463 [I] What is a test double? Name fake, stub, mock, spy.**
Stand-ins for real dependencies: stub = canned answers; fake = a working lightweight implementation (in-memory DynamoDB); mock = verifies calls were made; spy = records calls. This project mostly uses *fakes* (`MemoryStore`, fake AWS CLI) that record what was called.
Study: [Fowler: TestDouble](https://martinfowler.com/bliki/TestDouble.html), [Mocks aren't stubs](https://martinfowler.com/articles/mocksArentStubs.html)

**Q464 [I] What is the biggest weakness of fakes and how is it handled?**
A fake is only as honest as your knowledge of the real system. Real-AWS runs found: bucket versions blocking deletion, 201 vs 200, circular dependency, signature version bug, CloudFront verification. Rule: "write the fake from what the real system does, then check the fake against the real system once", and turn each finding into a permanent test.

**Q465 [I] What does `live_smoke.py` / `live_load.py` do?**
Run against a real account to prove things fakes can't (e.g. S3 really rejects an oversized upload with 400, the provider refuses a call over the cap, concurrency behaves). They are not part of `run.mjs`.

**Q466 [I] What are "template tests" (`test_template_*.py`)?**
They parse `template.yaml` and assert security/structure rules: IAM scoping (who can read the secret), no inbound rules on the worker, the signing key has no `kms:Sign` for platform roles, Lambda timeouts are ordered. Security rules as executable tests.

**Q467 [I] What is `test_spec.py` and the "machine-checked facts" in the spec?**
The technical specification has a section whose facts (numbers, names, versions) are checked against the code by a test, so documentation can't silently drift.

**Q468 [I] What does `test_no_identifiers.py` guard?**
That no account ids, emails, domains or key-shaped strings are committed, protecting the open-source repo from leaking private data. `tools/scrub-identifiers.mjs` and `tools/private-files.json` support it.

**Q469 [I] Why do tests inject time (`now`) instead of sleeping?**
Deterministic and fast. Examples: token expiry, counters per minute, session expiry, dry-run installs.

**Q470 [I] What makes a regression test for a bug trustworthy?**
It fails without the fix. After the 2.1.1 stale-state bug, the new test was proven to fail when the fix was removed. A test that never failed proves nothing.

**Q471 [I] Why write tests that pin two copies of a value equal (e.g. the checker's limits and the platform's limits)?**
A test imports both and asserts equality so you can't change one without the other. Cheap protection against drift between duplicated constants.

**Q472 [I] How does the kit get an end-to-end test?**
`test_kit_e2e.mjs` builds a kit, runs its checker and dev server, and exercises flows; `test_ai_patterns_e2e.mjs` runs search → approval → decline → `npm run eval`.

**Q473 [I] Why test CRLF/LF and Windows path issues?**
The author works on Windows; line endings changed a hash (kit hash and add-on checksums), Git Bash rewrote `/aws/...` log group paths (`MSYS_NO_PATHCONV=1`), bash `tar` couldn't read zips. Cross-platform bugs hide in the edges between tools.

**Q474 [D] What does CI cover and not cover?**
GitHub Actions runs the suite (Linux) plus CodeQL/Scorecard. CRITICAL_ISSUES item 5: no automated checks on real AWS, CI Linux-only (while the author uses Windows).

**Q475 [I] What is TDD and is it used?**
Test-driven development: write a failing test, make it pass, refactor. Parts are TDD-like (bug → test → fix). Not dogmatic; the stronger habit is "every real bug becomes a permanent test".
Study: Kent Beck, *Test-Driven Development: By Example* (book)

## B. Engineering decisions and habits

**Q476 [B] What is an ADR / decision record and where are they here?**
A short record of a decision, options and reasons. This repo keeps them in the spec section 12 ("Decisions, rejected alternatives and open items") and STATUS section 6.
Study: [Michael Nygard: Documenting architecture decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)

**Q477 [I] Name five decisions you should be able to defend.**
(1) Teacher's own AWS account (ownership). (2) Pure-stdlib Python/zero-dependency Node (supply chain, simplicity). (3) Provider-enforced caps (security). (4) Plan-then-apply with a plan id (safety). (5) Teacher portal as the only kit version authority (consistency). Also: no student code outside the student's function; own trace format; plain HTML/JS portal.

**Q478 [I] What are the costs of "no framework, no dependencies"?**
More hand-written code (agent loop, MCP server, tar writer, BM25) and more to maintain/test. Benefits: auditability, tiny package, fewer vulnerabilities, fewer upgrades. A conscious trade-off, not a default.

**Q479 [I] What does "feature freeze, fix P0 gaps" mean (STATUS 2026-11-08)?**
Stop adding features until blockers for a pilot are fixed: alerts/budget, clean-room install, capacity, backups of newer features. Technical debt is paid before more surface area is added.

**Q480 [I] What is a "spike"?**
A time-boxed experiment to answer a feasibility question. The package + deploy trick that avoided SAM CLI was a spike that made the one-command installer possible.

**Q481 [I] What is technical debt and where is it listed?**
Shortcuts that cost later. `docs/CRITICAL_ISSUES.md` P2/P3 and spec 12.3: hard-coded models/prices, vendored PDF reader with no update tracking, old permanent-key deploy path still shipped, per-IP lockout, no CSP.

**Q482 [D] How do you decide what to build next as a solo maintainer?**
Weigh risk × user pain × effort: blockers for a safe pilot first, then measured quality improvements, then new features. Keep a written queue (`ROADMAP.md`) and revisit after real user feedback.
Study: [Intercom: RICE prioritization](https://www.intercom.com/blog/rice-simple-prioritization-for-product-managers/)

**Q483 [I] Why version docs with code?**
Docs rot. The spec's checked facts, diagrams generated from Mermaid, and changelog make documentation part of CI. "Documentation was stale and bloated (done in this change)" was itself a listed issue.

## C. The portal front end (vanilla JS)

**Q484 [B] What technology is the portal built with?**
Plain HTML, CSS and ES modules (`web/*.js`), no framework, no build step. `common.js` provides `h()` (create DOM safely), `fill()`, `api()` and `session()` helpers.

**Q485 [I] What does `h('div', {class:'x'}, 'text')` protect against?**
XSS: text is appended as text nodes, never parsed as HTML. Event handlers are attached with `addEventListener`.
Study: [MDN: Node.textContent vs innerHTML](https://developer.mozilla.org/en-US/docs/Web/API/Node/textContent)

**Q486 [I] Where does the portal keep the session and why `sessionStorage`?**
`sessionStorage` keyed `session-<role>`; cleared on tab close, not shared across tabs. A fallback in-memory object is used when storage is unavailable (private mode). Tradeoff: JS-readable (XSS risk) but short-lived (8–12 h) and role-specific; no CSP yet (listed).

**Q487 [I] What does `api(path, {role})` do?**
Calls the portal API, attaches `Authorization: Bearer <session for that role>`, and throws `ApiError(status, message)` for non-2xx. One wrapper keeps error handling consistent.

**Q488 [I] Why separate pages (`teacher.html`, `student.html`, `index.html`)?**
Different roles and bundles; a student never needs teacher code. (The teacher API still enforces role server-side: UI hiding is not security.)

**Q489 [I] Why does the UI hide buttons but the server also checks?**
UI is convenience; the server is the authority. E.g. "Bring online" button shows only when the site is offline and unexpired, and the server returns 409 otherwise.

**Q490 [D] What is `web/zip.js` for?**
A small dependency-free zip helper used by the portal pages (together with `web/check.js` and `web/backend-check.js`, the in-browser checks) to handle a site or backend zip before upload. Compare with `starter/zip.mjs` on the CLI side. Open the files to confirm the exact split of work.

**Q491 [I] What is `usage.js`?**
The Usage tab logic: per-student counters (backend runs, AI spend, storage, knowledge, traces), shown to the teacher with limits.

**Q492 [D] How would you add a Content-Security-Policy to the portal?**
Serve a `Content-Security-Policy` header from the portal CloudFront response policy: `default-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'`, and remove inline scripts. The portal already avoids `innerHTML`, so this is feasible. See CRITICAL_ISSUES item 16.
Study: [MDN: CSP](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP)

## D. Languages and tools worth knowing here

**Q493 [I] Why both Python and Node?**
Lambda platform code and student backends are Python (stdlib); all installer/CLI/MCP tooling is Node (`npx` distribution, cross-platform, JSON-RPC over stdio, same language as the frontend toolchain). Students may pick a Node backend variant.

**Q494 [I] What is `npx` and how does the package run?**
npm's package runner: downloads a package (here a GitHub-hosted tarball) to a cache and runs its `bin`. `bin.mjs` routes subcommands.
Study: [npx docs](https://docs.npmjs.com/cli/v10/commands/npx)

**Q495 [I] What does `child_process.spawn` buy `awsCall`?**
It runs the AWS CLI as a child process with controlled arguments (no shell string concatenation → less injection risk) and captures stdout/stderr.
Study: [Node child_process](https://nodejs.org/api/child_process.html)

**Q496 [D] What is ESM (`.mjs`) and why does the repo use it?**
ECMAScript modules (`import`/`export`) are the standard module system; `.mjs` files are ESM without a package.json `type`. Top-level `await` works (used in `tests/run.mjs`).
Study: [Node ESM docs](https://nodejs.org/api/esm.html)

## Study materials for this chapter

- **Fowler: Test Pyramid / Test Doubles / DI** (links above).
- **Google Testing Blog**: <https://testing.googleblog.com/>
- Book: *Working Effectively with Legacy Code* (Feathers) and *Release It!* (Nygard) for failure-mode thinking.
- **MDN JavaScript guide**: <https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide>
- YouTube (search): "Testing serverless applications", "Mocks vs fakes vs stubs", "Architecture decision records".
- Hands-on: pick one `tests/test_*.py`, change an assertion to the wrong value, and run `python tests/<file>` to see how failure messages look; revert.
