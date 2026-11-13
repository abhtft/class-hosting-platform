# Chapter 10: LLM integration: the class AI connector, OpenRouter, Gemini and cost control

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> Code: `lambda/ai.py`, `lambda/openrouter.py`, `lambda/gemini.py`, `lambda/ai_proxy.py`, student helpers `student/class_ai.py` / `class_ai.mjs`.

## A. LLM API basics you need first

**Q331 [B] What is a "chat completion" request?**
You send a list of messages (`system`, `user`, `assistant`), a model name and parameters; the model returns the next assistant message plus token counts. The messages array *is* the whole memory: the API is stateless.
Study: [OpenAI Chat Completions reference](https://platform.openai.com/docs/api-reference/chat), [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)

**Q332 [B] What is a token and why does cost depend on tokens?**
Models read/write sub-word pieces ("tokens"; roughly ¾ of an English word). Providers bill per million input tokens and per million output tokens, at different prices. Output tokens typically cost several times input tokens.
Study: [Tokenizer playground (OpenAI)](https://platform.openai.com/tokenizer), [Karpathy: Let's build the GPT Tokenizer (YouTube)](https://www.youtube.com/watch?v=zduSFxRajkE)

**Q333 [B] What is a context window?**
The maximum number of tokens (input + output) a model can handle in one call. Long inputs cost more and may degrade quality ("lost in the middle").
Study: [Lost in the Middle (paper)](https://arxiv.org/abs/2307.03172)

**Q334 [I] What does "OpenAI-compatible" mean and why does the platform rely on it?**
Many providers (OpenRouter, Gemini's compat endpoint, local servers) accept the same JSON shape at `/v1/chat/completions`. The student's function gets `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AI_MODEL`, so any OpenAI SDK or a plain `fetch` works and the provider can be switched by the teacher without code changes.

**Q335 [I] What are `temperature`, `max_tokens` and `stream`?**
`temperature` = randomness; `max_tokens` = output cap (bounds cost and time; platform default cap `ai.maxTokens` 2048); `stream` = send partial tokens as they're produced. The Gemini proxy refuses `stream=true` (non-streamed only); Lambda response streaming isn't used here.

**Q336 [I] What is a system prompt versus user prompt?**
The system prompt sets role/rules; user messages carry the task. Models treat them differently but neither is a security boundary; hostile text in user or retrieved content can still steer the model (Chapter 12).

**Q337 [I] What are structured (JSON) answers and why are they fragile?**
Asking the model to output JSON for code to parse. Models may add prose, markdown fences or break syntax. Defences: clear schema in the prompt, parse defensively, retry/repair, provider "JSON mode"/schema features. The kit rules have a "Structured AI answers (JSON)" section.

## B. Class AI architecture

**Q338 [B] What problem does the AI connector solve?**
Students' apps need an AI, but giving every student the teacher's key would be unsafe and unmetered. The connector gives each student their *own capped, revocable key* (or a metered proxy) controlled by the teacher.

**Q339 [I] How does the OpenRouter path work end to end?**
Teacher switches AI on for a student → portal calls OpenRouter's key-management API with the **management key** (Secrets Manager) to create a key with a dollar `limit` and `limit_reset` → stores hash + key on the token item → injects `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AI_MODEL` into the student's function → the function calls OpenRouter **directly** (the platform never sees prompts or answers).
Repo: `docs/AI_CONNECTOR.md` section 3.

**Q340 [I] Why does the platform never see prompts and answers on the OpenRouter path?**
Privacy and cost: no proxy to run, no prompt storage. Trade-off: the platform can only see *usage numbers* via the provider, with a lag.

**Q341 [I] What is OpenRouter?**
A router/aggregator with one API and one bill for many models (Anthropic, OpenAI, Google, open models). It also offers per-key spend limits, which this design uses.
Study: [OpenRouter docs](https://openrouter.ai/docs), [Key management API](https://openrouter.ai/docs/api-reference/api-keys/create-api-key)

**Q342 [I] Why can Gemini not use the same approach, and what is the proxy?**
Google doesn't offer per-student capped keys, so students never receive the Google key. `ai_proxy.py` runs behind its own API Gateway: the student gets `ga_<slug>.<secret>` and the proxy's URL; it checks the key (constant time), token active, teacher switch, provider is Gemini, cap not reached; then calls Google with the real key and adds the cost to the student's counter.
Study: [Gemini API docs](https://ai.google.dev/gemini-api/docs), [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)

**Q343 [I] Why must the proxy force the model server-side?**
So a student cannot request a more expensive model. The model is always the one the teacher set.

**Q344 [I] How is cost computed for Gemini (including "thinking" tokens)?**
`cost_micro = prompt_tokens × price_in + output_tokens × price_out` where `output = total − prompt` because Gemini's "thinking" tokens appear in `total_tokens` but not in `completion_tokens`. Prices are hard-coded (CRITICAL_ISSUES item 12: will go stale).

**Q345 [I] Why is OpenRouter's per-key limit "the provider enforces the limit, not our code"?**
Even if the platform has a bug or the student bypasses the platform, the provider still refuses calls past the cap. Security controls are strongest where you don't own the failure mode.

**Q346 [I] What stops a student turning AI off by overwriting the env variables?**
The three names are reserved while AI is on (HTTP 400 on env updates).

**Q347 [I] What happens when a token is revoked or expires?**
The AI key is **disabled first** (before anything else), the sweeper retries failures. "Safe order of operations": switching off disables before updating anything.

**Q348 [I] Why is usage "lagging" and what is the effect?**
The provider's usage numbers update after calls; a burst can overshoot a cap by a few calls. The cap default is small ($0.25) so overshoot is bounded.

**Q349 [I] Why does the AI timeout chain run 25 s → 29 s → 30 s (now 27 s)?**
Each layer must time out *after* the one inside it so the inner layer can return a clean error: function (27) < gateway Lambda (29) < API Gateway (30). If the outer layer times out first the user sees an opaque 504.

**Q350 [D] Why is there an `ai.typicalCallUsd` setting?**
Used to estimate how many calls a cap covers (UI/hints), not enforcement.

**Q351 [I] Why money in "micro-dollars" and a test store that fails on floats (`StrictStore`)?**
To prove no float ever reaches DynamoDB (it rejects them). See Chapter 4.

## C. The student helper `class_ai`

**Q352 [I] What does `class_ai.chat()` do for the student?**
Wraps the OpenAI-compatible call with the env vars, a timeout fitted to the platform limit, error handling, and an offline mock locally. Students call one function instead of learning each provider's SDK.

**Q353 [I] Why must the browser never call the AI directly?**
The key would be in public JavaScript (anyone could steal and use it until the cap). The checker blocks AI service calls and `VITE_` secrets in the frontend; AI is called only from the backend.

**Q354 [I] How do you handle provider errors, rate limits and retries?**
Treat 429/5xx as transient: retry with exponential backoff + jitter, cap attempts, surface a friendly message. Never retry non-idempotent paid calls blindly.
Study: [Timeouts, retries and backoff with jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/)

**Q355 [D] How would you add prompt caching or response caching?**
Response cache keyed by hash of (model, messages, params) in class storage with a TTL; provider-side prompt caching reduces input cost for long repeated prefixes. `kb.answer` already has a 7-day answer cache per question.
Study: [Anthropic prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)

**Q356 [D] How does model choice affect cost and quality, and where is it configured?**
The teacher sets `ai.defaultModel` (and per-student model). Cheaper/smaller models suffice for classification and short answers; reasoning-heavy tasks need larger ones. Measure before choosing (Chapter 11, Chapter 13).

## D. Generic LLM engineering questions

**Q357 [I] What is hallucination and what are three mitigations?**
The model states false things confidently. Mitigations: ground answers in retrieved sources and cite them; refuse when evidence is weak ("The books do not say."); constrain outputs and verify with code.
Study: [Anthropic: Reduce hallucinations](https://docs.anthropic.com/en/docs/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)

**Q358 [I] How would you evaluate an LLM feature?**
Build a small golden set of inputs with expected properties, run it on every change, track pass rate/cost/latency, add LLM-as-judge or human review for fuzzy criteria. The project's `starter/template/evals/study.json` and `class_quality` are the beginning of this.
Study: [Hamel Husain: Your AI product needs evals](https://hamel.dev/blog/posts/evals/)

**Q359 [I] What is prompt injection and why is it the #1 LLM risk?**
Untrusted text (a web page, a retrieved passage, a user message) contains instructions the model obeys. Because instructions and data share one channel, you can't fully prevent it; you limit the damage (least privilege tools, approval steps, fences, no secrets in context).
Study: [OWASP Top 10 for LLM Apps](https://genai.owasp.org/llm-top-10/)

**Q360 [D] Why must you never put secrets in a prompt or a tool result?**
Prompts go to the AI provider and may be logged, and models can repeat them. Agent helper docs say: "Never put a secret in a tool's result or in the goal".

## Study materials for this chapter

- **Andrej Karpathy: "Intro to Large Language Models"** (1-hour YouTube talk), and "Let's build GPT".
- **3Blue1Brown: "But what is a GPT?"** (YouTube).
- **Anthropic docs and cookbook**: <https://docs.anthropic.com>, <https://github.com/anthropics/anthropic-cookbook>
- **OpenAI cookbook** (patterns, evals): <https://cookbook.openai.com/>
- **Jay Alammar, The Illustrated Transformer**: <https://jalammar.github.io/illustrated-transformer/>
- **Chip Huyen, *AI Engineering*** (book) and her blog <https://huyenchip.com/blog/>
- **Eugene Yan: Patterns for building LLM-based systems**: <https://eugeneyan.com/writing/llm-patterns/>
- Hands-on: read `lambda/ai.py` and `tests/test_ai.py` (revoke, expiry, invalid input), then `lambda/gemini.py` for cost maths.
