# AI patterns for this platform

Read this when the app uses the class AI for more than one plain answer. It tells you which pattern to pick, shows the smallest working version, and says what to
check before you deploy. **Start with the simplest pattern that works.** Every step up (more AI calls, more tools) costs allowance and time, and is harder to test.

The helpers are in `backend/` (Python) or `variants/node-backend/` (Node) and are refreshed by the platform: do not edit them. Python names are shown; Node uses the
same names in camel case (`class_agent.mjs`: `run`, `resume`, `tool`; `class_quality.mjs`: `cached`, `evaluate`...).

## Which pattern?

| The app must... | Use | Helper |
|---|---|---|
| answer one question, write one text | **A plain call** | `class_ai.ask` |
| answer from the class books or the student's own notes | **Answer from books** | `class_knowledge.answer` |
| choose between a few fixed actions from free text | **A router** | `class_ai.ask` + an allowlist |
| use several steps, where each step depends on the last | **A tool-using agent** | `class_agent.run` |
| do something that takes minutes | **Plan and do as a background job** | `class_agent.run` inside `run_job` |
| change or send something real | **Human approval** | `class_agent` with `needs_approval` |
| get cheaper, better and honest over time | **Quality loop** | `class_quality` |

## 1. A plain call

`ask(prompt, system="...", max_tokens=200)`. Give a role, the format you want, and a limit. If the answer must be one of a few words, say so and check it.

## 2. Answer from books

```python
import class_knowledge as kb
reply = kb.answer(question, collections=["library/science"])   # {"answer", "sources", "confident"}
```

Show the sources under the answer. When `confident` is false it already says "The books do not say.": show that, never fill the gap from memory.
Choose the collections in your code, never from what a visitor types.

## 3. A router

One short AI call picks **one name from a list you wrote**; your code then runs that action. The model never runs anything itself.

```python
ROUTES = {"quiz": make_quiz, "summary": summarise, "other": say_not_sure}
choice = ask(f"Pick ONE word from: {', '.join(ROUTES)}. Message: {text[:300]}", max_tokens=5).strip().lower()
action = ROUTES.get(choice, say_not_sure)          # anything unexpected falls back to the safe action
```

## 4. A tool-using agent

Use it only when the steps cannot be written down in advance. Keep **2 to 4 tools**, each small, boring and safe: the model chooses among them, your code
does the work.

```python
import class_agent as agent
tools = [
    agent.tool("search_books", "Find passages in the class books", {"query": "text"}, lambda a: kb.search(a["query"])["hits"][:3]),
    agent.tool("add_numbers", "Add two numbers", {"a": "number", "b": "number"}, lambda a: a["a"] + a["b"]),
]
result = agent.run(goal, tools, max_steps=5, max_seconds=16)   # a web request stops at 25 s: a real model needs 2 to 5 s per step, so keep this under 18
return respond(200, {"answer": result["answer"], "status": result["status"], "trace": result["trace"]})
```

What the helper does for you: it asks the model for one JSON step at a time, refuses unknown tools and wrong arguments, refuses a repeated call, cuts long results,
treats tool results as data (a web page that says "ignore your rules" is not obeyed), stops at the step, time and token budgets and says so honestly.
**Show the `trace` to the student**: it is how they see what the AI did. Always handle `status` other than `"done"`.

Rules for tools: they read or compute, they do not delete; a tool never receives a secret; a tool that changes something is a **human approval** tool (below).
A tool's result goes to the AI provider, so it must not hold private data.

## 5. Plan and do as a background job

An agent with many steps does not fit in a web request (25 seconds). If background jobs are on, run it as a job: the page starts the job and asks for the result.

```python
def run_job(job):
    if job["name"] == "research":
        r = agent.run(job["payload"]["goal"], tools, max_steps=12, max_seconds=50)   # stay under the minutes the teacher allowed
        return {"status": r["status"], "answer": r["answer"], "trace": r["trace"]}
```

## 6. Human approval

A tool made with `needs_approval=True` never runs by itself. The run stops; you store `result["state"]` (plain JSON) with class storage, show the student what
the agent wants to do, and call `agent.resume(state, approve=True|False, tools=tools)` when they answer. Do this for anything that saves, sends, deletes or
spends. The model cannot approve its own request; only a click from the student can.

**Show the person exactly what will happen, and what it is based on.** Show `result["pending"]["args"]` word for word (the exact note text, the exact recipient) and the `trace` of what the agent read. A model can pick the wrong line of a page: in a real test an agent searched the class books, read "CHAIRMAN ... J.V. Narlikar" and "CHIEF ADVISOR Rupamanjari Ghosh" on the same page, and asked to save a note naming the wrong person. Approval is where that is caught, so the page needs an Approve button, a Decline button and the text next to them. The starter has this card ready in `frontend/src/AgentPanel.tsx`. Each `resume` gets a fresh budget of steps, seconds and tokens, and a run can be approved only once.

## 7. The quality loop

```python
import class_quality as quality
text, hit = quality.cached("faq", question, lambda: ask(question), days=7)      # the same question, answered once
quality.feedback(answer_id, "down", "wrong page")                                # thumbs from your visitors
report = quality.evaluate(CASES, my_app)                                         # tests with a score, before every deploy
```

Write 8 to 15 cases (`{"input": ..., "contains": [...], "refuses": True}`) for the questions that matter, **including some the app should refuse**, run
`evaluate` before you deploy, and keep the cases in a file in the project so you can run them again after every change. For a book app also run
`quality.evaluate_retrieval(cases, collections)` to see whether the search finds the right page. Cache only answers that are the same for every visitor.

## Checklist before you deploy an AI app

- [ ] The simplest pattern that works. No agent where a plain call or a router does.
- [ ] Every loop has a limit (steps, seconds). The helper sets one; keep it.
- [ ] Every answer from books shows its sources and can say "I don't know".
- [ ] No tool can do something the student would not do themselves.
- [ ] Anything that changes or sends something waits for a human.
- [ ] You ran `evaluate` and read the failed cases, including the ones that must be refused.
- [ ] The page shows a friendly message for every `status` that is not `done` and for every `AiError` or `KnowledgeError`.
- [ ] No secret, no private data in a prompt, a tool result, the cache or a feedback comment.
