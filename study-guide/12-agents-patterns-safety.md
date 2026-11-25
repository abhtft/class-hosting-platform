# Chapter 12: AI patterns and agents: routers, tool-using agents, approval, quality loops and LLM safety

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> Code: `student/class_agent.py` / `.mjs`, `student/class_quality.py` / `.mjs`, `starter/template/PATTERNS.md`, `starter/template/backend/study.py`. Read `docs/AGENT_GUIDE.md` alongside.

## A. Patterns

**Q401 [B] Name the AI patterns in `PATTERNS.md`, from simplest to most complex.**
1) A plain call. 2) Answer from books (RAG). 3) A router. 4) A tool-using agent. 5) Plan-and-do as a background job. 6) Human approval. 7) The quality loop (measure and improve). Rule of thumb: use the simplest that works; many "agents" are really a plain call.
Study: [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

**Q402 [B] What is the difference between a workflow and an agent?**
Workflow: code decides the steps (the LLM fills in parts). Agent: the model decides the next step in a loop, using tools. Workflows are more predictable and cheaper; agents handle open-ended tasks. This platform defaults to the former.

**Q403 [I] What is a router and why is it safer than an agent?**
One short model call picks one name from a list the student wrote. The model never runs anything; unexpected output falls back to a safe default action.

**Q404 [I] What is ReAct (reason + act)?**
A prompting pattern where the model alternates reasoning and actions (tool calls), observing each result. `class_agent` is a "JSON-step" ReAct-style loop.
Study: [ReAct paper](https://arxiv.org/abs/2210.03629), [Lilian Weng: LLM Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/)

**Q405 [I] Why did `class_agent` use JSON replies instead of native tool calling?**
Works with any chat model; needs no provider feature; easy to test with a scripted model; easy to store the trace. Native tool calling differs per provider, and Gemini failed outright on the first prompt style (`MALFORMED_FUNCTION_CALL`).

**Q406 [I] Why not LangChain/LangGraph?**
Heavy dependencies; student functions are standard-library only; the bounds needed (steps, seconds, tokens, approval, JSON state) are small to write and fully tested. A framework would also hide the guardrails the students should understand.

**Q407 [I] Describe one run of the agent loop.**
Build system prompt (tools as exact JSON lines, rules, step limit) → budget check → call model (retry once on busy) → parse the first JSON object → `final`: done; `tool`: validate (real tool, args, repeat, uses so far) → needs approval? stop → else run tool, cut result, fence as `<tool_result>` data, loop.

**Q408 [I] List the guardrails and the failure each stops.**
Only declared tools (invented tools); argument check (bad/oversize args); no repeated call (loops); use limit per tool (3); step budget (6); time budget (20 s); token budget (8,000 est.); result size cut (1,500 chars); failing tools hide stack traces; fenced results (injection); invalid reply (nudge once); provider hiccup (retry once); human approval (side effects); final answer size cut.

**Q409 [I] How is "approval" implemented without keeping a process alive?**
A tool marked `needs_approval` stops the run with status `needs_approval`, returning a plain-JSON `state` and what it wanted to do. The app stores that state (class storage, collection `agent-runs`) and later `resume(state, approve=True/False)`. Serverless-friendly: no waiting server.

**Q410 [I] Why does resume give a fresh budget?**
A live test found a run resumed after approval had no budget left. The human delay shouldn't consume the model's allowance.

**Q411 [I] What did the real tests teach about "approval" wording?**
The model read "[needs a human's approval first]" as "I may not use this tool". Wording changed to "the program asks the person, so request it normally". Prompt wording matters and must be tested on real models.

**Q412 [I] Why show the exact text and evidence on the approval card?**
A real run once saved a wrong fact (a second name) because the passage's section label was missing. Humans can only approve what they can see; fix the root cause (label shown) and show evidence.

**Q413 [I] What's a scripted (mock) model test and why is it powerful?**
Tests pass planned replies so every guard and status is exercised for free, deterministically, in Python and Node. Test the things the agent *must refuse* first.

## B. The quality loop

**Q414 [I] What is `class_quality` for?**
Helpers to measure answer quality: retrieval metrics (`evaluate_retrieval`: recall, MRR, refusal), answer checks (`npm run eval`), and a loop to compare changes. It's the "measure first, change second" tooling.

**Q415 [I] What is LLM-as-judge and what are its pitfalls?**
Using a model to grade outputs. Pitfalls: bias to its own style, verbosity, position bias, inconsistency, and cost; always spot check with humans and keep deterministic checks where possible.
Study: [Judging LLM-as-a-Judge (paper)](https://arxiv.org/abs/2306.05685)

**Q416 [D] What's the difference between offline evals and online monitoring?**
Offline: fixed test set run before release. Online: traces of real usage, refusal/error rates and drift alarms after release (see Chapter 13: traces and daily rollups with `driftRefusalJump`).

## C. LLM safety and security

**Q417 [B] What is prompt injection (direct and indirect)?**
Direct: the user tells the model to ignore its rules. Indirect: hostile instructions hidden in data the model reads (web page, PDF passage, tool result). Indirect is more dangerous because the user never sees it.
Study: [Simon Willison: Prompt injection explained](https://simonwillison.net/2023/May/2/prompt-injection-explained/), [OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)

**Q418 [I] Why can't fencing (`<tool_result>`) fully solve injection?**
The fence is itself text; a strong enough attack can still persuade the model. It lowers risk (and `<`/`>` escaping stops fence-breaking) but real safety comes from limiting what a tool can do and requiring human approval for side effects.

**Q419 [I] How does the platform apply "least agency"?**
Tools are written by the student, so the model only chooses among them. The platform sandbox bounds the damage: no AWS credentials, capped AI key, capped storage, capped searches. Even a fully hijacked agent can only spend within caps.

**Q420 [I] What are other OWASP LLM Top 10 items relevant here?**
Sensitive information disclosure (secrets in prompts), excessive agency, insecure output handling (rendering model HTML), unbounded consumption (cost/DoS → caps), supply chain (add-ons/models), overreliance (show sources, refuse).
Study: [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/)

**Q421 [I] Why must model output be treated as untrusted input?**
It may contain HTML/JS, SQL, shell commands or malformed JSON. Escape when rendering; parse defensively; never execute model-generated code with real permissions.

**Q422 [D] What's the "data you don't own" problem for RAG answers (medical/legal)?**
Answers can look authoritative. Always show sources, add "not advice" lines, prefer refusing, and keep a human reviewer for consequential use (see RAG_GUIDE domain notes).

**Q423 [D] What would an agent "memory" feature need to be safe?**
A defined store, per-user isolation, retention/erase rules, size caps, and protection against memory poisoning (injected facts that persist). Not built.

**Q424 [D] Why is "background jobs + approval" listed as not yet combined?**
A job runs without a human present; an approval must pause for a person. The proposed design lets a job *return* the request for the app to ask.

## Study materials for this chapter

- **Anthropic: Building effective agents** <https://www.anthropic.com/engineering/building-effective-agents>
- **OWASP GenAI Security Project** <https://genai.owasp.org/>
- **Lilian Weng: Agents** <https://lilianweng.github.io/posts/2023-06-23-agent/>
- **Simon Willison's prompt injection series** <https://simonwillison.net/series/prompt-injection/>
- **Hamel Husain: evals** <https://hamel.dev/blog/posts/evals/>, **Eugene Yan** <https://eugeneyan.com/writing/llm-patterns/>
- YouTube (search): "Building agents (Anthropic)", "LLM security: prompt injection explained", "Andrew Ng agentic workflows".
- Hands-on: read `student/class_agent.py` top to bottom; then `tests/test_class_agent.py` for each guardrail test.
