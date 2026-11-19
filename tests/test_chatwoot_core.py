"""The pure part of the Chatwoot adapter (lambda/chatwoot_core.py): the signature check and the decision whether a webhook is a message to answer.

The signature is the one Chatwoot's own source builds (lib/webhooks/trigger.rb): "sha256=" + hex HMAC-SHA256 of "<timestamp>.<raw body>". The sample payload has the
shape of Chatwoot's message webhook (app/models/message.rb webhook_data): the account, inbox and conversation are objects with an id, message_type is a word."""
import copy
import hashlib
import hmac
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_core as core  # noqa: E402

SECRET = "test-only-secret-not-a-real-one"
NOW = 1_800_000_000
SAMPLE = {
    "event": "message_created", "id": 501, "content": "hello", "message_type": "incoming", "private": False, "content_type": "text",
    "sender": {"id": 9, "type": "contact", "name": "Customer"},
    "conversation": {"id": 12, "inbox_id": 3, "status": "pending", "account": {"id": 1}},
    "account": {"id": 1, "name": "Shop"}, "inbox": {"id": 3, "name": "Website"},
}


def raw(payload):
    return json.dumps(payload).encode("utf8")


def headers_for(body, secret=SECRET, ts=NOW):
    return {"x-chatwoot-timestamp": str(ts), "x-chatwoot-signature": core.sign(secret, ts, body)}


# ---- the signature is exactly "sha256=" + HMAC-SHA256(secret, "<timestamp>.<body>") in hex ----
body = raw(SAMPLE)
expected = "sha256=" + hmac.new(SECRET.encode(), str(NOW).encode() + b"." + body, hashlib.sha256).hexdigest()
assert core.sign(SECRET, NOW, body) == expected and core.sign(SECRET, str(NOW), body) == expected
assert core.verify(SECRET, headers_for(body), body, NOW) is None
print("  ok  a call signed the way Chatwoot signs it is accepted")

# ---- everything else is refused, with a reason ----
assert core.verify(SECRET, headers_for(body, secret="another-secret"), body, NOW) == "bad_signature"
assert core.verify(SECRET, headers_for(body), body + b" ", NOW) == "bad_signature", "one changed byte is enough"
assert core.verify(SECRET, headers_for(body), raw({**SAMPLE, "id": 502}), NOW) == "bad_signature"
assert core.verify(SECRET, {}, body, NOW) == "missing_signature"
assert core.verify(SECRET, {"x-chatwoot-timestamp": str(NOW)}, body, NOW) == "missing_signature"
assert core.verify(SECRET, {"x-chatwoot-signature": core.sign(SECRET, NOW, body)}, body, NOW) == "missing_signature", "Chatwoot sends no signature for a bot without a secret"
assert core.verify(SECRET, {**headers_for(body), "x-chatwoot-timestamp": "12abc"}, body, NOW) == "bad_timestamp"
assert core.verify(SECRET, {**headers_for(body), "x-chatwoot-timestamp": "-5"}, body, NOW) == "bad_timestamp"
assert core.verify(SECRET, headers_for(body), body, NOW + core.TOLERANCE_SECONDS) is None, "the edge of the window is still inside it"
assert core.verify(SECRET, headers_for(body), body, NOW + core.TOLERANCE_SECONDS + 1) == "stale_timestamp", "an old call cannot be replayed"
assert core.verify(SECRET, headers_for(body, ts=NOW + core.TOLERANCE_SECONDS + 1), body, NOW) == "stale_timestamp", "nor one from the future"
assert core.verify(SECRET, {**headers_for(body), "x-chatwoot-signature": "sha256=éé"}, body, NOW) == "bad_signature", "a non-ASCII signature is refused, not a crash"
assert core.verify(SECRET, {**headers_for(body), "x-chatwoot-signature": core.sign(SECRET, NOW, body).upper()}, body, NOW) == "bad_signature"
print("  ok  a wrong secret, a changed body, missing headers, a bad, old or future timestamp and an odd signature are all refused")

# ---- a customer's message in a pending conversation is accepted, and only its numbers are kept ----
fields, reason = core.classify(copy.deepcopy(SAMPLE))
assert reason is None and fields == {"message_id": 501, "conversation_id": 12, "account_id": 1, "inbox_id": 3}, (fields, reason)
assert "hello" not in json.dumps(fields) and "Customer" not in json.dumps(fields), "the text and the name are never carried on"
assert core.classify({**SAMPLE, "message_type": 0})[1] is None, "the stored number of 'incoming' is accepted too"
print("  ok  a customer's message is accepted and only its ids are kept")


def refused(change, why):
    payload = copy.deepcopy(SAMPLE)
    for key, value in change.items():
        if value is KeyError:
            payload.pop(key, None)
        else:
            payload[key] = value
    result = core.classify(payload)
    assert result == (None, why), (change, result, why)


# ---- what the bot must not answer ----
refused({"event": "message_updated"}, "event_ignored")
refused({"event": "conversation_status_changed"}, "event_ignored")
refused({"event": KeyError}, "event_ignored")
refused({"message_type": "outgoing"}, "not_incoming")  # the bot's own reply and a human agent's reply come back as webhooks too
refused({"message_type": "template"}, "not_incoming")
refused({"message_type": "activity"}, "not_incoming")
refused({"message_type": 1}, "not_incoming")
refused({"message_type": False}, "not_incoming")
refused({"message_type": KeyError}, "not_incoming")
refused({"private": True}, "private")
refused({"private": KeyError}, "private")
refused({"private": "false"}, "private")
refused({"sender": {"id": 4, "type": "agent_bot", "name": "Bot"}}, "from_bot")
refused({"conversation": {**SAMPLE["conversation"], "status": "open"}}, "not_pending")  # a human has taken it over
refused({"conversation": {**SAMPLE["conversation"], "status": "resolved"}}, "not_pending")
refused({"conversation": {"id": 12}}, "not_pending")
refused({"conversation": "12"}, "bad_payload")
refused({"conversation": KeyError}, "bad_payload")
print("  ok  other events, outgoing and template messages, private notes, the bot's own messages and conversations a human has taken are not answered")

# ---- a payload without proper ids is refused, whatever else it holds ----
refused({"id": KeyError}, "bad_payload")
refused({"id": "501"}, "bad_payload")
refused({"id": True}, "bad_payload")
refused({"id": 0}, "bad_payload")
refused({"id": -3}, "bad_payload")
refused({"id": 5.5}, "bad_payload")
refused({"conversation": {"status": "pending"}}, "bad_payload")
refused({"account": KeyError}, "bad_payload")
refused({"account": {"name": "Shop"}}, "bad_payload")
refused({"inbox": {"id": "3"}}, "bad_payload")
assert core.classify(None) == (None, "not_json") and core.classify([1]) == (None, "not_json") and core.classify("x") == (None, "not_json")
assert core.parse(b"not json") is None and core.parse(b"[1, 2]") is None and core.parse(b"\xff\xfe") is None and core.parse(b"") is None
assert core.parse(b'{"a": 1}') == {"a": 1}
print("  ok  a missing, textual, zero, negative, boolean or fractional id, and a body that is not a JSON object, are refused")

# ---- the address names the agent, and only a short lowercase name is one ----
for good in ("shop", "shop-1", "a", "0abc", "a" * 40):
    assert core.AGENT_ID.fullmatch(good), good
for bad in ("", "Shop", "-shop", "shop_1", "shop/1", "../x", "a" * 41, "shop 1", "shop\n"):
    assert not core.AGENT_ID.fullmatch(bad), bad
print("  ok  the agent name in the address is a short lowercase name and nothing like a path")

# ---- the reply (step T2) ----
assert core.reply_text(501) == "Received (message 501)"
for good in ("https://chat.example.org", "https://chat.example.org/", "https://chat.example.org:8443", "https://chat-1.example.org"):
    assert core.base_url_of(good) == good.rstrip("/"), good
for bad in ("", None, "http://chat.example.org", "chat.example.org", "https://", "https://user:pass" + chr(64) + "chat.example.org", "https://chat.example.org/api", "https://chat.example.org?x=1",
            "https://chat.example.org#x", "https://[::1]", "https://chat.example.org:notaport", "https://-bad.example.org", "https://bad_host.example.org", "ftp://chat.example.org",
            "https://chat.example.org\nHost: evil", "https://chat.example.org evil.org"):
    assert core.base_url_of(bad) is None, bad
print("  ok  the Chatwoot address is accepted only as https://host[:port]: no http, user name, path, query, fragment, odd host or line break")

TOKEN = "abcDEF123456xyz789"
assert core.token_ok(TOKEN) and core.token_ok("a1b2c3d4-e5.f6_g7~h8+i9/j0=")
for bad in ("", None, 5, "short", "has space 12345678", "line\nbreak12345", 'quote"12345678', "a" * 257, "tab\t12345678", "\u00e9" * 10):
    assert not core.token_ok(bad), repr(bad)
url, headers, body = core.reply_request("https://chat.example.org", "bearer", TOKEN, 1, 12, "Received (message 501)")
assert url == "https://chat.example.org/api/v1/accounts/1/conversations/12/messages"
assert headers == {"Content-Type": "application/json", "Accept": "application/json", "Authorization": "Bearer " + TOKEN}
assert json.loads(body) == {"content": "Received (message 501)", "message_type": "outgoing", "private": False}, "a public outgoing message"
url, headers, _ = core.reply_request("https://chat.example.org:8443", "api_access_token", TOKEN, "7", 3, "x")
assert url == "https://chat.example.org:8443/api/v1/accounts/7/conversations/3/messages" and headers["api_access_token"] == TOKEN and "Authorization" not in headers
for style in ("Bearer", "", None, "basic"):
    try:
        core.reply_request("https://chat.example.org", style, TOKEN, 1, 1, "x")
        raise AssertionError(style)
    except ValueError:
        pass
for odd in ("1/../2", "1; drop", "x", None, 1.5 + 1j):
    try:
        core.reply_request("https://chat.example.org", "bearer", TOKEN, odd, 1, "x")
        raise AssertionError(odd)
    except (ValueError, TypeError):
        pass
print("  ok  the request is Create New Message: a public outgoing message, the token in exactly one header, the ids only as whole numbers in the path")

assert core.reply_result(200, b'{"id": 77, "content": "x"}') == ("replied", 77)
assert core.reply_result(201, b'{"id": 78}') == ("replied", 78)
assert core.reply_result(200, b"not json") == ("replied", None) and core.reply_result(200, b"[]") == ("replied", None) and core.reply_result(200, b'{"id": "x"}') == ("replied", None)
assert core.reply_result(200, b"\xff") == ("replied", None)
for status, reason in ((401, "http_401"), (403, "http_403"), (404, "http_404"), (422, "http_422"), (429, "http_429"), (500, "http_5xx"), (502, "http_5xx"), (503, "http_5xx"), (599, "http_5xx"),
                       (400, "http_other"), (302, "http_other"), (301, "http_other"), (204, "http_other"), (0, "http_other")):
    assert core.reply_result(status, b'{"id": 1}') == ("failed", reason), (status, core.reply_result(status, b"{}"))
print("  ok  Chatwoot's answer is read as replied (with the message id) or failed with a short code; a redirect or an odd status is a failure, never followed")

# ---- the customer's words (for the worker only) ----
assert core.message_text({"content": "  What time do you open?  "}) == "What time do you open?"
assert core.message_text({"content": "x" * (core.MAX_QUESTION_CHARS + 50)}) == "x" * core.MAX_QUESTION_CHARS, "cut to the limit"
for odd in ({}, {"content": None}, {"content": 5}, {"content": ["a"]}, None, "text", []):
    assert core.message_text(odd) == "", odd
fields, reason = core.classify({**SAMPLE, "content": "secret words"})
assert reason is None and "secret words" not in json.dumps(fields), "classify never returns the text"
print("  ok  the customer's words are available as text for the worker (trimmed, cut, empty when missing) and never part of what classify returns")

print("test_chatwoot_core.py: signature, loop guard, payload checks and the reply")
