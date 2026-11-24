"""Chatwoot Agent Bot adapter, the pure part: no AWS, no network, nothing to import but the standard library and the settings loader.

A Chatwoot Agent Bot sends a webhook for every message in an inbox it is connected to. This module answers the questions that need no AWS:

  * Is the call really from Chatwoot?   verify()   the signature header Chatwoot adds when the bot has a secret:
        X-Chatwoot-Timestamp: <unix seconds>
        X-Chatwoot-Signature: sha256=<hex of HMAC-SHA256(secret, "<timestamp>.<raw request body>")>
    The raw bytes are signed (never a re-serialised JSON), the comparison is constant-time, and a timestamp outside the tolerance is refused.
    (Chatwoot sends no signature at all for a bot without a secret: such a call is refused here, a bot must have one.)
  * Is this a message the bot should answer?   classify()   Chatwoot also sends the bot's own replies, a human agent's replies, private notes and
    events of conversations a human has taken over. Only a customer's message in a conversation that is still `pending` (the bot's) is accepted.

  * What does the reply look like?   reply_request() and reply_result()   (step T2) the Create New Message call and how its answer is read.

The receiving function is chatwoot_api.py. Facts are from Chatwoot's source (lib/webhooks/trigger.rb, app/models/message.rb,
app/presenters/conversations/event_data_presenter.rb): in a webhook the message has `message_type` as a word ("incoming", "outgoing", "template"),
`conversation.id` is the number shown in the Chatwoot dashboard (its display id), and the account and inbox are objects with an `id`."""
import hashlib
import hmac
import json
import re
import uuid
from urllib.parse import urlsplit

import platform_settings

TOLERANCE_SECONDS = platform_settings.value("chatwoot.timestampToleranceSeconds")
MAX_QUESTION_CHARS = platform_settings.value("chatwoot.maxQuestionChars")
AGENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")  # the {agentId} in the webhook address: a short lowercase name, never a path. Always used with fullmatch (a "$" would let a trailing newline through)
_TIMESTAMP = re.compile(r"[0-9]{1,12}")
_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{8,256}")  # a Chatwoot token has no space, quote or line break: nothing that could add a header
REPLY_TEXT = "Received (message {id})"  # the placeholder reply of step T2; the real answer comes with the agent runtime
AUTH_STYLES = ("bearer", "api_access_token")  # Authorization: Bearer <token> (Chatwoot 4.19 and later) or the older api_access_token header


def sign(secret, timestamp, body):
    """The signature header value Chatwoot would send for this secret, timestamp (text) and raw body (bytes)."""
    message = str(timestamp).encode("ascii") + b"." + body
    return "sha256=" + hmac.new(secret.encode("utf8"), message, hashlib.sha256).hexdigest()


def verify(secret, headers, body, now):
    """None when the call is authentic, else a short reason: missing_signature, bad_timestamp, stale_timestamp or bad_signature.
    `headers` has lower-case names (an HTTP API gives them that way); `body` is the raw request bytes."""
    timestamp = headers.get("x-chatwoot-timestamp")
    given = headers.get("x-chatwoot-signature")
    if not timestamp or not given:
        return "missing_signature"
    if not _TIMESTAMP.fullmatch(timestamp):
        return "bad_timestamp"
    if abs(int(now) - int(timestamp)) > TOLERANCE_SECONDS:
        return "stale_timestamp"
    expected = sign(secret, timestamp, body)
    if not hmac.compare_digest(expected.encode("ascii"), given.encode("utf8", "replace")):
        return "bad_signature"
    return None


def parse(body):
    """The webhook body as a dict, or None when it is not a JSON object."""
    try:
        data = json.loads(body.decode("utf8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _id(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def channel_of(payload):
    """The kind of channel the message came on, as a short lowercase word ("whatsapp", "web_widget", "facebook_page", "instagram"...), or ''. Chatwoot names it in the
    conversation as "Channel::Whatsapp". A word the operator's own system supplies, not the customer's: it may travel to the worker and into the prompt."""
    conversation = payload.get("conversation") if isinstance(payload, dict) else None
    raw = conversation.get("channel") if isinstance(conversation, dict) else None
    if not isinstance(raw, str) or not re.fullmatch(r"Channel::[A-Za-z]{1,40}", raw):
        return ""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", raw.split("::", 1)[1]).lower()


def classify(payload):
    """(fields, None) for a message the bot should answer, else (None, reason). `fields` holds only numbers (message, conversation, account and inbox
    ids): the text of the message is deliberately not part of it, nothing here may store it. Reasons: not_json, event_ignored, not_incoming, private,
    from_bot, not_pending, bad_payload."""
    if not isinstance(payload, dict):
        return None, "not_json"
    if payload.get("event") != "message_created":
        return None, "event_ignored"
    kind = payload.get("message_type")  # the word "incoming" in a webhook; its stored number (0) is accepted in case a version emits that
    if not (kind == "incoming" or (isinstance(kind, int) and not isinstance(kind, bool) and kind == 0)):
        return None, "not_incoming"
    if payload.get("private") is not False:
        return None, "private"
    sender = payload.get("sender")
    if isinstance(sender, dict) and sender.get("type") == "agent_bot":
        return None, "from_bot"
    conversation = payload.get("conversation")
    if not isinstance(conversation, dict):
        return None, "bad_payload"
    if conversation.get("status") != "pending":
        return None, "not_pending"
    account, inbox = payload.get("account"), payload.get("inbox")
    fields = {
        "message_id": _id(payload.get("id")),
        "conversation_id": _id(conversation.get("id")),
        "account_id": _id(account.get("id")) if isinstance(account, dict) else None,
        "inbox_id": _id(inbox.get("id")) if isinstance(inbox, dict) else None,
    }
    if None in fields.values():
        return None, "bad_payload"
    return fields, None


def message_text(payload):
    """The customer's words, cut to a limit. They are for the worker only: they travel in the asynchronous event and are never logged or put in a message item (the worker's
    conversation memory is the one place that keeps them, briefly), which is why classify() does not return them."""
    content = payload.get("content") if isinstance(payload, dict) else None
    return content.strip()[:MAX_QUESTION_CHARS] if isinstance(content, str) else ""


def attachment_kinds(payload):
    """The kinds of file the customer's message carries ("image", "audio", "video", "file", "location"...), at most five short words and nothing else about them: the bot does
    not read a file, it only needs to know there is one."""
    items = payload.get("attachments") if isinstance(payload, dict) else None
    kinds = []
    for item in items if isinstance(items, list) else []:
        kind = item.get("file_type") if isinstance(item, dict) else None
        if isinstance(kind, str) and re.fullmatch(r"[a-z_]{1,20}", kind) and kind not in kinds:
            kinds.append(kind)
    return kinds[:5]


# ---------- the reply (step T2) ----------

def reply_text(message_id):
    return REPLY_TEXT.format(id=int(message_id))


def base_url_of(url):
    """The Chatwoot address as 'https://host[:port]', or None unless it is exactly that: https, a plain host name, no user name, path, query or fragment.
    The platform calls this one address and nothing taken from a webhook or from data."""
    try:
        parts = urlsplit(url or "")
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or not _HOST.fullmatch(parts.hostname) or parts.username or parts.password:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    return "https://" + parts.hostname + (f":{port}" if port else "")


def token_ok(token):
    return isinstance(token, str) and _TOKEN.fullmatch(token) is not None


USER_AGENT = "ClassPlatformChatwootAdapter/1.0"


def reply_request(base_url, style, token, account_id, conversation_id, text, private=False):
    """(url, headers, body) of Chatwoot's Create New Message call: a public (private false) outgoing message in the conversation, sent with the agent bot's
    own token. The ids go into the path as whole numbers only."""
    url = f"{base_url}/api/v1/accounts/{int(account_id)}/conversations/{int(conversation_id)}/messages"
    # A name of our own: a Chatwoot behind Cloudflare refuses Python's default "Python-urllib" user-agent with a 403 (error 1010) before the request reaches Chatwoot.
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
    if style == "bearer":
        headers["Authorization"] = "Bearer " + token
    elif style == "api_access_token":
        headers["api_access_token"] = token
    else:
        raise ValueError("unknown auth style")
    return url, headers, json.dumps({"content": text, "message_type": "outgoing", "private": bool(private)}).encode("utf8")


def attachment_request(base_url, style, token, account_id, conversation_id, filename, mime, data, caption="", boundary=None):
    """(url, headers, body) of Create New Message with ONE file attached (a multipart form: the same call, the same token and checks as a reply). `filename` and `mime` are
    ours (chatwoot_media), never the customer's."""
    url, headers, _ = reply_request(base_url, style, token, account_id, conversation_id, "")
    boundary = boundary or "----classplatform" + uuid.uuid4().hex
    fields = (("content", caption), ("message_type", "outgoing"), ("private", "false"))
    body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf8") for name, value in fields)
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="attachments[]"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode("utf8") + bytes(data) + f"\r\n--{boundary}--\r\n".encode("utf8")
    headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    return url, headers, body


def status_request(base_url, style, token, account_id, conversation_id, status):
    """(url, headers, body) of Chatwoot's toggle_status call: the conversation's status ("open" puts it in the team's list). The same address, header and checks as a reply."""
    url, headers, _ = reply_request(base_url, style, token, account_id, conversation_id, "")
    return url[: -len("/messages")] + "/toggle_status", headers, json.dumps({"status": status}).encode("utf8")


def assign_request(base_url, style, token, account_id, conversation_id, team_id):
    """(url, headers, body) of Chatwoot's assignment call with a team only (the team's own setting decides whether a person of the team is picked). Same address rules as a reply."""
    url, headers, _ = reply_request(base_url, style, token, account_id, conversation_id, "")
    return url[: -len("/messages")] + "/assignments", headers, json.dumps({"team_id": int(team_id)}).encode("utf8")


def status_result(status, body):
    """('ok', None) when Chatwoot changed the status, else ('failed', reason) with the same short codes as a reply."""
    kind, detail = reply_result(status, body)
    return ("ok", None) if kind == "replied" else (kind, detail)


def reply_result(status, body):
    """('replied', message id or None) when Chatwoot created the message, else ('failed', reason) with a short code. Nothing from the body is kept."""
    if status in (200, 201):
        try:
            data = json.loads(body.decode("utf8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        reply_id = data.get("id") if isinstance(data, dict) else None
        return "replied", _id(reply_id)
    known = {401: "http_401", 403: "http_403", 404: "http_404", 422: "http_422", 429: "http_429"}
    if status in known:
        return "failed", known[status]
    if isinstance(status, int) and 500 <= status < 600:
        return "failed", "http_5xx"
    return "failed", "http_other"
