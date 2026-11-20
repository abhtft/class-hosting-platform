# Handoff: Agent Studio (Chatwoot bot + agent platform)

Date: 2026-11-19. Language of the discussion: Hindi (Devanagari) with English technical terms. Keep the same style when continuing.

## 1. Goal (in the user's words, condensed)

- The user self-hosts Chatwoot (open-source omnichannel inbox). Messages from Facebook, Instagram, WhatsApp and the website reach a Chatwoot inbox.
- They want their own AI agent platform that connects to Chatwoot as an **Agent Bot**, so a bot can answer conversations from any channel.
- The platform should look like the Anthropic console: a left panel with a "New Agent" button at the top left. When creating an agent, the user chooses a type (Chatbot or General) and a purpose (for example Sales bot or Lead collection bot).
- Each agent gets knowledge: RAG documents (Word/DOCX, Google Doc links, PDFs), images and videos that the bot can use to answer.
- First milestone: the platform for creating and managing agents. Orchestration of sales and lead flows comes after.

## 2. Decisions taken so far

| Topic | Decision | Status |
|---|---|---|
| Where the work lives | A **separate** project ("Agent Studio", suggested domain studio.मार्गदर्शक.com or similar), not inside the main teacher platform repo | Recommended by assistant, user agreed in principle ("सहमत हूँ") |
| Shared code | Reuse the core (RAG, agent loop, quality, traces, jobs) from the teacher repo as a pinned copy first, later as a versioned package (for example `class-core`) | Recommended, not confirmed |
| Separate AWS account or stack for Studio | Yes, to limit blast radius and billing confusion | Recommended, not confirmed |
| Local working folder | `D:\Github_Codes\Test\studio` (user created it, is making a fresh branch there by copying a token) | Done |
| Git workflow | Work directly on **main**. No feature branches. The user is the only developer, so all work is pushed to main. Push after each checkpoint is approved (the user's explicit instruction, 2026-11-19) | Confirmed by user |

## 3. Facts verified about Chatwoot (from official docs)

- **Agent bot setup:** Settings → Bots → Add Bot. Provide name, avatar and webhook URL. Chatwoot generates a **secret** that is used to verify incoming payloads. The bot is then connected to an inbox under Bot Configuration. New conversations enter the **pending** status.
- **Events sent to the bot:** `widget_triggered`, `message_created`, `message_updated`.
- **There is no bot access token in the bot setup.** Replies go through the Chatwoot REST API using an API access token from Profile Settings. Recommended header: `Authorization: Bearer <token>` (Chatwoot v4.19.0 and later). `api_access_token` header is deprecated.
- **Reply mechanism:** the bot calls the "Create New Message" API endpoint (exact path and body to verify in developers docs).
- **Message status endpoint (user had asked about it):** `PATCH /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages/{message_id}`. Body `status` is one of `sent`, `delivered`, `read`, `failed`. It works **only on API-channel inboxes**. It is NOT the reply mechanism. Returns 403 for WhatsApp/Instagram/Facebook inboxes.
- **Handoff:** the bot switches the conversation to **open** via the conversation update API, so a human agent takes it. A human sets it back to **pending** to return it to the bot.
- **Loop safety (assistant's advice):** outgoing messages (including the bot's own replies) also arrive as `message_created`. Reply only to incoming customer messages. Verify the exact payload field in developers docs.

## 4. Facts about the existing teacher repo (`class-hosting-platform-github`, read partially)

- The file listing was truncated. Only a few files were read. No Chatwoot code was found in the visible part (not a full audit).
- Read files: `docs/AGENT_GUIDE.md`, `docs/RAG_GUIDE.md`, `docs/future/RAG_ROADMAP.md`, `student/class_agent.py`, `student/class_quality.py`, `lambda/gateway.py`.

Reusable pieces:
- **RAG:** shelves, hybrid search (vector + BM25, reciprocal rank fusion), confidence gate "The books do not say.", fenced passages. Files: `lambda/knowledge*.py`, `lambda/embeddings.py`.
- **Agent loop with guardrails:** `student/class_agent.py`. JSON-step loop, tool allowlist, budgets (steps, seconds, tokens), per-tool call limit, fenced tool results, human approval that pauses the run.
- **Quality:** `student/class_quality.py` — answer cache, thumbs feedback, `evaluate()`, `evaluate_retrieval()`.
- **Traces:** `docs/TRACE_SCHEMA.md`, metadata-only by default.
- **Background jobs:** `lambda/jobs.py`, up to 15 minutes.
- **Gateway:** `lambda/gateway.py` — origin-secret check, per-IP rate limit, in-flight limiter, run allowance. Its origin-secret check does NOT fit Chatwoot webhooks.

Known limits and gaps:
- Web request limit is 27 s. Agent budget inside a web request is about 16–20 s.
- Knowledge accepts only PDF, text, Markdown and HTML. No DOCX, images, video or Google Docs links. No OCR.
- Stop words and stemming are English-only. Hindi/Hinglish is roadmap Phase 3 (planned to be moved earlier).
- Human approval in `class_agent` pauses the run for a person on a page. This does not fit live chat with customers.
- Limits are per student, not per agent or organisation.
- Traces default to metadata-only. Lead data (personal data) needs its own rule.

## 5. Architecture agreed as the starting point (draft)

```
Customer (WhatsApp / Instagram / Facebook / Web)
  -> Chatwoot inbox -> Agent Bot webhook (signed with secret)
  -> Studio: POST /hooks/chatwoot/{agentId}
       verify HMAC, dedupe by message id, return 200 at once
  -> queue / background job
  -> agent runtime (loads the PUBLISHED version of the agent)
       Chatbot mode: RAG answer; low confidence -> handoff
       Sales/Lead mode: router + tools (search_kb, product info, save_lead with schema check)
  -> reply via Chatwoot "Create New Message" API (API token)
  -> handoff: set conversation to open
  -> trace (metadata) + feedback + eval

Knowledge pipeline:
  text:  upload or Google Doc (OAuth) -> extract (PDF / DOCX / text) -> chunk -> embed -> per-agent shelf index
  media: upload image/video + human description tag -> store as file (no AI analysis) -> matched by tag, shared in chat
```

Hard constraints (draft, to confirm in the spec):
- Model never receives secrets (Chatwoot token, channel tokens). Store in AWS Secrets Manager.
- Each agent's knowledge is isolated from other agents and other customers.
- Customer messages are never cached across customers.
- Loop prevention: reply only to incoming messages.
- No human approval in live chat: use allowlists, schema validation and handoff instead.

## 6. Phases (agreed draft)

| Phase | Deliverable | Gate |
|---|---|---|
| 0. Decisions | Answers to Q1–Q5 below; a Chatwoot test instance (API inbox) | Answers received |
| 1. Studio shell | Left panel, New Agent wizard, agent CRUD, versioning (no AI yet) | An agent can be created and published; version can be read |
| 2. Knowledge pipeline | Text RAG: DOCX, PDF, Google Docs via OAuth + Drive API; per-agent shelves. Media library: images and videos stored as files with a description tag, no AI analysis | Right passages come back for each text file type; media is found by its description and shared |
| 3. Chatwoot webhook | HMAC ingress, dedupe, async worker, reply, handoff; first an echo bot | Round trip on the test inbox; no loop; duplicates do not produce two replies |
| 4. Chatbot mode | RAG answer, confidence gate, Hindi/Hinglish retrieval, internal trace | Eval set passes (answers and refusals) |
| 5. Sales and lead mode | Tools, consent line, data minimisation, lead destination | Leads land in the chosen place; no unnecessary personal data kept |
| 6. Ops | Trace view, feedback, golden probes, per-agent limits, guardrails | Probes and attack tests pass |
| 7. Channels and business | WhatsApp, Instagram, Facebook rollout (check each channel's messaging rules); audit; roles | Each channel tested separately |

## 7. Decisions from the user's answers (2026-11-19) — these are FINAL unless the user changes them

- **Q1 (runtime): A.** New "agent" module inside the AWS kit (code reuse, one install per organisation). Note: this is in tension with section 2's "separate project" decision. Resolve in the Phase 1 spec: the Studio app is a separate repo that consumes the kit's core, and the runtime module is part of that stack. Confirm with the user before coding.
- **Q2 (lead data): A.** Store leads as Chatwoot contact attributes.
- **Q3 (Google Docs): B.** Google OAuth + Drive API, so private docs work too. This is a Phase 2 item with its own consent flow, token storage in Secrets Manager, and least-privilege read-only scope.
- **Q4 (media): a third option the user chose, NOT one of A/B.** Images and videos are NOT analysed by AI. No vision captioning, no OCR, no transcription, no frame extraction. Each media file is stored as a plain file together with a short human-written description (for example "library video", "offer video", "service X video"). When a customer asks for a brochure, service list or price list, the bot finds the matching media by its description and shares the file in the chat. The bot only reads the description; it does not look inside the media. Phase 2 scope shrinks accordingly: media becomes a tagged attachment library, separate from the RAG text index.
- **Q5 (first channel): A.** Chatwoot API inbox for the first tests, then WhatsApp and others later.

Working style for the rest of the project (user preference): surface assumptions as A/B/C questions, and ask first about questions that change the architecture. Before any coding, use a numbered Intent Capture checklist, a Spec Table with a Status column, a Boundary Document with Hard Constraints, and stop at every checkpoint. Do not start coding without approval.

## 8. Working folder state

- `D:\Github_Codes\Test\studio` already contains a starter-kit checkout: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.windsurfrules`, `kit-manifest.json`, `platform.json`, `portal-deploy.mjs`, `backend/`, `frontend/`, `evals/`, `tools/`, `variants/`, `PATTERNS.md`, `PROMPTS.md`, `INTERVIEW.md`, `README.md`, `package.json`. This is the student starter template, so the Studio project is starting from it, not from an empty folder. Decide whether to keep it or strip it. The assistant has NOT read these files yet.
- Git: all work goes directly to **main** (single developer, user's explicit instruction). No feature branch. Push to main after each approved checkpoint. Do not force-push.

## 9. Next step

1. Phase 1 spec (planning only, no code): Intent Capture checklist, Spec Table with the assistant's proposed answers and an empty Status column, Boundary Document, checkpoints. The user approves the Spec Table.
2. Read the starter files in `studio` (`AGENTS.md`, `platform.json`, `kit-manifest.json`, `README.md`, `PATTERNS.md`) to see what the starter already provides and what must be removed.
3. Only after approval: build Phase 1 (studio shell and agent CRUD), with a checkpoint after each step.

## 10. Caveats

- Chatwoot details come from two official pages only: the bot guide and the message-status endpoint. The Create New Message endpoint, the webhook payload fields and the access rules were NOT verified in full. Check developers docs before implementing.
- The repo audit is partial (truncated listing; six files read). It is not a full review.
