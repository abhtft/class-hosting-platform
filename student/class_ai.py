"""class_ai: use the class AI with two lines. This file belongs to the platform kit (do not edit it; it is refreshed with the kit).

    from class_ai import ask
    answer = ask("Summarise this shopping list")

How it works
------------
When your teacher switches the class AI on, the platform gives your function three environment variables:
OPENAI_API_KEY, OPENAI_BASE_URL and AI_MODEL. This module reads them and makes one request in the OpenAI-compatible format.
Standard library only: nothing to install.

* ``ask(prompt, system=None, max_tokens=300, model=None, default=None, mock=None)`` -> the answer text.
* ``chat(messages, max_tokens=300, model=None, default=None, mock=None)``          -> the same, with a full message list
  (for a conversation: send the earlier messages too, the AI remembers nothing between calls).
* ``available()``                                                      -> True when an answer can be produced (real or mock).
* On any problem it raises ``AiError`` with a friendly ``.message`` you can show, and a ``.kind``:
  ``not_enabled`` (the class AI is not on for you), ``cap_reached`` (your allowance is used up), ``disabled`` (your key is off),
  ``busy`` (the AI is overloaded, tried twice), ``timeout``, ``provider_error``.
  Pass ``default="Sorry, the AI is resting"`` to get that text back instead of an exception.
* On your own computer, with no variables set, it answers from a tiny OFFLINE MOCK (starts with "[mock AI]"), so you can build
  and test without spending anything. Set AI_MOCK=1 to force the mock anywhere, AI_MOCK=0 to forbid it.
* ``mock="..."`` is the text the offline mock returns instead of "[mock AI] ...". Use it when your code expects a certain shape,
  for example ``mock='[{"question": "2+2?", "choices": ["3", "4"], "answer": 1}]'``, so you can try the whole flow offline.
  It is ignored when the real AI answers.

Safety: the key is never printed, logged or put in an error. Never send it to the browser: your page calls your own /api route.
Your prompts go to the AI provider, so do not send private data.
"""
import json
import os
import secrets
import time
import urllib.error
import urllib.request

KIT_HELPER_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 20

MESSAGES = {
    "not_enabled": "The class AI is not switched on for you yet. Ask your teacher.",
    "cap_reached": "Your AI allowance is used up. Ask your teacher for more, or try again when it resets.",
    "disabled": "Your AI key is switched off (the AI was turned off, or your token is not active).",
    "busy": "The AI is busy right now. Please try again in a moment.",
    "timeout": "The AI took too long to answer. Try a shorter question.",
    "provider_error": "The AI service had a problem. Please try again.",
}


class AiError(Exception):
    """A failed AI call. ``kind`` is one of MESSAGES; ``message`` is safe to show to the user; ``status`` is the HTTP status or 0."""

    def __init__(self, kind, status=0):
        super().__init__(MESSAGES[kind])
        self.kind, self.status, self.message = kind, status, MESSAGES[kind]


def _mock_mode():
    flag = os.environ.get("AI_MOCK")
    if flag in ("1", "true"):
        return True
    if flag in ("0", "false"):
        return False
    # No key and not running on the platform (AWS Lambda): assume a laptop.
    return not os.environ.get("OPENAI_API_KEY") and not os.environ.get("AWS_LAMBDA_FUNCTION_NAME")


def mocking():
    """True when answers come from the offline mock (on your computer without a key, or AI_MOCK=1). class_agent uses it to run a scripted offline agent."""
    return _mock_mode()


def available():
    return _mock_mode() or bool(os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_BASE_URL"))


def _mock_answer(messages):
    last = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    return f"[mock AI] You asked: {str(last)[:120]}"


def _classify(status):
    if status == 401:
        return "disabled"
    if status in (402, 403):
        return "cap_reached"
    if status == 429 or status >= 500:
        return "busy"
    return "provider_error"


# ---------- traces: one answer = one trace (the teacher's Traces view). Best effort: a failure here can never change an answer. ----------
_trace = {"id": None, "spans": []}


def trace_begin(trace_id=None):
    """Start (or continue, with an id from a saved run) the trace of one answer. Calls to the class AI and the books made after this carry its id."""
    ok = isinstance(trace_id, str) and len(trace_id) == 19 and trace_id.startswith("tr_") and all(c in "0123456789abcdef" for c in trace_id[3:])
    _trace["id"], _trace["spans"] = (trace_id if ok else "tr_" + secrets.token_hex(8)), []
    return _trace["id"]


def trace_id():
    return _trace["id"]


def trace_span(name, kind="other", attrs=None, ms=0, status="ok"):
    """Note one step of your own (the platform already records the AI call and the book search). Sent with trace_flush()."""
    if _trace["id"] and len(_trace["spans"]) < 30:
        _trace["spans"].append({"name": str(name)[:60], "kind": kind, "attrs": attrs or {}, "dur_ms": int(ms), "status": status, "ts": int(time.time() * 1000) - int(ms)})


def trace_flush():
    """Send the noted steps (one small request, 2 seconds at most) and end the trace. Never raises."""
    spans, tid = _trace["spans"], _trace["id"]
    _trace["id"], _trace["spans"] = None, []
    url, key = os.environ.get("CLASS_KNOWLEDGE_URL", ""), os.environ.get("CLASS_KNOWLEDGE_KEY", "")
    if not (tid and spans and url and key) or _mock_mode():
        return tid
    try:
        request = urllib.request.Request(url.rstrip("/") + "/v1/trace", data=json.dumps({"trace_id": tid, "spans": spans}).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        urllib.request.urlopen(request, timeout=2).close()
    except Exception:
        pass
    return tid


def _request(url, key, payload, timeout):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if _trace["id"]:
        headers["x-trace-id"] = _trace["id"]
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8").strip())


def chat(messages, max_tokens=300, model=None, default=None, temperature=None, mock=None):
    """Send a list of {"role", "content"} messages and return the answer text (see the module docstring for errors)."""
    try:
        if _mock_mode():
            return mock if mock is not None else _mock_answer(messages)
        key, base = os.environ.get("OPENAI_API_KEY"), os.environ.get("OPENAI_BASE_URL")
        if not key or not base:
            raise AiError("not_enabled")
        payload = {"model": model or os.environ.get("AI_MODEL") or "", "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            payload["temperature"] = temperature
        url = base.rstrip("/") + "/chat/completions"
        timeout = float(os.environ.get("AI_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
        started, last = time.time(), None
        for attempt in (1, 2):
            try:
                data = _request(url, key, payload, max(timeout - (time.time() - started), 1))
                break
            except urllib.error.HTTPError as e:
                last = AiError(_classify(e.code), e.code)
                if last.kind != "busy" or attempt == 2 or time.time() - started > timeout - 3:
                    raise last
                time.sleep(0.6)
            except (TimeoutError, OSError) as e:  # URLError, socket timeouts, connection resets
                timed_out = isinstance(e, TimeoutError) or "timed out" in str(e).lower()
                raise AiError("timeout" if timed_out else "provider_error")
            except ValueError:
                raise AiError("provider_error")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AiError("provider_error")
        return "" if content is None else str(content)
    except AiError as error:
        print(f"class_ai: {error.kind} (status {error.status})")  # kind and status only: never the key, prompt or answer
        if default is not None:
            return default
        raise


def ask(prompt, system=None, max_tokens=300, model=None, default=None, temperature=None, mock=None):
    """Ask one question and return the answer text."""
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": str(prompt)}]
    return chat(messages, max_tokens=max_tokens, model=model, default=default, temperature=temperature, mock=mock)
