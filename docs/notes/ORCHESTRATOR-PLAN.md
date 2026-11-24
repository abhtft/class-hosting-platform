# Orchestrator agent: one WhatsApp number, several departments (plan, 2026-11-24)

**Status: O1 built 2026-11-24 (platform and a first Studio screen); O2 onwards not started.** It reuses what exists; new code is marked as such.

## 1. The need

A client has one WhatsApp number but several services behind it (for Margdarshak: the library, digital classes, other facilities). Customers write anything to that one number. The same shape is common in every domain (a clinic: appointments, reports, billing; a shop: products, orders, service). We want **one standard, configurable orchestrator**, not a custom build per client.

Constraint that decides the shape: **Chatwoot connects one agent bot to one inbox, and one WhatsApp number is one inbox.** So the orchestrator is the single bot on that inbox, and the departments live behind it.

## 2. The design in one picture

```
WhatsApp ──> Chatwoot inbox ──> webhook (receiver, as today) ──> worker
                                                                   │
                                   ┌──────── 1. Guard (global, no model) ───────┐
                                   │  hand-over words, distress, attacks, media │
                                   └────────────────────┬───────────────────────┘
                                   ┌──────── 2. Route (layered, cheapest first) ┐
                                   │  L0 rules: menu reply, keywords, "menu"     │
                                   │  L1 sticky: stay in the current department  │
                                   │  L2 meaning: question vector vs department  │
                                   │     examples (no extra model call)          │
                                   │  L3 small model: one JSON call, only when   │
                                   │     L0-L2 are not sure                      │
                                   │  L4 ask: a short menu, never a guess        │
                                   └────────────────────┬───────────────────────┘
                                   ┌──────── 3. Answer (the department's agent) ┐
                                   │  its live version: books, corrections,      │
                                   │  media, fallback, exactly as today          │
                                   └────────────────────┬───────────────────────┘
                                   ┌──────── 4. After ───────────────────────────┐
                                   │  hand-over to the department's Chatwoot     │
                                   │  team, label, trace with the route reason   │
                                   └─────────────────────────────────────────────┘
```

**Departments are ordinary agents.** The library agent, the classes agent: each keeps its own books, corrections, media, tests, versions and make-live gate, all built and tested the way `shop` is today. The orchestrator only decides *which one answers* and owns the things that are shared (greeting, menu, guard, hand-over routing).

## 3. Why this shape (and not a "supervisor agent" that thinks freely)

- **Deterministic first, model last.** Most messages in a conversation are follow-ups to the same topic; rules and the conversation's current department settle them for free. A model is asked only for the genuinely unclear ones. This is the "routing" workflow from Anthropic's *Building effective agents* and the "pre-classifier, then supervisor, structured output" pattern vendors now ship ([Kore.ai](https://docs.kore.ai/agent-platform/guides/multi-agent-orchestration), [Augment Code](https://www.augmentcode.com/guides/multi-agent-orchestration-architecture-guide)).
- **A free-thinking supervisor loop** (the model plans, calls sub-agents, merges answers) costs 3 to 5 model calls a message, adds seconds, and is hard to test. Not worth it for "which desk answers this".
- **Separate agents per department** reuse everything we have built and tested, let each department change without touching the others, and keep each prompt short (shorter prompts answer better and cost less).
- **Ask, do not guess.** A wrong department gives a confident wrong answer; a one-line menu costs the customer one tap.

## 4. Each layer in detail

### 4.1 Guard (global, before routing, no model)
Reuses `chatwoot_handover` (person asked, complaint, warm lead, stuck, media without words) plus the planned **distress** reason. These are checked on every message for the whole number, so a complaint about the library and one about classes behave the same. Attacks are not routed differently: each department's prompt already fences the customer's text, and the safety suite runs on the orchestrator end to end.

### 4.2 Route

| Layer | How | Cost | When it decides |
|---|---|---|---|
| **L0 rules** | A reply to the menu ("1", "2", or the department's name), the department's keywords (a short list per department, Hindi, Hinglish and English), "menu"/"main menu" | nothing | the message matches exactly one department |
| **L1 sticky** | The conversation's current department (kept in the conversation memory item, with its time); stays for follow-ups | nothing | a department was chosen in the last N minutes (setting, default 30) and L2/L3 do not see a clear switch |
| **L2 meaning** | The question's vector (already made for the book search) against each department's example questions (embedded once per version, cached) | nothing extra | the best department is above a threshold AND ahead of the second by a margin |
| **L3 small model** | One call to the cheapest model: department descriptions + the last turn + the message → JSON `{department, confidence, switch}`; `max_tokens` about 40; an answer outside the list is ignored | about 0.01 to 0.02 cents | only when L0 to L2 did not decide |
| **L4 ask** | A short menu: "Aap kis baare mein poochh rahe hain? 1. Library 2. Digital classes 3. Kuch aur" | nothing | L3 is unsure, or two departments are close |

A **topic switch** inside a sticky conversation ("aur library ka timing?") is caught by L0 keywords or by L2/L3 pointing clearly elsewhere; then the current department changes and the trace says why.

**Multi-topic messages** ("library fees and class timing"): version 1 answers the first department and offers the second ("Aap classes ke baare mein bhi poochh rahe the, bataun?"). No parallel fan-out: it doubles cost and makes one long confusing reply.

**A "General" department** answers what belongs to the business as a whole (address, owner, overall hours, what services exist) from a small shared shelf; it is also where L4 lands when the customer picks "Kuch aur".

### 4.3 Answer
The chosen department's **live version** answers through the existing `respond()` (books, corrections, media, fallback, memory). Conversation memory stays one per conversation, with each turn tagged by department, so a department sees the recent turns of its own topic.

### 4.4 After the answer
- **Hand-over goes to the right people:** each department names a Chatwoot **team**; the worker assigns the conversation to that team (`POST /conversations/{id}/assignments` with `team_id`, [Chatwoot API](https://developers.chatwoot.com/api-reference/conversation-assignments/assign-conversation)) and adds a private note saying the department and the reason. To verify on our Chatwoot: what happens when both `assignee_id` and `team_id` are sent (send only `team_id`).
- **Label** the conversation with the department (Chatwoot labels), so the team can filter.
- **Trace** every turn with the route layer (L0 to L4), the scores and the department.

## 5. Which model where

| Job | Model | Why |
|---|---|---|
| Guard, L0, L1, L2 | none | rules and vectors decide most turns for free |
| L3 router | the cheapest model (today Gemini 3.5 Flash-Lite), JSON only, very short | a classification of a few words |
| Department answer | the cheapest model by default; a department may name a stronger one | most answers are "find it in the books and say it briefly" |
| Escalation (later) | a stronger model, only for a turn the cheap one failed (answer came back "not sure" while the books scored high, or a calculation across departments) | pay for strength only where it changes the outcome |
| Offline work (consistency check, fill from the books, teach distillation, suite runs) | a mid model is allowed | not on the customer's clock; quality matters more than a cent |

Today the platform prices only Flash-Lite (`gemini.DEFAULT_PRICES`); a second model is one price entry plus a setting, and Bedrock waits for the AWS support case.

**Rough cost per customer message** (Flash-Lite at USD 0.30 / 2.50 per million tokens): answer about 0.1 cent as today; L3 router about 0.01 to 0.02 cent, needed on perhaps one turn in four or five. **Orchestration adds well under 10 percent** to today's cost. Latency: L0 to L2 add milliseconds; L3 adds about 0.5 to 1 second on the turns that need it.

## 6. Configuration (generic, set in Studio)

An agent of type `orchestrator` (the registry already has `type` and `purpose` fields) has, in its version:

```json
{
  "greeting": "Namaste! Margdarshak mein swagat hai. Library, digital classes ya kuch aur?",
  "menu_prompt": "Aap kis baare mein poochh rahe hain?",
  "sticky_minutes": 30,
  "departments": [
    {"id": "library", "label": "Library", "agent": "library", "team_id": 4,
     "description": "Reading room seats, timings, fees, membership", "keywords": ["library", "seat", "padhai ki jagah"],
     "examples": ["library ki fees kitni hai?", "seat available hai?"]},
    {"id": "classes", "label": "Digital classes", "agent": "classes", "team_id": 5, "description": "...", "keywords": ["class", "batch", "course"], "examples": ["..."]},
    {"id": "general", "label": "Kuch aur", "agent": "general", "team_id": null, "description": "address, owner, all services", "keywords": [], "examples": []}
  ],
  "handover_text": "...", "fallback_text": "..."
}
```

Department agents are created as agents of type `department` with **no inbox of their own** (new: the registry today requires at least one inbox; a department is reached only through an orchestrator of the same business).

## 7. Testing (the parts we already trust, extended)

- **Routing table (new):** rows of "customer message → expected department or `ask`", including Hinglish, typos, switches in the middle of a conversation and multi-topic messages. Run on a version or the draft, like Your tests. Shows the layer that decided. Pass rule: the department matches; a message marked "ask" must not be answered by a department.
- **Department tests:** each department keeps its own tests, safety and repeatability, unchanged.
- **Safety suite** runs on the orchestrator end to end (attacks must not be routed into a department that obeys them).
- **Repeatability for routing:** the same message asked 3 times must land in the same department.
- **Consistency across departments:** shared facts (address, owner, phone) are checked across the orchestrator and all its departments (the consistency check, extended to several versions).

## 8. Phases and "done"

| Phase | Work | Done when |
|---|---|---|
| **O1 Departments** | Agent type `department` without an inbox; an orchestrator version that lists departments; the worker runs the guard, L0 rules, L1 sticky and L4 menu, then calls the department's live version; hand-over to the department's team with a label; trace of the route | On the test inbox, "1" goes to the library agent, a follow-up stays there, "menu" brings the menu back, "kisi se baat karni hai" reaches the library team |
| **O2 Meaning and the small model** | L2 with department examples (vectors cached per version); L3 one JSON call with the cheapest model; switch detection | A routing table of 40 rows (Hinglish included) passes 95 percent; the trace shows that most turns did not call the router model |
| **O3 Studio** | Orchestrator screens: Departments card (pick an agent, label, keywords, examples, team), Routing tests table, Activity shows the department and the layer | The owner builds the Margdarshak orchestrator from Studio without the command line |
| **O4 Shared facts and checks** | General department; consistency across departments; safety suite on the orchestrator; routing repeatability | A clash of the address between two departments is caught before Publish |
| **O5 Later, by evidence** | Escalation to a stronger model for failed turns; interactive WhatsApp lists (Chatwoot's `input_select` is reported to work on WhatsApp, [discussion](https://www.github.com/chatwoot/chatwoot/discussions/8140): verify on our version first); multi-topic answers | Only when traces show the need |

Each phase updates `docs/`, gets its smoke checks, and is deployed through a change set you approve.

## 9. Decisions for the owner

1. **Departments as separate agents** (recommended: reuse, separate testing, short prompts) or one agent with several shelves (simpler, but every change touches all topics and one prompt grows long).
2. **Menu style in version 1:** numbered text (recommended: works everywhere) or WhatsApp interactive lists (nicer, to be verified on our Chatwoot).
3. **Sticky time:** 30 minutes (recommended) or until the customer says "menu".
4. **Human teams per department in Chatwoot:** create one team per department (recommended) or one team for all with labels only.
5. **First client set-up:** Margdarshak with Library, Digital classes and General (recommended as the pilot).

## Sources
- [Chatwoot: assign a conversation (agent or team)](https://developers.chatwoot.com/api-reference/conversation-assignments/assign-conversation)
- [Chatwoot discussion: interactive messages on WhatsApp (`input_select`)](https://www.github.com/chatwoot/chatwoot/discussions/8140)
- [Kore.ai: multi-agent orchestration, pre-classifier before the supervisor](https://docs.kore.ai/agent-platform/guides/multi-agent-orchestration)
- [Augment Code: hybrid routing and structured routing decisions](https://www.augmentcode.com/guides/multi-agent-orchestration-architecture-guide)
- [LangGraph supervisor vs swarm trade-offs](https://dev.to/focused_dot_io/multi-agent-orchestration-in-langgraph-supervisor-vs-swarm-tradeoffs-and-architecture-1b7e)
- Anthropic, *Building effective agents* (routing and orchestrator-worker workflows)
