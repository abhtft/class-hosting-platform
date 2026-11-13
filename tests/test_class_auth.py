"""The kit's sign-in helper (student/class_auth.py): the three modes, the Google flow through the platform with a pretend token service,
every way a callback can be wrong (nobody is signed in), the signed session cookie (changed, expired, another key), the place to return to
(never another site), and the routes."""
import base64
import hashlib
import json
import os
import sys
import urllib.parse

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "student"))
for name in list(os.environ):
    if name.startswith("CLASS_AUTH_") or name == "AWS_LAMBDA_FUNCTION_NAME":
        del os.environ[name]
import class_auth as ca  # noqa: E402

clock = [1_800_000_000]
ca._now = lambda: clock[0]
ON = {"CLASS_AUTH_HOST": "https://sd-class-abc.auth.us-east-1.amazoncognito.com", "CLASS_AUTH_CLIENT_ID": "client-1",
      "CLASS_AUTH_ISSUER": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pool", "CLASS_AUTH_REDIRECT": "https://ana.example.com/api/auth/callback",
      "CLASS_AUTH_HOME": "https://ana.example.com/", "CLASS_AUTH_KEY": "k" * 43, "CLASS_AUTH_DAYS": "7"}


def set_mode(kind, **extra):
    for name in list(os.environ):
        if name.startswith("CLASS_AUTH_") or name == "AWS_LAMBDA_FUNCTION_NAME":
            del os.environ[name]
    if kind == "on":
        os.environ.update({**ON, **extra, "AWS_LAMBDA_FUNCTION_NAME": "sd-ana"})
    elif kind == "off":
        os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "sd-ana"


def event(path, method="GET", query="", cookies=None, header_cookie=None):
    e = {"rawPath": path, "rawQueryString": query, "requestContext": {"http": {"method": method}}, "headers": {}}
    if cookies is not None:
        e["cookies"] = cookies
    if header_cookie:
        e["headers"]["cookie"] = header_cookie
    return e


def jar(answer, old=None):
    """The cookies a browser would now hold, as the list the platform passes to the app."""
    held = dict(c.split("=", 1) for c in (old or []))
    for cookie in answer.get("cookies", []):
        pair, *attrs = cookie.split("; ")
        name, value = pair.split("=", 1)
        if "Max-Age=0" in attrs:
            held.pop(name, None)
        else:
            held[name] = value
    return [f"{k}={v}" for k, v in held.items()]


def token(**claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


GOOD = {"iss": ON["CLASS_AUTH_ISSUER"], "aud": "client-1", "token_use": "id", "exp": clock[0] + 3600, "sub": "user-123", "email": "ana" + "@" + "example.org", "name": "Ana"}

# ---------- the modes ----------
set_mode("local")
assert ca.mode() == "local"
set_mode("off")
assert ca.mode() == "off"
set_mode("on")
assert ca.mode() == "on"
print("  ok  three modes: on (the platform set the values), local (your computer), off (on the platform without them)")

# ---------- other paths are not ours ----------
assert ca.handle(event("/notes")) is None and ca.handle(event("/authentic")) is None
assert ca.handle(event("/auth/unknown"))["statusCode"] == 404
assert ca.handle(event("/auth/logout", "GET"))["statusCode"] == 404, "signing out is a POST (a link or an image cannot do it)"

# ---------- local: a pretend visitor ----------
set_mode("local")
me = json.loads(ca.handle(event("/auth/me"))["body"])
assert me == {"user": None, "sign_in": "local"}
go = ca.handle(event("/auth/login", query="next=/notes"))
assert go["statusCode"] == 302 and go["headers"]["location"] == "/notes"
assert "HttpOnly" in go["cookies"][0] and "SameSite=Lax" in go["cookies"][0] and "Secure" not in go["cookies"][0], "plain http on your own computer"
held = jar(go)
user = ca.current_user(event("/x", cookies=held))
assert user["id"] == "local-visitor" and user["name"] == "Local Visitor" and "@" in user["email"]
assert ca.current_user(event("/x", header_cookie="; ".join(held))) == user, "the cookie header works too (the local dev server sends it that way)"
out = ca.handle(event("/auth/logout", "POST", cookies=held))
assert json.loads(out["body"]) == {"ok": True} and jar(out, held) == []
try:
    ca.require_user(event("/x"))
    raise AssertionError("require_user must stop a visitor who is not signed in")
except ca.SignInRequired as stop:
    assert stop.response["statusCode"] == 401 and json.loads(stop.response["body"])["error"] == "sign_in_required"
assert ca.require_user(event("/x", cookies=held)) == user
print("  ok  on your own computer a pretend visitor signs in at once; sign-out clears it; require_user answers 401 when nobody is signed in")

# ---------- off ----------
set_mode("off")
off = ca.handle(event("/auth/login"))
assert off["statusCode"] == 503 and json.loads(off["body"])["error"] == "sign_in_off"
assert ca.current_user(event("/x", cookies=held)) is None, "a cookie from your computer is worth nothing on the platform"
assert json.loads(ca.handle(event("/auth/me"))["body"]) == {"user": None, "sign_in": "off"}
print("  ok  on the platform with sign-in off: a plain 503, and nobody is signed in")

# ---------- on: the way to Google ----------
set_mode("on")
go = ca.handle(event("/auth/login", query="next=/notes%3Fpage%3D2"))
assert go["statusCode"] == 302
target = urllib.parse.urlparse(go["headers"]["location"])
q = dict(urllib.parse.parse_qsl(target.query))
assert f"{target.scheme}://{target.netloc}{target.path}" == ON["CLASS_AUTH_HOST"] + "/oauth2/authorize"
assert q["response_type"] == "code" and q["client_id"] == "client-1" and q["redirect_uri"] == ON["CLASS_AUTH_REDIRECT"]
assert q["scope"] == "openid email profile" and q["code_challenge_method"] == "S256" and q["identity_provider"] == "Google"
assert len(q["state"]) >= 24 and len(q["code_challenge"]) == 43
pending_cookie = go["cookies"][0]
assert pending_cookie.startswith("class_auth_tx=") and "HttpOnly" in pending_cookie and "Secure" in pending_cookie and "SameSite=Lax" in pending_cookie and "Max-Age=600" in pending_cookie
pending = jar(go)
other = ca.handle(event("/auth/login"))
assert dict(urllib.parse.parse_qsl(urllib.parse.urlparse(other["headers"]["location"]).query))["state"] != q["state"], "every sign-in has its own random values"

# ---------- on: coming back ----------
seen = {}


def service(url, fields):
    seen.update(url=url, fields=dict(fields))
    return {"id_token": token(**GOOD), "access_token": "not-used"}


ca._http_post = service
back = ca.handle(event("/auth/callback", query=f"code=one-time-code&state={q['state']}", cookies=pending))
assert back["statusCode"] == 302 and back["headers"]["location"] == "/notes?page=2", back["headers"]
assert seen["url"] == ON["CLASS_AUTH_HOST"] + "/oauth2/token"
assert seen["fields"]["grant_type"] == "authorization_code" and seen["fields"]["code"] == "one-time-code" and seen["fields"]["client_id"] == "client-1"
assert seen["fields"]["redirect_uri"] == ON["CLASS_AUTH_REDIRECT"]
proof = base64.urlsafe_b64encode(hashlib.sha256(seen["fields"]["code_verifier"].encode()).digest()).rstrip(b"=").decode()
assert proof == q["code_challenge"], "the proof key sent to the token service matches the challenge sent to Google's page"
session_cookie = next(c for c in back["cookies"] if c.startswith("class_session="))
assert "HttpOnly" in session_cookie and "Secure" in session_cookie and "SameSite=Lax" in session_cookie and f"Max-Age={7 * 86400}" in session_cookie
assert any(c.startswith("class_auth_tx=;") and "Max-Age=0" in c for c in back["cookies"]), "the one-time values are cleared"
assert "one-time-code" not in json.dumps(back) and "user-123" not in back["headers"]["location"], "nothing about the visitor is put in the address"
held = jar(back, pending)
assert ca.current_user(event("/x", cookies=held)) == {"id": "user-123", "email": GOOD["email"], "name": "Ana"}
assert json.loads(ca.handle(event("/auth/me", cookies=held))["body"])["user"]["id"] == "user-123"
print("  ok  the Google sign-in: sent to the platform's page with a proof key and random state; the code is exchanged once; the session cookie is signed, HttpOnly, Secure, SameSite=Lax")


# ---------- every wrong callback signs nobody in ----------
def attempt(claims=None, state=None, cookies="pending", code="c", fail=False):
    go = ca.handle(event("/auth/login"))
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(go["headers"]["location"]).query))

    def svc(url, fields):
        if fail:
            raise OSError("service down")
        return {"id_token": token(**(claims if claims is not None else GOOD))}

    ca._http_post = svc
    use = jar(go) if cookies == "pending" else cookies
    query = f"state={state if state is not None else q['state']}" + (f"&code={code}" if code else "")
    answer = ca.handle(event("/auth/callback", query=query, cookies=use))
    return answer, go


wrong = {
    "a state that does not match": dict(state="someone-elses-state"),
    "no pending cookie": dict(cookies=[]),
    "a changed pending cookie": dict(cookies=["class_auth_tx=abc.def"]),
    "no code": dict(code=""),
    "the token service failing": dict(fail=True),
    "another issuer": dict(claims={**GOOD, "iss": "https://evil.example/pool"}),
    "a token for another app": dict(claims={**GOOD, "aud": "client-2"}),
    "an access token instead of an id token": dict(claims={**GOOD, "token_use": "access"}),
    "an expired token": dict(claims={**GOOD, "exp": clock[0] - 1}),
    "no user id": dict(claims={k: v for k, v in GOOD.items() if k != "sub"}),
}
for name, how in wrong.items():
    answer, _ = attempt(**how)
    assert answer["statusCode"] == 302 and answer["headers"]["location"] == "/?signin=failed", name
    assert not any(c.startswith("class_session=") for c in answer.get("cookies", [])), f"{name}: nobody is signed in"
# a pending cookie is good for ten minutes only
answer, go = attempt()
assert any(c.startswith("class_session=") for c in answer["cookies"])
go = ca.handle(event("/auth/login"))
q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(go["headers"]["location"]).query))
clock[0] += 601
late = ca.handle(event("/auth/callback", query=f"code=c&state={q['state']}", cookies=jar(go)))
assert late["headers"]["location"] == "/?signin=failed", "a sign-in that took longer than ten minutes starts again"
clock[0] -= 601
print("  ok  a wrong callback signs nobody in: wrong state, no or changed pending cookie, no code, a failing service, another issuer, another app, an access token, an expired token, no user id, or too late")

# ---------- the session cookie cannot be forged, and it ends ----------
assert ca.current_user(event("/x", cookies=["class_session=" + held[0].split("=", 1)[1][:-2] + "xx"])) is None, "a changed signature"
body, sig = next(c for c in held if c.startswith("class_session=")).split("=", 1)[1].split(".")
forged = base64.urlsafe_b64encode(json.dumps({"id": "admin", "email": "", "name": "", "exp": clock[0] + 999}).encode()).rstrip(b"=").decode()
assert ca.current_user(event("/x", cookies=[f"class_session={forged}.{sig}"])) is None, "another payload with an old signature"
set_mode("on", CLASS_AUTH_KEY="another-apps-key-" + "z" * 30)
assert ca.current_user(event("/x", cookies=held)) is None, "a cookie from another app (another key) is worth nothing here"
set_mode("on")
assert ca.current_user(event("/x", cookies=held)) is not None
clock[0] += 7 * 86400 + 1
assert ca.current_user(event("/x", cookies=held)) is None, "the session ends after its days"
clock[0] -= 7 * 86400 + 1
for garbage in ("", "x", "a.b.c", "....", "class"):
    assert ca.current_user(event("/x", cookies=[f"class_session={garbage}"])) is None
set_mode("on", CLASS_AUTH_DAYS="3")
answer, _ = attempt()
assert f"Max-Age={3 * 86400}" in next(c for c in answer["cookies"] if c.startswith("class_session="))
set_mode("on", CLASS_AUTH_DAYS="lots")
answer, _ = attempt()
assert f"Max-Age={7 * 86400}" in next(c for c in answer["cookies"] if c.startswith("class_session=")), "a bad setting falls back to 7 days"
set_mode("on")
print("  ok  the session cookie: a changed or forged one, one from another app, and an old one are all refused; its length follows the teacher's setting")

# ---------- the place to return to is always inside this app ----------
for bad in ("//evil.example/x", "https://evil.example/", "/\\evil", "javascript:alert(1)", "/a\nb", "/" + "x" * 600):
    assert ca._safe_next(bad) == "/", bad
assert ca._safe_next("/notes?a=1") == "/notes?a=1" and ca._safe_next(None) == "/"
go = ca.handle(event("/auth/login", query="next=" + urllib.parse.quote("//evil.example/steal")))
q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(go["headers"]["location"]).query))
ca._http_post = lambda url, fields: {"id_token": token(**GOOD)}
back = ca.handle(event("/auth/callback", query=f"code=c&state={q['state']}", cookies=jar(go)))
assert back["headers"]["location"] == "/", "an outside address is never used"
# a platform without its own domain serves the app under a path: the return keeps that path
set_mode("on", CLASS_AUTH_HOME="https://d123.cloudfront.net/ana/", CLASS_AUTH_REDIRECT="https://d123.cloudfront.net/ana/api/auth/callback")
go = ca.handle(event("/auth/login", query="next=/notes"))
q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(go["headers"]["location"]).query))
back = ca.handle(event("/auth/callback", query=f"code=c&state={q['state']}", cookies=jar(go)))
assert back["headers"]["location"] == "/ana/notes"
print("  ok  the place to return to is always a path inside this app, never another site")

print("test_class_auth.py: the sign-in helper (Python) passes")
