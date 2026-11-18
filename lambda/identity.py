"""Student identity for the platform's own sign-in with Google (docs/STUDENT_IDENTITY_PLAN.md, phase 1).

This module holds the rules that decide WHO may sign in, and the record of each signed-in student. It never talks to Google or Cognito:
the portal verifies the sign-in first (phase 1b) and then asks this module what the person may do.

Records in the one tokens table:
  adm#rules   the teacher's admission rules: mode, listed e-mail addresses, listed domains.
  usr#<sub>   one student identity, keyed on Google's stable "sub" (never on the e-mail, which can change or be reused):
              sub, email, name, status (active | disabled), first_seen, last_seen.

Modes (the teacher chooses; an existing platform starts in "token-only", so nothing changes until the teacher turns Google sign-in on):
  token-only  Google sign-in is off. The token login works as before.
  invite      only the listed e-mail addresses are let in.
  domain      the listed addresses and everyone at a listed e-mail domain are let in.
  request     the listed addresses and listed domains are let in; any other verified Google account may sign in and ASK, and gets nothing until approved.

Admission answers with a level: "member" (let in), "requester" (may sign in and send requests only) or "denied".
"""
import os
import re
import time

import jwtcheck
import platform_settings
from portal import STUDENT_SESSION_SECONDS, PortalError, client_ip, db, sign_session, verify_session

RULES_KEY = "adm#rules"
USER_PREFIX = "usr#"
MODES = ("token-only", "invite", "domain", "request")
MAX_EMAILS = platform_settings.value("identity.maxListedEmails")
MAX_DOMAINS = platform_settings.value("identity.maxListedDomains")
SIGNIN_PER_MINUTE = platform_settings.value("identity.signInAttemptsPerMinute")

EMAIL_RE = re.compile(r"^[^@\s,;<>\"']{1,64}@[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?\.[a-z]{2,63}$")
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z]{2,63}$")
SUB_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def default_rules():
    return {"mode": "token-only", "emails": [], "domains": []}


def get_rules():
    item = db().get(RULES_KEY) or {}
    base = default_rules()
    return {"mode": item.get("mode", base["mode"]) if item.get("mode") in MODES else base["mode"],
            "emails": list(item.get("emails") or []), "domains": list(item.get("domains") or []),
            "portal_url": item.get("portal_url") or "", "updated_at": item.get("updated_at")}


def _clean_list(value, name, pattern, limit, what):
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PortalError(400, f"{name} must be a list of text values.")
    out, seen = [], set()
    for raw in value:
        v = raw.strip().lower()
        if not v:
            continue
        if not pattern.match(v):
            raise PortalError(400, f"'{raw.strip()[:80]}' is not a valid {what}.")
        if v not in seen:
            seen.add(v)
            out.append(v)
    if len(out) > limit:
        raise PortalError(400, f"Too many {name}: at most {limit}.")
    return sorted(out)


def _checked(body):
    """The rules as they would be after this change, or a plain error. Stores nothing. Only the fields sent are changed."""
    current = get_rules()
    mode = body.get("mode", current["mode"])
    if mode not in MODES:
        raise PortalError(400, "mode must be one of: " + ", ".join(MODES) + ".")
    emails = _clean_list(body.get("emails"), "emails", EMAIL_RE, MAX_EMAILS, "e-mail address")
    domains = _clean_list(body.get("domains"), "domains", DOMAIN_RE, MAX_DOMAINS, "e-mail domain (for example school.edu)")
    emails = current["emails"] if emails is None else emails
    domains = current["domains"] if domains is None else domains
    if mode == "invite" and not emails:
        raise PortalError(400, "Invite mode needs at least one e-mail address on the list, or nobody could sign in.")
    if mode == "domain" and not (domains or emails):
        raise PortalError(400, "Domain mode needs at least one domain (or address) on the list, or nobody could sign in.")
    portal_url = check_portal_url(body["portal_url"]) if body.get("portal_url") else current["portal_url"]
    return {"mode": mode, "emails": emails, "domains": domains, "portal_url": portal_url}


def set_rules(body, now):
    """The teacher changes the admission rules (this stores them; apply_rules also prepares the sign-in service)."""
    db().put({"pk": RULES_KEY, **_checked(body), "updated_at": now})
    return get_rules()


def check_portal_url(url):
    """The address students open the portal at. It is registered with the sign-in service as the place to come back to, so it is checked:
    https only, no path, and either the platform's own deploy address or a CloudFront address."""
    found = re.fullmatch(r"https://([a-z0-9][a-z0-9.-]{2,252})/?", str(url or "").strip().lower())
    root = os.environ.get("ROOT_DOMAIN", "").strip().lower()
    host = found.group(1) if found else ""
    if not host or not (host.endswith(".cloudfront.net") or (root and host == "deploy." + root)):
        raise PortalError(400, "portal_url must be the address of your portal, for example https://deploy.your-domain (https, no path).")
    return "https://" + host


def apply_rules(body, now, ensure=None):
    """The teacher's change end to end. When Google sign-in is on after this change, the portal's sign-in client is made FIRST, so a failure
    leaves the old rules in place. `ensure(portal_url)` is auth.ensure_portal_client by default."""
    wanted = _checked(body)  # every check, before anything is created
    if wanted["mode"] != "token-only":
        if not wanted["portal_url"]:
            raise PortalError(400, "Send portal_url (the address of your portal) when you turn Google sign-in on.")
        if ensure is None:
            import auth
            ensure = auth.ensure_portal_client
        ensure(wanted["portal_url"])
    return set_rules(body, now)


def split_email(email):
    local, _, domain = str(email or "").strip().lower().rpartition("@")
    return local, domain


def admit(email, email_verified, rules=None):
    """What may this verified Google identity do? {"level": member | requester | denied, "reason": short plain text}.
    The domain must match EXACTLY: 'school.edu' admits a@school.edu, not a@evilschool.edu and not a@x.school.edu."""
    rules = rules or get_rules()
    mode = rules["mode"]
    if mode == "token-only":
        return {"level": "denied", "reason": "Sign in with Google is not turned on for this platform. Use your token."}
    if email_verified is not True:  # only the boolean True counts: the text "true", 1 or a missing claim do not
        return {"level": "denied", "reason": "Google has not verified this e-mail address."}
    address = str(email or "").strip().lower()
    local, domain = split_email(address)
    if not local or not domain or not EMAIL_RE.match(address):
        return {"level": "denied", "reason": "This account has no usable e-mail address."}
    listed = address in rules["emails"]
    if mode in ("domain", "request") and domain in rules["domains"]:
        listed = True
    if listed:
        return {"level": "member", "reason": "Allowed by the teacher's list."}
    if mode == "request":
        return {"level": "requester", "reason": "You can sign in and ask your teacher for access."}
    return {"level": "denied", "reason": "This e-mail address is not on your teacher's list. Ask your teacher to add it."}


# ---------- student identities ----------
def user_key(sub):
    if not isinstance(sub, str) or not SUB_RE.match(sub):
        raise PortalError(400, "The sign-in did not carry a valid account id.")
    return USER_PREFIX + sub


def get_user(sub):
    return db().get(user_key(sub))


def upsert_user(sub, email, name, now):
    """Record a verified sign-in. A new identity is 'active'; a disabled one stays disabled (it is refused by the caller)."""
    key = user_key(sub)
    clean_email = str(email or "").strip().lower()[:254]
    clean_name = re.sub(r"[\x00-\x1f\x7f]", " ", str(name or "")).strip()[:80]
    record = {"pk": key, "sub": sub, "email": clean_email, "name": clean_name, "status": "active", "first_seen": now, "last_seen": now}
    if db().put(record, if_absent=True):
        return record
    return db().update(key, {"email": clean_email, "name": clean_name, "last_seen": now})


def set_user_status(sub, status, now):
    if status not in ("active", "disabled"):
        raise PortalError(400, "status must be active or disabled.")
    if not get_user(sub):
        raise PortalError(404, "No such student identity.")
    return db().update(user_key(sub), {"status": status, "status_at": now})


def list_users():
    return sorted(db().list_prefix(USER_PREFIX), key=lambda u: -u.get("last_seen", 0))


def user_view(user):
    return {"sub": user["sub"], "email": user.get("email", ""), "name": user.get("name", ""), "status": user.get("status", "active"),
            "first_seen": user.get("first_seen"), "last_seen": user.get("last_seen"), "approved": bool(user.get("granted"))}


# ---------- teacher routes ----------
def admin_route(method, path, body, now):
    """/api/admin/identity (GET, POST), /api/admin/identity/users (GET), /api/admin/identity/users/<sub>/(disable|enable) (POST).
    The caller has already checked the teacher's session. Returns (status, data), or None for an unknown route."""
    if path == "/api/admin/identity":
        if method == "GET":
            return 200, {"rules": get_rules(), "modes": list(MODES), "limits": {"emails": MAX_EMAILS, "domains": MAX_DOMAINS}}
        if method == "POST":
            return 200, {"rules": apply_rules(body, now)}
        return None
    if path == "/api/admin/identity/users" and method == "GET":
        return 200, {"users": [user_view(u) for u in list_users()]}
    found = re.fullmatch(r"/api/admin/identity/users/([A-Za-z0-9_.:-]{1,128})/erase", path)
    if found and method == "POST":  # the account and what hangs on it; its sites are only unlinked (delete them with the usual controls)
        if body.get("confirm") != found.group(1):
            raise PortalError(400, "Send confirm with the account id to erase it. This cannot be undone.")
        if not get_user(found.group(1)):
            raise PortalError(404, "No such student identity.")
        import access
        return 200, {"erased": access.erase_identity(found.group(1))}
    found = re.fullmatch(r"/api/admin/identity/users/([A-Za-z0-9_.:-]{1,128})/(disable|enable)", path)
    if found and method == "POST":
        user = set_user_status(found.group(1), "disabled" if found.group(2) == "disable" else "active", now)
        return 200, {"user": user_view(user)}
    return None


# ---------- the student's sign-in with Google ----------
# The page sends the student to Google (through the pool's sign-in page) with a PKCE challenge, and comes back with a one-time code.
# The code and the PKCE verifier come here; this function trades them for an ID token, checks the token, applies the teacher's rules, and
# hands back a "user" session. A user session is its own kind (own signing key): it names an account, not a token, and opens only the
# routes below, never a token's routes and never the teacher's.
CODE_RE = re.compile(r"^[A-Za-z0-9_.~-]{8,512}$")
VERIFIER_RE = re.compile(r"^[A-Za-z0-9_.~-]{43,128}$")


def google_config():
    """What the page needs to start a sign-in. Public: it holds no secret (the client has none; the sign-in uses PKCE)."""
    import auth
    rules = get_rules()
    info = auth.portal_signin_info()
    if rules["mode"] == "token-only" or not info:
        return {"enabled": False}
    return {"enabled": True, "host": info["host"], "client_id": info["client_id"], "redirect_uri": info["redirect_uri"], "scopes": auth.SCOPES,
            "mode": rules["mode"]}


def too_many(event, now):
    return db().counter_add(f"gl#{client_ip(event)}#{now // 60}", 1, limit=SIGNIN_PER_MINUTE, ttl=now + 180, now=now) is None


def google_login(body, event, now, post=None, fetch=None):
    import auth
    if too_many(event, now):
        raise PortalError(429, "Too many sign-in attempts. Wait a minute and try again.")
    info = auth.portal_signin_info()
    rules = get_rules()
    if rules["mode"] == "token-only" or not info:
        raise PortalError(403, "Sign in with Google is not turned on for this platform. Use your token.")
    code, verifier = str(body.get("code") or ""), str(body.get("verifier") or "")
    if not CODE_RE.match(code) or not VERIFIER_RE.match(verifier):
        raise PortalError(400, "The sign-in answer from Google is not valid. Start again.")
    id_token = auth.exchange_code(info, code, verifier, post)
    try:
        claims = jwtcheck.verify_id_token(id_token, info["issuer"], info["client_id"], jwtcheck.fetch_jwks(info["issuer"], now, fetch), now)
    except jwtcheck.TokenError as e:
        raise PortalError(401, str(e))
    verified = claims.get("email_verified") in (True, "true")  # Cognito writes the fact as a boolean or as the text "true"
    decision = admit(claims.get("email"), verified, rules)
    sub = claims["sub"]
    existing = get_user(sub)
    if decision["level"] == "denied" and existing and existing.get("granted") and verified:
        decision = {"level": "requester", "reason": "Approved by your teacher."}  # an approved student stays in even if the lists changed
    if decision["level"] == "denied":
        raise PortalError(403, decision["reason"])
    if existing and existing.get("status") == "disabled":
        raise PortalError(403, "This account has been turned off by your teacher.")
    user = upsert_user(sub, claims.get("email"), claims.get("name"), now)
    return {"session": sign_session("user", sub, STUDENT_SESSION_SECONDS, now), "expires_at": now + STUDENT_SESSION_SECONDS,
            "user": user_view(user), "level": effective_level(user, decision["level"])}


def current_user(event, now):
    """The signed-in Google student behind a request, checked again every time: the teacher's rules and the disabled switch apply at once."""
    sub = verify_session("user", event, now)
    user = db().get(USER_PREFIX + sub)
    if not user or user.get("status") == "disabled":
        raise PortalError(401, "Please sign in again.")
    decision = admit(user.get("email"), True)  # the stored address was verified when it was stored
    if decision["level"] == "denied" and not user.get("granted"):
        raise PortalError(403, decision["reason"])
    if get_rules()["mode"] == "token-only":
        raise PortalError(403, "Sign in with Google is not turned on for this platform. Use your token.")
    return user, effective_level(user, decision["level"])


def effective_level(user, level):
    """A student whose request the teacher approved is a member even if their address is on no list."""
    return "member" if user.get("granted") or level == "member" else "requester"


def owner_allowed(sub):
    """May the account behind a site or a paired computer still be used? Checked on every login and every request of such a session."""
    user = db().get(USER_PREFIX + sub) if isinstance(sub, str) and SUB_RE.match(sub) else None
    if not user or user.get("status") == "disabled":
        return False
    if get_rules()["mode"] == "token-only":
        return False
    return bool(user.get("granted")) or admit(user.get("email"), True)["level"] == "member"


def tokens_of(sub):
    return [t for t in db().list_prefix("tok#") if t.get("owner_sub") == sub]


def link_token(user, level, body, now):
    """A student who has a token and a Google account joins the two. Anyone holding the token can do this once (a token was always a bearer secret);
    after that the token belongs to that account and cannot be linked to another one."""
    import portal
    if level != "member":
        raise PortalError(403, "Your teacher has not given this account access yet.")
    token = str(body.get("token") or "").strip()
    item = db().get("tok#" + portal.token_hash(token)) if token else None
    if not item or item.get("revoked"):
        raise PortalError(401, "This token is not valid. Check it or ask your teacher.")
    if item.get("owner_sub") and item["owner_sub"] != user["sub"]:
        raise PortalError(409, "This token already belongs to another account.")
    return db().update(item["pk"], {"owner_sub": user["sub"], "linked_at": item.get("linked_at") or now})


def student_route(method, path, body, event, now, site_url, post=None, fetch=None):
    """/api/student/google/config (GET), /login (POST), /me (GET), /link (POST). Returns (status, data), or None for an unknown route."""
    import portal
    if (method, path) == ("GET", "/api/student/google/config"):
        return 200, google_config()
    if (method, path) == ("POST", "/api/student/google/login"):
        return 200, google_login(body, event, now, post, fetch)
    if (method, path) == ("GET", "/api/student/google/me"):
        user, level = current_user(event, now)
        return 200, {"user": user_view(user), "level": level, "tokens": [portal.view(i, now, site_url) for i in tokens_of(user["sub"])]}
    if (method, path) == ("POST", "/api/student/google/link"):
        user, level = current_user(event, now)
        item = link_token(user, level, body, now)
        return 200, {"token": portal.view(item, now, site_url)}
    if path.startswith("/api/student/google/"):  # home, requests, pairing, computers (access.py)
        import access
        user, level = current_user(event, now)
        return access.student_route(method, path, body, user, level, now, site_url)
    return None
