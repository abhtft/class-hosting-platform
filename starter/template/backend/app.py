"""Starter backend (Python 3.12) for the class platform. Replace the routes with your own.

The contract: define handler(event, context). `event` is an API Gateway HTTP API (payload v2) event:
  event["rawPath"]                         the path AFTER /api, e.g. "/health" (the site calls /api/health)
  event["rawQueryString"]                  text after "?"
  event["requestContext"]["http"]["method"]
  event["body"], event["isBase64Encoded"]  the request body as a string
Return {"statusCode": ..., "headers": {...}, "body": "<string>"}.

Rules (see AGENTS.md): standard library only (nothing is pip-installed on the platform), at most 10 s per request (25 s while the
class AI is on), secrets only in the portal's "Environment variables" (never in code or a .env file inside backend/).

Run it on your computer with `npm run dev` (or `npm run dev -- --backend-only`).
"""
import base64
import json
import time
from urllib.parse import parse_qs

import class_storage as storage  # the platform's helper for saving data (needs the teacher to switch class storage on)
from class_ai import AiError, ask  # the platform's helper for the class AI: two lines are all you need


def respond(status, data):
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(data)}


def read_json(event):
    """The request body as a dict ({} when empty or not JSON)."""
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def handler(event, context):
    method = event["requestContext"]["http"]["method"]
    path = event["rawPath"]

    if method == "GET" and path == "/health":
        return respond(200, {"ok": True})

    if method == "GET" and path == "/hello":
        name = parse_qs(event.get("rawQueryString", "")).get("name", ["world"])[0]
        return respond(200, {"message": f"Hello, {name}!"})

    if method == "POST" and path == "/echo":
        return respond(200, {"youSent": read_json(event)})

    if method == "POST" and path == "/ask":
        prompt = str(read_json(event).get("prompt", "")).strip()
        if not prompt or len(prompt) > 500:
            return respond(400, {"error": "bad_request", "message": "Send a prompt of 1 to 500 characters."})
        try:
            # THE class AI call. `ask` returns the answer text, or raises AiError with a friendly message.
            answer = ask(prompt, system="You are a friendly helper for a school project. Answer in at most three sentences.", max_tokens=200)
        except AiError as error:
            return respond(503, {"error": error.kind, "message": error.message})
        return respond(200, {"answer": answer})

    if path == "/notes" and method in ("GET", "POST"):
        try:
            if method == "GET":  # the newest 20 notes (ids start with the time, so they come back oldest first)
                return respond(200, {"notes": [row["value"] for row in storage.items("notes")][-20:]})
            text = str(read_json(event).get("text", "")).strip()
            if not text or len(text) > 200:
                return respond(400, {"error": "bad_request", "message": "Send a note of 1 to 200 characters."})
            note = {"id": f"{int(time.time() * 1000):013d}", "text": text}
            storage.put("notes", note["id"], note)  # THE storage call: collection, id, any JSON
            return respond(201, note)
        except storage.StorageError as error:  # not switched on, full, allowance used... a friendly message and a kind
            return respond(503, {"error": error.kind, "message": str(error)})

    return respond(404, {"error": "not_found", "message": "No such route."})
