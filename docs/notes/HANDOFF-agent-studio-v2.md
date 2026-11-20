# HANDOFF v2 — Agent Studio (chat -> execution sessions)

Date: 2026-11-19. Supersedes HANDOFF-agent-studio.md and PHASE-1-SPEC-v2.md where they conflict.
From now on ALL planning and implementation continue in the execution sessions (Claude Code). The planning chat is closed.

## 0. How to work (user's standing protocol — follow it)
The user (Abhishek) wants the Outer Boundary Method for every coding task:
1. Intent capture: 2-3 structured questions per dimension (Scope, Depth, Constraints, Edge Cases, Intent, Non-Goals), numbered, wait for answers.
2. Spec table: Decision Point | Proposed Answer | User's Answer | Status. Wait for the user to fill it.
3. Boundary document: Hard Constraints, Implementation Plan, Checkpoints (mandatory pause points).
4. Only then write code; stop at each checkpoint and report.
Other rules: when in doubt, stop and ask. Ask with concrete options (A/B/C), not open questions. Ask first the questions that change the architecture.
The user's language is Hindi/Hinglish; reports and questions to the user can be Hindi, code/specs in English.
Work alone, everything on main, no feature branches, local git only (no remote). Never print tokens, never commit .env.local.
Commit trailer (use on every commit):
  Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01QMGPoSdY1KM36kGtfJEywH

## 1. Goal
"Agent Studio": a platform to create AI agents that connect to the user's self-hosted Chatwoot as Agent Bots. A bot attached to an inbox answers messages from website, WhatsApp, Instagram, Facebook (the channel does not matter; the bot talks to the inbox).
UI like the Anthropic console: left panel, "New Agent" button top-left. Agent has a type (Chatbot or General) and a purpose (sales, lead_collection, support, faq; assistant, custom). Knowledge: DOCX, PDF, Google Docs (OAuth + Drive API), media files (images/videos) stored with a description tag and shared in chat on request, NOT analysed by AI.
Business intent: Studio and the teacher platform stay separate so agents can become a separate business later.

## 2. Domains (spelling is "margdarshakss"; corrected 2026-11-19: "markdarshakss" does not exist in DNS, platform.json and push_project use margdarshakss)
- Main teacher portal ("head"/lead): deploy.margdarshakss.com
- Studio site: studio.margdarshakss.com = a STUDENT PROJECT on the main portal (confirmed by user). Local checkout: D:\Github_Codes\Test\studio
- Teacher repo (read, and later modify only after approval): D:\Github_Codes\class-hosting-platform-github
- Notes folder: D:\Github_Codes\Test\studio-notes
- Open: executor must confirm (hostname only) which host the class-platform MCP config and the C1 push_project used.

## 3. Architecture decision (R2, confirmed)
Teacher platform = head. It gets new product features; Studio is an HTTP client of it:
1. Channel adapter (Chatwoot webhook route).
2. Fast-ack + async processing (no separate job system or queue).
3. Query-able data store for messages and agent versions (no 64 KB limit).
4. Versioned agent runtime API (RAG + agent loop + quality + traces).
Separation rule: Studio <-> teacher only by HTTP API + token. No shared files. Later, separating Studio = change base URL and keys.
Rejected: R1 (stay only on student kit; limits too tight for 100-5,000 msgs/day), R3 (full separate AWS stack; too much work now), GitHub integration (not needed; use MCP push/pull).

## 4. Decisions table (user confirmed D1-D10; no doubts)
| # | Decision | Final |
|---|---|---|
| D1 | Volume | 100 to 5,000 messages/day |
| D2 | Tenancy | One Chatwoot; each business has its own inbox (own admin). Bot runtime is one universal place. Mapping key: (chatwoot_account_id, inbox_id) -> agent (published version). Keys (bot secret, API token) per client |
| D3 | Architecture | R2 above; Studio separate repo and deploy |
| D4 | Separation | HTTP API + token only |
| D5 | Webhook route | Separate route; HMAC signature verify; return 200 immediately; exempt from the per-IP limit (all Chatwoot calls come from one server IP) |
| D6 | Reply path | Webhook -> verify + dedupe by message_id -> 200 at once -> same function re-invokes itself async (Lambda InvocationType=Event, up to 15 min, built-in retries) -> RAG+LLM -> Chatwoot Create Message API with account_id/inbox_id/conversation_id. On failure: move conversation to human handoff, never stay silent. NOTE: the webhook response body is not used for the reply, so there is no reason to hold the webhook open |
| D7 | Data store | Query-able store on teacher side; choice pending T0 audit |
| D8 | Channels | One adapter for all inboxes; first test = website widget |
| D9 | Loop safety | Reply only to incoming messages; handoff by conversation status (open/pending) |
| D10 | Secrets | Teacher-side secret store, never in 64 KB class storage, never exposed to student code |
Pending verification (decides D6/D7): can the teacher runtime invoke a function async (self-invoke, IAM lambda:InvokeFunction)? What is the real Chatwoot webhook timeout and retry behaviour? Both are T0 questions.

## 5. Verified Chatwoot facts
- Agent Bot: webhook URL + generated secret for payload verification. Events: widget_triggered, message_created, message_updated.
- Reply via Create New Message API, Authorization Bearer API token (Chatwoot v4.19.0+).
- PATCH /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages/{message_id} only sets delivery status (sent/delivered/read/failed); API-channel inboxes only, 403 otherwise. It is NOT how replies are sent.
- Handoff: change conversation status (open/pending).
- UNVERIFIED (check in T0/Q8): webhook HTTP timeout (recalled as only a few seconds), retry on failure, exact signature header name and algorithm, whether global/platform bots can serve several accounts.

## 6. Student-kit limits (why Studio cannot carry runtime load)
Class storage value <= 64 KB, no SQL/search/sort, last writer wins; 27 s web request; custom domain off; Python 3.12 stdlib only; one function per student site; secrets only in portal env variables; no Secrets Manager; 5 MB file limit; job trigger only from the page; gateway has origin-secret, per-IP limit, in-flight limit, run allowance.
Studio's own UI and records (agents, agent-versions) fit the kit for now. Anything runtime/webhook goes to the teacher side.

## 7. Teacher kit pieces to reuse (read in earlier sessions)
RAG shelves (hybrid BM25 + vector, RRF), confidence gate "The books do not say.", class_agent JSON-step loop (run(goal, tools, ...), resume(state, approve, tools, ...)), class_quality (cache, feedback, evaluate, evaluate_retrieval), traces (metadata-only default), background jobs (up to 15 min), lambda/gateway.py. Docs: docs/AGENT_GUIDE.md, RAG_GUIDE.md, future/RAG_ROADMAP.md, MCP.md. Coupling to "student/class/site" is unknown -> T0 Q5.

## 8. class-platform MCP (docs/MCP.md)
stdio, package class-platform-mcp. Tools: init_project, check_project, deploy (confirm=true; refuses on failed check), status, logs, project_status, push_project / pull_project (dry_run first; confirm=true to write; first yes enables auto-save after every deploy), project_auto, list_addons, add_addon, remove_addon, update_kit. Token only from environment or project .env.local. User connected it to Claude Code on Windows (cmd /c npx ...). Managed starter files (class_*.py, tools/, portal-deploy.mjs, platform.json, kit-manifest.json, .github/workflows/deploy.yml, variants/, kit docs) are auto-updated by sync: do not edit them.
Rules: project_auto OFF (recommended, user accepted). Deploy only on explicit user yes with confirm=true. push_project: dry_run first, confirm only on user yes.

## 9. Studio checkpoints (Phase 1)
- C0 approved. C1 DONE: starter checkout in D:\Github_Codes\Test\studio, local commit 9fa72c0 (84 files), no remote; push_project sent 29 files, revision 1, 124 KB; auto-save was turned on in C1 (turn it OFF in C2); no deploy; .env.example has only a commented placeholder.
- C2 NEXT (prompt below): starter cleanup + empty shell; spelling fix in the spec; host confirmation.
- C3 sign-in (class_auth, single owner, Google). Local "Local Visitor" first; `signin -- on` only with user's yes.
- C4 CRUD for agents (409 via updated_at, 404 for other owners).
- C5 publish and versions (separate records: agents and agent-versions, because of the 64 KB limit).
- C6 left panel + New Agent wizard (add-ons frontend-design, responsive, accessibility here).
- C7 editor + versions UI.
- C8 gate and deploy (manual, user's explicit yes).
Decisions from PHASE-1-SPEC-v2: English-only UI; secrets in portal env variables; keep the kit, remove the demo; .temp/ outside the project; no KIT-PIN.md; spec copies live in studio-notes.
Demo files to delete in C2: frontend StudyPanel.tsx, AgentPanel.tsx, patterns.ts; backend/study.py; evals/study.json; starter demo routes in backend/app.py; App.tsx is replaced by the empty shell.

## 10. Teacher-side phase (Phase 2) checkpoints — NOT started, needs user approval each
- T0 (read-only audit of the teacher repo; prompt below). Output: answers + risks; STOP.
- T1 webhook endpoint + signature verify + dedupe + 200 + async re-invoke; echo bot only, no AI.
- T2 reply via Chatwoot Create Message API with loop safety.
- T3 inbox -> agent mapping, per-business keys in secret store.
- T4 agent runtime (RAG + agent loop) behind the versioned API.
- T5 handoff and failure handling.
- T6 connect Studio UI to this API.
After T0: write the final Phase-2 spec table (D6, D7 finalized), get the user's answers, then boundary doc, then T1. Do not touch the teacher repo (no edits, commits, deploys) before the user approves T1.
Later decision (Phase 3+): keep Studio as a student project (R1 for UI) while runtime lives on the teacher side (R2). Re-check if volume exceeds 5,000/day.

## 11. Prompts already given to the user (the user pastes them)
### T0 (session in D:\Github_Codes\class-hosting-platform-github, plan mode, read-only)
Questions Q1-Q8: (1) async self-invoke possible + IAM/timeout/memory; (2) external webhook route with HMAC only, x-origin-secret, per-IP limit exemption; (3) persistence options, query by key, conditional writes for dedupe, items > 64 KB; (4) teacher-side secrets storage for per-client Chatwoot secret+token; (5) reusability of class_agent/RAG/quality/traces for a non-student caller, coupling points; (6) auth between Studio and teacher runtime (server-to-server token); (7) where to put new code without touching existing behaviour, test/deploy commands; (8) Chatwoot webhook timeout, retry, signature header/algorithm with source URLs. Output: numbered answers + risks + minimal change list; also write D:\Github_Codes\Test\studio-notes\T0-AUDIT.md; STOP.
### C2 (session in D:\Github_Codes\Test\studio)
Spelling: keep margdarshakss (the earlier "markdarshakss" claim was wrong, see section 2); confirm MCP/push host (hostname only); delete demo files; empty shell with left panel + "New Agent" top-left + "No agents yet"; English; no sign-in/CRUD/add-ons; project_auto OFF; no deploy; no push confirm=true; check_project + build + tests; local commit with trailer; report; STOP.

## 12. Caveats and mistakes to avoid
- Earlier the planning chat misspelled the portal as "margdarshakss"; PHASE-1-SPEC-v2.md and the first C2 prompt still contained it. Fix first.
- Studio is already pushed once (revision 1) to the portal host; confirm which host.
- Do not assume teacher-platform capabilities (async invoke, data store, gateway exemption) before T0.
- Media files are stored with a description tag only; no AI analysis.
- Single owner with Google sign-in for Studio Phase 1; multi-tenant Chatwoot mapping is Phase 2 design (D2).
