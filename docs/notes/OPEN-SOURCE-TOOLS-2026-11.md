# Open-source tools that could shorten the road (2026-11-24)

Written from what the repo does today (see `ARCHITECTURE-REVIEW-2026-11.md`) and general knowledge of the open-source field. **No star counts, versions or licences are quoted: they change, and I could not check them from here.** Before adopting any tool: read its licence (AGPL and "source-available" terms matter if it is hosted for clients), its last release date, and run a one-day spike.

The rule used to choose: adopt a tool only if it (a) replaces something we would otherwise build and maintain, (b) can be hosted cheaply (Railway today, like Chatwoot), (c) can be swapped out later without rewriting the agent core. Our core (receiver → worker → grounded answer → versions → checks) stays ours: it is the differentiator and it is already well tested.

---

## 1. Where the architecture has gaps, and what fills them

| Gap (review id) | Tool | What it gives | How it plugs in | Verdict |
|---|---|---|---|---|
| Trace UI, cost per call, prompt/version comparison (S1, C1) | **Langfuse** | Trace viewer, per-call cost and latency, sessions, datasets | Worker sends spans (OpenTelemetry or its SDK) next to our own traces; our TracesTable stays the source of truth | **Spike first.** Biggest time saver for "why was this slow or wrong". Self-hosted needs Postgres + ClickHouse: use the cloud free tier for the spike |
| Evaluation of answers beyond key-fact match (R3) | **Promptfoo**, **Ragas**, **DeepEval** | Test matrices, LLM-as-judge for faithfulness and relevance, CI runs | Run offline against the `rag` route with our tests table exported as YAML/JSON | **Adopt Promptfoo** for CI regression runs; Ragas only for retrieval quality numbers (context precision/recall) |
| Red-team beyond our 52-case suite (R4, safety) | **Garak**, **PyRIT** | Hundreds of probes (injection, leakage, jailbreak) | Run against a test endpoint monthly; feed new failures into our safety suite as cases | Use as a periodic scanner, not in the request path |
| Personal data in logs and traces (traces privacy) | **Microsoft Presidio** | Detects and masks phone, e-mail, ID numbers (Indian formats need custom recognisers) | Mask before writing traces / lead records | Good fit; our `trace_core` already has masking hooks |
| Hard guardrails on output (R4) | **LLM Guard**, **NeMo Guardrails**, **Guardrails AI** | Input/output scanners, topic rails | Our deterministic checks already cover most; a distress/self-harm keyword list is cheaper than any of these | Skip for now; revisit if rules grow |
| Alarms and uptime (L2) | **CloudWatch alarms** (native), **Uptime Kuma** | Alarms on worker errors; outside-in ping of the webhook and Studio | Alarms in `template.yaml`; Uptime Kuma on Railway for a status page and WhatsApp/e-mail alert | Alarms are cents; Uptime Kuma is a nice extra for clients |
| Secrets (E4) | **Infisical** | Secret store with rotation and audit | Only if secrets sprawl past AWS Secrets Manager | Skip: Secrets Manager is already in the stack |

## 2. Agent types with actions (the business goal: sales, leads, marketing)

| Need | Tool | What it gives | How it plugs in | Verdict |
|---|---|---|---|---|
| Lead record, pipeline, follow-up reminders (P2) | **Twenty CRM** | Open-source CRM with API and webhooks | Action "record lead" posts name, phone, need to Twenty; Studio's Leads card reads from it or from our own small table | Strong candidate for clients who want a real CRM. For a first version a DynamoDB `lead#` record plus CSV is enough |
| Bookings (Booking agent type) | **Cal.com** | Scheduling pages, availability, API, webhooks | Action "offer slots / create booking" calls its API; confirmation goes back through Chatwoot | Best fit for clinics, coaching, salons |
| Visual workflows for the actions that follow a lead (send brochure, notify owner, update sheet) | **n8n** (fair-code licence: check terms for client hosting) or **Activepieces** (MIT core) | 300+ connectors, no code | Our worker calls ONE webhook per action; the client's flow lives in n8n. Keeps our core free of one-off integrations | Adopt **one** of them as the "integration hatch". Do not let it into the answer path |
| Social posting and campaigns (marketing) | **Postiz** | Scheduling across social channels | Already connected through its MCP; agent type "Marketing" drafts, owner approves, Postiz schedules | Already chosen |
| Forms and quick surveys inside the chat | **Typebot** | Chatbot flows with forms | Only for strictly scripted flows (a registration form); the LLM agent stays for open questions | Optional |

## 3. Channels and conversation layer

| Need | Tool | Note |
|---|---|---|
| Inbox, handover, agents, WhatsApp via official Cloud API | **Chatwoot** (in use) | Keep. Check the version supports bearer tokens (>= 4.19 per the T0 audit) |
| Unofficial WhatsApp bridges (**Evolution API**, **WAHA**, Baileys-based) | These link a normal number by QR. They are cheap and fast to start, but they break WhatsApp's terms and numbers can be banned. For paying clients use the official Cloud API through Chatwoot. Fine only for a personal test |
| Voice notes (customer sends audio) | **Whisper / faster-whisper** | Today a voice note with no words is handed over. Transcribing it (self-hosted on a small box, or a provider API) would let the agent answer. Cost per minute is small; adds latency. Optional upgrade of the media rule |

## 4. Knowledge and retrieval (only when traces show the need)

| Need | Tool | Note |
|---|---|---|
| Better book ingestion: tables, scanned PDFs, layouts | **Docling** (IBM), **Unstructured**, **Marker** | Replace the PDF reader only if real client books extract badly. Run as a batch job at upload, not at answer time |
| Reading a client's website as a book | **Firecrawl** (self-hostable), **Crawl4AI** | Gives "paste your site URL, we build the shelf". Strong demo and onboarding feature for new businesses |
| Reranking for short Hinglish questions (R3) | **bge-reranker** or a hosted reranker | One more step after hybrid search; try only if low-score misses dominate in traces |
| Vector store at scale (S2) | **Qdrant**, **pgvector**; AWS-native S3 Vectors | Not before about 20,000 passages per business; the in-memory shelf is fine until then |
| Model gateway: one API for Gemini, OpenRouter and others, with budgets and fallback (L3, C1) | **LiteLLM** | Would replace our key rotation and fallback code and give per-key budgets, which maps to the "customers" and "testing" spend buckets. Worth a spike, because provider code is plumbing we maintain ourselves |
| Long-term memory per customer | **Mem0** | Our conversation memory is enough today; revisit with the Lead agent |

## 5. Engineering hygiene

| Need | Tool |
|---|---|
| Studio screen tests (E3) | **Playwright** (smoke tests in practice mode) |
| Business dashboards for clients (leads per week, fallback rate) | **Metabase** or **Grafana** over exported rows; Studio's own cards are enough until clients ask |
| Durable multi-step workflows | **Temporal**: not needed. SQS + the sweeper, or Step Functions, cover our cases |

## 6. Look at, but do not adopt

**Dify, Flowise, Langflow, n8n AI nodes, Botpress, Rasa:** complete agent builders. Excellent to study for UI ideas (Studio's Build tab) and templates, but they would replace the thing that makes this platform different: immutable versions, the make-live gate, the four checks, and per-business caps. Running them beside ours means two sources of truth.
**LangChain, LlamaIndex, CrewAI, AutoGen:** frameworks. Our worker is small and fully tested; a framework would add dependencies and hide the prompts we want to control. Borrow ideas (query rewrite, routers), not the library.
**Open WebUI:** a chat front-end for people, not for customers.

## 7. Suggested order (smallest effort, clearest payoff)

1. **Langfuse** (one day, cloud tier): see real traces for a week; answers S1 and part of R3 with data.
2. **Promptfoo** in CI: our tests table as the case file; catches prompt regressions before publish.
3. **CloudWatch alarms** (already phase A) plus **Uptime Kuma** for an outside view.
4. **LiteLLM** spike: can it replace our provider rotation and give the two spend buckets? Decide with a half-day test.
5. **Lead capture type**: own `lead#` record first; connect **Twenty** or **n8n** as the export hatch when the first client asks.
6. **Cal.com** for the Booking type, **Firecrawl** for "build a shelf from a website" onboarding.
7. **Presidio** when the first lead data is stored; **Garak** monthly.

## 8. Questions only the owner can answer

- Which client types come next (clinic, school, shop, real estate)? That decides CRM vs booking first.
- Do clients want to own their data in a separate CRM, or is a Leads card in Studio enough?
- Are you ready to host one more service (Langfuse or n8n) on Railway, with the upkeep that implies?
- Licence comfort: n8n and similar "fair-code" licences have limits on offering them as a hosted service to others. Check before building clients on them.
