# Class Hosting Platform: Study Guide (547 questions with short answers)

> Private file (`.temp/` is git-ignored). Written for an engineer with about 3 years of AI/software experience who built this platform one commit at a time and now wants to **understand why it works**, not just that it works.
> Prepared from a read of the code, `template.yaml`, the docs, the tests and the git history at version 2.8.0 (2026-11-13).

## How to use this guide

1. **Do not read it like a book. Quiz yourself.** Read a question, say the answer out loud (or write two lines), then read the answer. If you missed it, open the "Repo" file mentioned and find the lines.
2. Questions are tagged **[B]** basics, **[I]** intermediate, **[D]** deep. Do all **[B]** first (84 questions), then **[I]**, then **[D]**.
3. Answers are deliberately short. They are anchors, not tutorials. The *Study materials* list at the end of every chapter and `99-READING-LIST.md` give the long-form explanations.
4. Limits and numbers come from `config/settings.json` (shipped defaults). A live platform may differ.
5. Honest caveats: I could not check every detail of every file. Where an answer says "check the file", I did not confirm it. Link correctness is reported in `99-READING-LIST.md`.

## Chapters

| # | File | Topics | Questions |
|---|---|---|---|
| 1 | `01-big-picture-and-multitenancy.md` | What the product is, tenants, three paths, request journey, state and lifecycle | Q1–Q30 |
| 2 | `02-serverless-lambda-apigateway.md` | Lambda, concurrency, the gateway checks, in-flight limiter, API Gateway, student sandbox | Q31–Q70 |
| 3 | `03-edge-storage-dns.md` | CloudFront, OAC, S3, presigned URLs, DNS, ACM, custom domains, browser isolation | Q71–Q106 |
| 4 | `04-dynamodb-and-state.md` | DynamoDB modelling, atomic counters, TTL, PITR, SSM, Secrets | Q107–Q135 |
| 5 | `05-security-iam-secrets.md` | Threat model, IAM, secrets, KMS, OWASP | Q136–Q170 |
| 6 | `06-identity-auth-oauth.md` | Tokens, sessions, scrypt, OAuth2, OIDC, PKCE, JWT, Cognito, Google sign-in | Q171–Q218 |
| 7 | `07-iac-installer-release-supplychain.md` | CloudFormation, SAM, plan/apply, installer engine, releases, Sigstore, kit signing | Q219–Q263 |
| 8 | `08-mcp-and-ai-editors.md` | MCP protocol, student and teacher servers, safety of tools, AI editors | Q264–Q298 |
| 9 | `09-student-kit-addons-sync-settings.md` | Kit, checker, add-ons, project sync, PWA, settings registry | Q299–Q330 |
| 10 | `10-llm-ai-connector.md` | LLM API basics, OpenRouter, Gemini proxy, caps and cost | Q331–Q360 |
| 11 | `11-rag-knowledge.md` | RAG, embeddings, BM25, hybrid search, RRF, confidence gate, evaluation | Q361–Q400 |
| 12 | `12-agents-patterns-safety.md` | Routers, agents, approval, quality loop, prompt injection | Q401–Q424 |
| 13 | `13-observability-jobs-worker.md` | Logs/metrics/traces, alarms, background jobs, EC2 worker | Q425–Q460 |
| 14 | `14-testing-engineering-frontend.md` | Test strategy, fakes, CI, decisions, vanilla JS portal | Q461–Q496 |
| 15 | `15-scenarios-cost-and-debugging.md` | "What happens if...", cost, debugging drills, design discussions | Q497–Q547 |
| — | `99-READING-LIST.md` | Curated links, videos, books, order of study, glossary | — |

## A suggested 4-week plan (about 1 hour a day)

| Week | Goal | Do |
|---|---|---|
| 1 | The mental model | Ch 1, 2, 3 (all [B], then [I]); read `docs/AWS_LEARNINGS.md` sections 0–7 and `lambda/gateway.py` twice |
| 2 | State and security | Ch 4, 5, 6; read `lambda/portal.py` sessions and `lambda/auth.py`; do the OAuth reading (oauth.com) |
| 3 | Delivery and tools | Ch 7, 8, 9; read `installer/engine.mjs` plan/apply and `starter/mcp/server.mjs` |
| 4 | AI parts and practice | Ch 10, 11, 12, 13, 14; then Ch 15 as an oral exam; write your own 10 questions |

If you only have one weekend, do this **core list**: Q5, Q11–Q13, Q21, Q41–Q43, Q46, Q77, Q94, Q108, Q117–Q120, Q136–Q139, Q172–Q176, Q189–Q192, Q224–Q226, Q233–Q236, Q264, Q273–Q274, Q300–Q301, Q361–Q362, Q368–Q369, Q376, Q405–Q409, Q417–Q419, Q435, Q444–Q446, Q464, and the whole of Ch 15 [B] and [I].

## Where each of your commits fits (so you can study in the order you built)

| Commit era (git log) | What was built | Chapters |
|---|---|---|
| 1.0.0–1.1.0 | Guided install, verify, destroy, CloudShell resume | 7 |
| 1.2.0–1.2.3 | Custom domains in the install, feature switches, kit follows platform | 3, 7, 9 |
| 2.0.x | One version for all, CI security tools, pinned actions, Sigstore | 7 |
| F1 / 2.0.4–2.1.0 | `portal/` write removed, KMS signing, student logs, Usage tab, cache limits | 5, 7, 2 |
| 2.1.1 | Stale-state fix, kit `.gitignore`, errors-only timeouts, Bring online | 7, 14, 2 |
| 2.1.2 | Background jobs, response headers policy, signed manifest of all files | 13, 3, 7 |
| 2.2.0 | Knowledge service (RAG), agent and quality helpers | 11, 12 |
| 2.3.x | Traces, compute worker, alerts and budget, capacity, backups of knowledge, project sync | 13, 2, 7, 9 |
| 2.4.0–2.7.0 | Add-ons, settings registry, installable apps, higher backend limits | 9 |
| 2.8.0 | Sign-in for student apps (Cognito + Google), platform side | 6 |

## 20 "if you only understand one thing" ideas behind the design

1. Three paths: pages, student code, control. Keep them apart. (Ch 1)
2. Assume student code is hostile; contain it. (Ch 5)
3. Put each limit where a bug in your code cannot bypass it (provider caps, IAM, S3 policy). (Ch 5, 10)
4. Atomic conditional writes replace locks. (Ch 2, 4)
5. Cheap checks first; count money only after slots are held. (Ch 2)
6. The stack is the truth; local state is a cache. (Ch 7)
7. Plan, show, then apply exactly what was shown (plan id). (Ch 7)
8. Whatever is destructive or secret stays in the human's terminal; the AI never gets those tools. (Ch 8)
9. Advice in a prompt is not enforcement; enforce in code. (Ch 8, 9)
10. A deterministic gate before an LLM is cheaper and safer than asking the LLM to be careful. (Ch 11)
11. Retrieval quality = chunking + hybrid search + a measured threshold. (Ch 11)
12. Tool results are data, not instructions; side effects need a human. (Ch 12)
13. Observability must never break the thing observed. (Ch 13)
14. Anything over a minute needs saved state and a way to resume. (Ch 7, 13)
15. A fake is only as honest as your knowledge of the real system; run the real thing once. (Ch 14)
16. Every real bug becomes a permanent test that failed before the fix. (Ch 14)
17. Test the artifact users receive, not only the source tree (npm drops `.gitignore`). (Ch 7)
18. Pin versions and hash everything that crosses a trust boundary. (Ch 7, 9)
19. Secrets are never in code, URLs, logs, chat or editor settings. (Ch 5, 6)
20. Write down accepted risks and the trigger that re-opens them. (Ch 5, 14)

## Self-assessment (be honest)

After finishing, you should be able to:
- Draw from memory the path of `GET /api/hello` from browser to student function and name each check.
- Explain, without notes, why `counter_add` is safe under concurrency.
- Explain OAuth Authorization Code + PKCE and how the shared Cognito pool + per-app client works.
- Explain how hybrid search with RRF works and why there is a confidence gate.
- Explain what `plan` and `apply` protect against and how `planId` binds them.
- Explain why the teacher MCP has no `destroy` tool.
- Name three things fakes in tests cannot tell you, with examples from this project's history.
