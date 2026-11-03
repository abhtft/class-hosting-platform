"""EXAMPLE routes for the AI patterns (see PATTERNS.md): answer from books, feedback, and a tool-using agent that needs your approval before it saves.
Read it, copy what your app needs, and delete the rest (and the `import study` lines in app.py) when you do not need it. It is YOUR file: edit it freely.

  POST /study          {"question": "...", "collections": ["library/science"]?}   -> answer from the books, with sources ("The books do not say." when unsure)
  POST /feedback       {"item": "<answer_id>", "rating": "up"|"down", "comment"?}   -> thumbs from your visitors
  GET  /feedback                                                                       -> {"up", "down", "recent_down": [...]}
  POST /agent          {"goal": "..."}                                                  -> a tool-using agent: status, answer, trace (and run_id when it waits for you)
  POST /agent/approve  {"run_id": "...", "approve": true|false}                         -> continue a run that was waiting for approval

Everything works on your computer too (npm run dev): books are read from .class-knowledge/ and the agent follows the scripted `MOCK` replies below, so it is free.
On the platform it needs the teacher to have switched class knowledge (books), class storage (cache, feedback, waiting runs) and the class AI on.
"""
import time
import uuid

import class_agent as agent
import class_knowledge as kb
import class_quality as quality
import class_storage as storage
from class_ai import AiError

BOOKS = None  # None searches everything you can see; or a fixed list you choose, e.g. ["library/science"]. NEVER take this from the visitor.
RUNS = "agent-runs"
RUN_ID_LENGTH = 24


def tools():
    """What the agent may do. Keep it to 2 to 4 small, safe tools. A tool that changes something needs a human's approval first."""
    return [
        agent.tool("search_books", "Find passages in the books", {"query": "text"},
                   # the section is the label of the passage ("CHIEF ADVISOR"): without it the model sees names with no idea which is which
                   lambda a: [{"title": h["title"], "page": h["page"], "section": h["section"], "text": h["text"][:600]} for h in kb.search(a["query"], BOOKS, 3)["hits"]]),
        agent.tool("save_note", "Save a short note for the student", {"text": "text"},
                   lambda a: storage.put("notes", f"{int(time.time() * 1000):013d}", {"text": a["text"][:200]}) and "saved", needs_approval=True),
    ]


def mock_script(goal):
    """What the agent 'hears' on your computer, where the class AI is only an offline mock: a fixed script, so you can try the whole flow for free."""
    if "save" in goal.lower():
        return [{"tool": "save_note", "args": {"text": goal[:100]}}, {"final": "I saved your note."}]
    return [{"tool": "search_books", "args": {"query": goal[:100]}}, {"final": "[mock agent] I searched the books for: " + goal[:60]}]


def _fail(error):
    status = 503
    body = {"error": getattr(error, "kind", "error"), "message": getattr(error, "message", None) or str(error)}
    return status, body


def route(method, path, body):
    """(status, data) for a route of this file, or None when the path is not one of them."""
    try:
        if (method, path) == ("POST", "/study"):
            question = str(body.get("question", "")).strip()
            if not question or len(question) > 500:
                return 400, {"error": "bad_request", "message": "Send a question of 1 to 500 characters."}
            reply = quality.answer_cached(question, BOOKS, days=7)  # the same question is answered once; a changed book is never served an old answer
            return 200, {**reply, "answer_id": quality.cache_key("answer", question)}
        if (method, path) == ("POST", "/feedback"):
            rating = body.get("rating")
            if rating not in ("up", "down"):
                return 400, {"error": "bad_request", "message": 'rating must be "up" or "down".'}
            return 200, {"saved": quality.feedback(str(body.get("item", ""))[:80], rating, str(body.get("comment", "")))}
        if (method, path) == ("GET", "/feedback"):
            return 200, quality.feedback_summary()
        if (method, path) == ("POST", "/agent"):
            goal = str(body.get("goal", "")).strip()
            if not goal or len(goal) > 300:
                return 400, {"error": "bad_request", "message": "Send a goal of 1 to 300 characters."}
            result = agent.run(goal, tools(), max_steps=5, max_seconds=16, mock=mock_script(goal))
            return 200, _publish(result)
        if (method, path) == ("POST", "/agent/approve"):
            run_id = str(body.get("run_id", ""))
            if len(run_id) != RUN_ID_LENGTH or not run_id.isalnum():
                return 400, {"error": "bad_request", "message": "Unknown run."}
            state = storage.get(RUNS, run_id, default=None)
            if not state:
                return 404, {"error": "not_found", "message": "That run is not waiting any more."}
            storage.delete(RUNS, run_id)  # a run is approved or declined once
            result = agent.resume(state, bool(body.get("approve")), tools(), max_steps=5, mock=[{"final": "Done."}])
            return 200, _publish(result)
    except (AiError, kb.KnowledgeError, storage.StorageError) as error:  # not switched on, allowance used, full... a friendly message and a kind
        return _fail(error)
    return None


def _publish(result):
    """What the page may see: never the internal state. A run that waits for approval is kept in class storage under a run id the page sends back."""
    state = result.pop("state", None)
    if result["status"] == "needs_approval":
        run_id = uuid.uuid4().hex[:RUN_ID_LENGTH]
        storage.put(RUNS, run_id, state)
        result["run_id"] = run_id
    return result
