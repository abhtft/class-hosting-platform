"""Starter backend (Python 3.12). Deploy this folder from the portal's "Backend" card, then call it from your site:

    fetch('/api/hello')        -> this function, at the path /hello

The contract: define handler(event, context). `event` is an API Gateway HTTP API (payload v2) event:
  event["rawPath"], event["rawQueryString"], event["requestContext"]["http"]["method"], event["headers"], event["body"]
Return {"statusCode": ..., "headers": {...}, "body": "..."} (or just a value: a dict becomes a 200 JSON response).
Secrets (database URLs, API keys) go in "Environment variables" in the portal: read them with os.environ.
"""
import json
from urllib.parse import parse_qs


def respond(status, data):
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(data)}


def handler(event, context):
    method = event["requestContext"]["http"]["method"]
    path = event["rawPath"]

    if path == "/health":
        return respond(200, {"ok": True})

    if path == "/hello" and method == "GET":
        name = parse_qs(event.get("rawQueryString", "")).get("name", ["world"])[0]
        print("hello requested for", name)  # shows up under "Show recent logs" in the portal
        return respond(200, {"message": f"Hello, {name}!"})

    if path == "/echo" and method == "POST":
        try:
            data = json.loads(event.get("body") or "{}")
        except ValueError:
            return respond(400, {"error": "Send JSON."})
        return respond(200, {"youSent": data})

    return respond(404, {"error": "Not found"})
