"""class_auth: "Sign in with Google" for the visitors of your app. Standard library only.

    import class_auth

    def handler(event, context):
        answer = class_auth.handle(event)           # serves /auth/login, /auth/callback, /auth/logout and /auth/me for you
        if answer:
            return answer
        user = class_auth.current_user(event)       # {"id", "email", "name"} or None
        ...
        try:
            user = class_auth.require_user(event)   # the same, or it raises SignInRequired
        except class_auth.SignInRequired as stop:
            return stop.response                    # a ready 401 answer

The page sends people to /api/auth/login (the kit's frontend/src/auth.ts does it) and asks /api/auth/me who is signed in.

Rules that keep it safe (see AGENTS.md, "Sign-in"):
  * never write your own login form, password check or token: this helper is the only sign-in;
  * the user comes from current_user(event), never from something the page sent;
  * save each visitor's data under user["id"], and check it on every read and write.

How it works: the platform runs the Google sign-in (Amazon Cognito). This helper sends the visitor there, receives them back with a one-time
code, exchanges the code with the platform over HTTPS, checks the answer (who issued it, for which app, not expired) and then sets ITS OWN
session cookie: signed, not readable by page scripts, sent only over HTTPS. The page never sees a token.

Three modes:
  * on the platform with sign-in ON (the teacher's platform set the CLASS_AUTH_* values): the real Google sign-in;
  * on your own computer (npm run dev): a pretend visitor is signed in at once, so you can build without Google;
  * on the platform with sign-in OFF: /auth/login answers 503 with a plain message, and current_user() is None.
Turn it on with `npm run signin -- on` (or ask your AI editor).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

SESSION_COOKIE = "class_session"
PENDING_COOKIE = "class_auth_tx"
PENDING_SECONDS = 600
LOCAL_KEY = "local-development-only"


class SignInRequired(Exception):
    """Raised by require_user() when nobody is signed in. `.response` is a ready 401 answer."""

    def __init__(self):
        super().__init__("Please sign in first.")
        self.response = _json(401, {"error": "sign_in_required", "message": "Please sign in first."})


# ---------- the mode ----------
def _env(name):
    return os.environ.get(name, "")


def mode():
    """"on" (the platform's Google sign-in), "local" (your computer: a pretend visitor) or "off"."""
    if _env("CLASS_AUTH_HOST") and _env("CLASS_AUTH_CLIENT_ID") and _env("CLASS_AUTH_KEY"):
        return "on"
    return "off" if _env("AWS_LAMBDA_FUNCTION_NAME") else "local"


def _key():
    return (_env("CLASS_AUTH_KEY") if mode() == "on" else LOCAL_KEY).encode()


def _days():
    try:
        return max(1, min(30, int(_env("CLASS_AUTH_DAYS") or 7)))
    except ValueError:
        return 7


def _now():
    return int(time.time())


# ---------- signed values (cookies) ----------
def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload):
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return body + "." + _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def _unsign(value):
    """The payload of a value we signed, or None when it is missing, changed or expired."""
    try:
        body, signature = str(value).split(".")
        if not hmac.compare_digest(_b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest()), signature):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int) or payload["exp"] <= _now():
        return None
    return payload


def _cookies(event):
    found = {}
    parts = list(event.get("cookies") or [])
    header = (event.get("headers") or {}).get("cookie")
    if header:
        parts += header.split(";")
    for part in parts:
        name, _, value = str(part).strip().partition("=")
        if name and name not in found:
            found[name] = value
    return found


def _cookie(name, value, seconds, path="/"):
    secure = "" if mode() == "local" else "; Secure"
    return f"{name}={value}; Path={path}; Max-Age={seconds}; HttpOnly; SameSite=Lax{secure}"


# ---------- answers ----------
def _json(status, data, cookies=None):
    answer = {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(data)}
    if cookies:
        answer["cookies"] = cookies
    return answer


def _redirect(location, cookies=None):
    answer = {"statusCode": 302, "headers": {"location": location, "cache-control": "no-store"}, "body": ""}
    if cookies:
        answer["cookies"] = cookies
    return answer


def _safe_next(value):
    """Only a path inside this app may be the place to return to (never another site)."""
    value = str(value or "/")
    if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value) or len(value) > 500:
        return "/"
    return value


def _home_path(next_path):
    prefix = urllib.parse.urlparse(_env("CLASS_AUTH_HOME")).path.rstrip("/") if mode() == "on" else ""
    return prefix + next_path


def _query(event):
    return {k: v[0] for k, v in urllib.parse.parse_qs(event.get("rawQueryString") or "").items()}


# ---------- the user ----------
def current_user(event):
    """The signed-in visitor as {"id", "email", "name"}, or None. Always use this: never trust a user id the page sends."""
    if mode() == "off":
        return None
    session = _unsign(_cookies(event).get(SESSION_COOKIE, ""))
    if not session or not session.get("id"):
        return None
    return {"id": str(session["id"]), "email": str(session.get("email", "")), "name": str(session.get("name", ""))}


def require_user(event):
    """The signed-in visitor, or raise SignInRequired (return its .response)."""
    user = current_user(event)
    if not user:
        raise SignInRequired()
    return user


def _session_cookie(user):
    seconds = _days() * 86400
    return _cookie(SESSION_COOKIE, _sign({"id": user["id"], "email": user["email"], "name": user["name"], "exp": _now() + seconds}), seconds)


# ---------- the Google sign-in (through the platform) ----------
def _http_post(url, fields):
    """POST a form and return the JSON answer. Tests replace this."""
    request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), headers={"content-type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read())


def _login(event):
    next_path = _safe_next(_query(event).get("next"))
    if mode() == "off":
        return _json(503, {"error": "sign_in_off", "message": "Sign-in is not switched on for this app. Its owner can turn it on with: npm run signin -- on"})
    if mode() == "local":  # your own computer: a pretend visitor, so you can build without Google
        user = {"id": "local-visitor", "email": "local.visitor" + "@" + "example.test", "name": "Local Visitor"}
        return _redirect(next_path, [_session_cookie(user)])
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    pending = _sign({"s": state, "v": verifier, "n": next_path, "exp": _now() + PENDING_SECONDS})
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": _env("CLASS_AUTH_CLIENT_ID"), "redirect_uri": _env("CLASS_AUTH_REDIRECT"),
        "scope": "openid email profile", "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "identity_provider": "Google",
    })
    return _redirect(_env("CLASS_AUTH_HOST") + "/oauth2/authorize?" + query, [_cookie(PENDING_COOKIE, pending, PENDING_SECONDS)])


def _callback(event):
    clear = _cookie(PENDING_COOKIE, "", 0)
    failed = _redirect(_home_path("/") + "?signin=failed", [clear])
    if mode() != "on":
        return failed
    query = _query(event)
    pending = _unsign(_cookies(event).get(PENDING_COOKIE, ""))
    if not pending or not query.get("code") or not hmac.compare_digest(str(pending.get("s", "")), str(query.get("state", ""))):
        return failed
    try:
        answer = _http_post(_env("CLASS_AUTH_HOST") + "/oauth2/token", {
            "grant_type": "authorization_code", "client_id": _env("CLASS_AUTH_CLIENT_ID"), "code": query["code"],
            "redirect_uri": _env("CLASS_AUTH_REDIRECT"), "code_verifier": pending["v"],
        })
        # The token comes straight from the platform's sign-in service over HTTPS, in answer to our one-time code and proof key, so the
        # connection itself proves who sent it (OpenID Connect allows this instead of checking the token's signature). Its claims are checked.
        claims = json.loads(_unb64(str(answer["id_token"]).split(".")[1]))
    except Exception:  # noqa: BLE001 (any failure signs nobody in; nothing about it is shown or logged)
        return failed
    if (not isinstance(claims, dict) or claims.get("iss") != _env("CLASS_AUTH_ISSUER") or claims.get("aud") != _env("CLASS_AUTH_CLIENT_ID")
            or claims.get("token_use") != "id" or not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= _now() or not claims.get("sub")):
        return failed
    email = str(claims.get("email", ""))
    user = {"id": str(claims["sub"]), "email": email, "name": str(claims.get("name") or email.split("@")[0])}
    return _redirect(_home_path(_safe_next(pending.get("n"))), [_session_cookie(user), clear])


def handle(event):
    """Serve the sign-in routes. Returns an answer for /auth/..., or None for every other path (then your own routes run)."""
    path = event.get("rawPath") or ""
    if not path.startswith("/auth/"):
        return None
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "GET")
    if path == "/auth/me" and method == "GET":
        return _json(200, {"user": current_user(event), "sign_in": mode()})
    if path == "/auth/login" and method == "GET":
        return _login(event)
    if path == "/auth/callback" and method == "GET":
        return _callback(event)
    if path == "/auth/logout" and method == "POST":
        return _json(200, {"ok": True}, [_cookie(SESSION_COOKIE, "", 0)])
    return _json(404, {"error": "not_found", "message": "No such sign-in route."})
