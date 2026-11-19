"""Access requests, the student's home, and personal computers (docs/STUDENT_IDENTITY_PLAN.md, phases 2 to 4).

A student who signs in with Google (identity.py) sees their sites, the class projects the teacher lists, and the state of their requests.
They can ask for a new site with the settings they expect, or ask to join a listed class project; the teacher accepts, rejects, or accepts with
changes. A site made this way has no token to hand out: the student pairs each computer with a code (like signing in to a TV), and the platform
gives that computer its own personal token, which works for 30 days and can be signed out one computer at a time.

Records in the one tokens table (all plain items; nothing here needs a new table):
  req#<id>     one request: kind (new_site | join_project), requester (account id), state, requested, granted, notes.
               States: pending -> approving -> approved | rejected | withdrawn. A failed approval goes back to pending with the reason, and the
               site it had already made is kept (site_id), so asking again never makes a second site.
  cred#<sha>   one paired computer: the SHA-256 of its personal token (the platform never sees the token), the site, the owner, when it expires.
  pair#<CODE>  a pairing in progress: the SHA-256 the computer chose, a label, and whether the student approved it. Short-lived.
  inv#<sha>    an e-mail invitation: the address (lower case) and the sites (projects) it was invited to. Applied at that address's first sign-in.

A project is a site: one address, one backend, one AI budget. Several people can work on it. The site item holds `members` ({account id: {at, by}}),
`invites` (addresses still waiting) and `owner_sub` (the account that asked for it, or linked its old token). Every member pairs their own computers
and gets their own personal token, so signing one person out never touches the others. The teacher's own token for a site is unchanged.

The grants use the teacher's own functions (backends, ai, storage, knowledge, traces, domains), so every limit the teacher has is the limit here.
"""
import hashlib
import re
import secrets

import identity
import platform_settings
import portal
from portal import DAY, PortalError, db

PERSONAL_DAYS = platform_settings.value("identity.personalTokenDays")
SITES_PER_STUDENT = platform_settings.value("identity.sitesPerStudent")
PENDING_PER_STUDENT = platform_settings.value("identity.pendingPerStudent")
REQUESTS_PER_DAY = platform_settings.value("identity.requestsPerDay")
PENDING_TOTAL = platform_settings.value("identity.pendingTotal")
REQUEST_TTL_DAYS = platform_settings.value("identity.requestTtlDays")
PAIR_MINUTES = platform_settings.value("identity.pairMinutes")
DEVICES_PER_SITE = platform_settings.value("identity.devicesPerSite")
PEOPLE_PER_PROJECT = platform_settings.value("identity.peoplePerProject")
NOTE_MAX = 500
PAIR_CODE_LENGTH = 8
PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: a code read off a screen and typed cannot be mistaken
PERSONAL_PREFIX = "mdp_"
INVITE = "inv#"

REQ = "req#"
CRED = "cred#"
PAIR = "pair#"
OPEN_STATES = ("pending", "approving")

# What a student may ask for, and with which keys. The values go to the same checks the teacher's own settings go through.
FEATURES = {
    "backend": ("enabled", "runs_limit", "runs_period"),
    "ai": ("enabled", "limit_usd", "period", "model", "provider"),
    "storage": ("enabled", "data_mb", "files_mb", "ops_limit", "ops_period"),
    "knowledge": ("enabled", "collections", "documents", "passages", "stored_mb", "upload_mb", "searches_limit", "searches_period", "embed_tokens"),
    "traces": ("enabled", "level", "retention_days", "student_view", "daily_limit"),
    "domain": ("enabled",),
}
TEXT_RE = re.compile(r"^[A-Za-z0-9_./:-]{1,60}$")
ID_RE = re.compile(r"^[0-9a-f]{12}$")


# ---------- reading what a request or feature block says ----------
def clean_text(value, what, limit=NOTE_MAX):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PortalError(400, f"{what} must be text.")
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", value).strip()[:limit]


def clean_features(value):
    """Only the known features and keys; booleans, plain numbers and short plain words. Anything else is refused, not dropped."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PortalError(400, "features must be an object.")
    out = {}
    for name, block in value.items():
        if name not in FEATURES:
            raise PortalError(400, f"Unknown feature '{str(name)[:30]}'. Known: {', '.join(FEATURES)}.")
        if not isinstance(block, dict):
            raise PortalError(400, f"{name} must be an object.")
        clean = {}
        for key, v in block.items():
            if key not in FEATURES[name]:
                raise PortalError(400, f"{name}.{str(key)[:30]} is not something you can ask for.")
            if isinstance(v, bool):
                clean[key] = v
            elif isinstance(v, (int, float)) and v == v and 0 <= v <= 1_000_000_000:
                clean[key] = v
            elif isinstance(v, str) and TEXT_RE.match(v):
                clean[key] = v
            else:
                raise PortalError(400, f"{name}.{key} has a value that is not allowed.")
        if clean:
            out[name] = clean
    return out


def clean_days(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= portal.MAX_DAYS:
        raise PortalError(400, f"days must be a whole number from 1 to {portal.MAX_DAYS}.")
    return value


def clean_slug(value):
    if value in (None, ""):
        return ""
    slug = str(value).strip().lower()
    if not portal.SLUG_RE.match(slug) or slug in portal.RESERVED:
        raise PortalError(400, "A site name uses 1 to 30 lowercase letters, digits or '-' and is not a reserved name.")
    return slug


# ---------- requests ----------
def all_requests():
    return db().list_prefix(REQ)


def get_request(rid):
    item = db().get(REQ + rid) if isinstance(rid, str) and ID_RE.match(rid) else None
    if not item:
        raise PortalError(404, "No such request.")
    return item


def request_view(r, with_requester=False):
    out = {"id": r["id"], "kind": r["kind"], "state": r["state"], "requested": r.get("requested") or {}, "granted": r.get("granted"),
           "note": r.get("note", ""), "teacher_note": r.get("teacher_note", ""), "created_at": r["created_at"], "decided_at": r.get("decided_at"),
           "error": r.get("error", "")}
    if r.get("site_id"):
        out["site"] = r.get("site_slug", "")
    if with_requester:
        user = db().get(identity.USER_PREFIX + r["requester"]) or {}
        out["requester"] = {"sub": r["requester"], "email": user.get("email", ""), "name": user.get("name", "")}
    return out


def owned_sites(sub):
    return sorted(identity.tokens_of(sub), key=lambda t: t["created_at"])


def is_member(site, sub):
    """Does this account belong to the site (as the one who asked for it, or as a person the teacher added)?"""
    return bool(sub) and (site.get("owner_sub") == sub or sub in (site.get("members") or {}))


def member_sites(sub):
    return sorted([t for t in db().list_prefix("tok#") if is_member(t, sub)], key=lambda t: t["created_at"])


def any_member_allowed(site):
    """A site made for Google accounts works while at least one of its people may still sign in."""
    subs = {site.get("owner_sub"), *(site.get("members") or {})} - {None}
    return not subs or any(identity.owner_allowed(s) for s in subs)


def submit(user, level, body, now):
    sub = user["sub"]
    kind = body.get("kind")
    if kind not in ("new_site", "join_project"):
        raise PortalError(400, "kind must be new_site or join_project.")
    mine = [r for r in all_requests() if r["requester"] == sub]
    if sum(1 for r in mine if r["state"] in OPEN_STATES) >= PENDING_PER_STUDENT:
        raise PortalError(429, f"You already have {PENDING_PER_STUDENT} requests waiting. Wait for your teacher, or withdraw one.")
    if sum(1 for r in mine if r["created_at"] > now - DAY) >= REQUESTS_PER_DAY:
        raise PortalError(429, "You have sent many requests today. Try again tomorrow.")
    if sum(1 for r in all_requests() if r["state"] in OPEN_STATES) >= PENDING_TOTAL:
        raise PortalError(503, "Your teacher's inbox is full right now. Ask your teacher.")
    note = clean_text(body.get("note"), "note")
    record = {"pk": "", "id": secrets.token_hex(6), "kind": kind, "requester": sub, "state": "pending", "note": note, "created_at": now,
              "ttl": now + REQUEST_TTL_DAYS * DAY}
    if kind == "new_site":
        if len(owned_sites(sub)) >= SITES_PER_STUDENT:
            raise PortalError(409, f"One account can own at most {SITES_PER_STUDENT} sites.")
        requested = {"days": clean_days(body.get("days")), "slug": clean_slug(body.get("slug")), "features": clean_features(body.get("features"))}
    else:
        if level != "member":
            raise PortalError(403, "Ask for a site first. You can join a class project once you have one.")
        project = _catalogue_project(body.get("project"))
        sites = member_sites(sub)
        wanted = body.get("site")
        site = next((s for s in sites if s["id"] == wanted), None) if wanted else (sites[0] if len(sites) == 1 else None)
        if not site:
            raise PortalError(400, "Say which of your sites should join (site), or make a site first.")
        if site["slug"] in (project.get("members") or []):
            raise PortalError(409, "This site is already in that project.")
        if any(r["kind"] == "join_project" and r["state"] in OPEN_STATES and r["requested"]["project"] == project["name"] and r["requested"]["site"] == site["id"] for r in mine):
            raise PortalError(409, "You already asked for this.")
        requested = {"project": project["name"], "site": site["id"], "slug": site["slug"]}
    record["requested"] = requested
    record["pk"] = REQ + record["id"]
    db().put(record)
    return record


def _catalogue_project(ref):
    import projects
    found = projects.load("t", projects.team_name(ref)) if isinstance(ref, str) and projects.TEAM_RE.match(ref) else None
    if not found or not found.get("catalogue"):
        raise PortalError(404, "No such class project.")
    return found


def withdraw(user, rid):
    r = get_request(rid)
    if r["requester"] != user["sub"]:
        raise PortalError(404, "No such request.")  # not "forbidden": another student's request is not even confirmed to exist
    if r["state"] != "pending":
        raise PortalError(409, "This request is already being decided.")
    if not db().replace_if({**r, "state": "withdrawn", "decided_at": r.get("decided_at")}, "state", "pending"):
        raise PortalError(409, "This request is already being decided.")
    return db().get(REQ + rid)


# ---------- the teacher's decision ----------
def _grant_from(body, requested, now):
    """The final values: the request as asked, or the teacher's own version of it."""
    if body.get("granted") is None:
        return {k: v for k, v in requested.items()}
    g = body["granted"]
    if not isinstance(g, dict):
        raise PortalError(400, "granted must be an object.")
    if requested.get("project"):
        return dict(requested)
    return {"days": clean_days(g.get("days", requested.get("days"))), "slug": clean_slug(g.get("slug", requested.get("slug"))),
            "features": clean_features(g.get("features", requested.get("features")))}


def create_site(user, days, slug, now):
    """A site that belongs to a Google account. Nobody is handed a token for it: each computer is paired (see start_pair)."""
    secret = PERSONAL_PREFIX + "site_" + secrets.token_urlsafe(24)  # only its hash is kept, and nobody ever sees the text
    tid = portal.token_hash(secret)
    name = user.get("name") or user.get("email", "").split("@")[0]
    chosen = portal.claim_slug(slug, name, tid)
    item = {"pk": f"tok#{tid}", "id": tid, "slug": chosen, "student_name": (name or "")[:60], "hint": "pair", "created_at": now, "expires_at": now + days * DAY,
            "revoked": False, "regen": {"state": "none"}, "kind": "personal", "owner_sub": user["sub"]}
    try:
        db().put(item)
    except Exception:
        db().delete(f"slug#{chosen}")
        raise
    return item


def _apply_features(item, features, now, appliers):
    import ai, backends, domains, knowledge, storage, traces
    table = appliers or {
        "backend": lambda i, b: backends.set_backend(i, b, now),
        "storage": lambda i, b: storage.set_storage(i, b, now),
        "knowledge": lambda i, b: knowledge.set_knowledge(i, b, now),
        "ai": lambda i, b: ai.set_ai(i, b, now),
        "traces": lambda i, b: (traces.set_settings(i, b), db().get(i["pk"]))[1],
        "domain": lambda i, b: domains.set_access(i, b.get("enabled")) if "enabled" in b else i,
    }
    for name in ("backend", "storage", "knowledge", "ai", "traces", "domain"):  # the backend first: the others add to its function
        if name in features:
            try:
                item = table[name](item, features[name]) or db().get(item["pk"])
            except PortalError as e:
                raise PortalError(e.status, f"{name}: {e}")
    return item


def heal_stale(now):
    """A decision that was interrupted (the function timed out half way) is put back to waiting after five minutes, so the teacher can answer again.
    What it had already made (the site) is kept and reused."""
    for r in all_requests():
        if r["state"] == "approving" and now - r.get("approving_at", 0) > 300:
            db().replace_if({**r, "state": "pending", "error": "The last try was interrupted. Answer again."}, "state", "approving")


def decide(rid, body, now, site_url, appliers=None):
    """The teacher answers a request. action: accept (as asked), modify (with granted: {days, slug, features}), or reject."""
    heal_stale(now)
    r = get_request(rid)
    action = body.get("action")
    if action not in ("accept", "modify", "reject"):
        raise PortalError(400, "action must be accept, modify or reject.")
    note = clean_text(body.get("note"), "note")
    if r["state"] != "pending":
        raise PortalError(409, f"This request is {r['state']}, so it cannot be decided now.")
    if action == "reject":
        if not db().replace_if({**r, "state": "rejected", "teacher_note": note, "decided_at": now, "error": ""}, "state", "pending"):
            raise PortalError(409, "This request was just decided by someone else.")
        return request_view(db().get(REQ + rid), True)
    if action == "modify" and body.get("granted") is None:
        raise PortalError(400, "modify needs granted (the settings you give instead).")
    granted = _grant_from(body, r["requested"], now)  # every check before anything is created
    user = db().get(identity.USER_PREFIX + r["requester"])
    if not user or user.get("status") == "disabled":
        raise PortalError(409, "The student's account is not active.")
    if not db().replace_if({**r, "state": "approving", "approving_at": now}, "state", "pending"):  # only one decision can win
        raise PortalError(409, "This request was just decided by someone else.")
    r = db().get(REQ + rid)
    try:
        if r["kind"] == "new_site":
            if r.get("site_id"):
                item = db().get("tok#" + r["site_id"])
            else:
                item = create_site(user, granted["days"], granted["slug"], now)
                r = db().update(r["pk"], {"site_id": item["id"], "site_slug": item["slug"]})
            if granted.get("features"):
                _apply_features(item, granted["features"], now, appliers)
            if not user.get("granted"):
                db().update(user["pk"], {"granted": True})  # an approved student is let in even if their address is not on a list
        else:
            _join(r["requested"], user)
        done = {**db().get(REQ + rid), "state": "approved", "granted": granted, "teacher_note": note, "decided_at": now, "error": ""}
        db().put(done)
    except Exception as e:  # nothing is lost: back to pending, with the reason, and the site (if made) is reused next time
        message = str(e) if isinstance(e, PortalError) else "Something went wrong while granting this. Try again."
        db().put({**db().get(REQ + rid), "state": "pending", "error": message[:300]})
        raise
    return request_view(db().get(REQ + rid), True)


def _join(requested, user):
    import projects
    project = projects.load("t", requested["project"])
    site = db().get("tok#" + requested["site"])
    if not project or not site or not is_member(site, user["sub"]):
        raise PortalError(409, "The project or the site is gone.")
    members = list(project.get("members") or [])
    if site["slug"] not in members:
        if len(members) >= projects.MAX_MEMBERS:
            raise PortalError(409, "The project is full.")
        db().update(project["pk"], {"members": members + [site["slug"]]})


# ---------- the student's home ----------
def home(user, level, now, site_url):
    sub = user["sub"]
    sites = member_sites(sub)
    mine = [r for r in all_requests() if r["requester"] == sub]
    mine.sort(key=lambda r: -r["created_at"])
    out = {"user": identity.user_view(user), "level": level,
           "sites": [{**portal.view(s, now, site_url), "people": len(set(s.get("members") or {}) | ({s["owner_sub"]} if s.get("owner_sub") else set()))} for s in sites],
           "requests": [request_view(r) for r in mine], "projects": [],
           "limits": {"sites": SITES_PER_STUDENT, "pending": PENDING_PER_STUDENT}}
    if level == "member":
        slugs = {s["slug"]: s["id"] for s in sites}
        for p in sorted(db().list_prefix("proj#t#"), key=lambda p: p["name"]):
            if not p.get("catalogue"):
                continue
            inside = [s for s in p.get("members") or [] if s in slugs]
            waiting = [r for r in mine if r["kind"] == "join_project" and r["state"] in OPEN_STATES and r["requested"]["project"] == p["name"]]
            out["projects"].append({"id": p["name"], "title": p.get("title") or p["name"], "members": len(p.get("members") or []), "frozen": bool(p.get("frozen")),
                                    "state": "member" if inside else ("pending" if waiting else "none"), "sites": inside,
                                    "request": waiting[0]["id"] if waiting else None})
    return out


# ---------- paired computers ----------
def _norm_code(value):
    code = re.sub(r"[\s-]", "", str(value or "")).upper()
    if len(code) != PAIR_CODE_LENGTH or any(c not in PAIR_ALPHABET for c in code):
        raise PortalError(400, "That code is not valid. It has 8 letters and numbers, as shown in your terminal.")
    return code


def _shown(code):
    return code[:4] + "-" + code[4:]


def start_pair(body, now):
    """A computer asks for a code. It sends the SHA-256 of a token it made itself; the token never leaves the computer."""
    digest = str(body.get("hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PortalError(400, "hash must be the SHA-256 of the token the computer made.")
    device = clean_text(body.get("device"), "device", 40) or "A computer"
    for _ in range(8):
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LENGTH))
        record = {"pk": PAIR + code, "hash": digest, "device": device, "state": "pending", "created_at": now, "expires_at": now + PAIR_MINUTES * 60,
                  "ttl": now + PAIR_MINUTES * 60 + 3600}
        if db().put(record, if_absent=True):
            return {"code": _shown(code), "expires_at": record["expires_at"], "minutes": PAIR_MINUTES}
    raise PortalError(503, "Could not make a code. Try again.")


def _pair(code, now):
    item = db().get(PAIR + _norm_code(code))
    if not item or now >= item["expires_at"]:
        raise PortalError(404, "That code is not known or has expired. Run npm run login again for a new one.")
    return item


def poll_pair(body, now):
    item = db().get(PAIR + _norm_code(body.get("code")))
    if not item or str(body.get("hash") or "") != item["hash"]:
        return {"state": "unknown"}  # the same answer for a wrong code and a wrong hash
    if item["state"] == "pending" and now >= item["expires_at"]:
        return {"state": "expired"}
    return {"state": item["state"]}


def pair_info(code, now):
    item = _pair(code, now)
    return {"device": item["device"], "state": item["state"], "expires_at": item["expires_at"]}


def approve_pair(user, level, body, now):
    if level != "member":
        raise PortalError(403, "Your teacher has not given this account access yet.")
    item = _pair(body.get("code"), now)
    if item["state"] != "pending":
        raise PortalError(409, "That code was already answered.")
    sites = member_sites(user["sub"])
    wanted = body.get("site")
    site = next((s for s in sites if s["id"] == wanted), None) if wanted else (sites[0] if len(sites) == 1 else None)
    if not site:
        raise PortalError(400, "Say which of your sites this computer is for (site).")
    if portal.status_of(site, now) == "revoked" or site.get("revoked"):
        raise PortalError(409, "That site is revoked.")
    live = [c for c in db().list_prefix(CRED) if c["site_id"] == site["id"] and c["owner"] == user["sub"] and c["expires_at"] > now]
    if len(live) >= DEVICES_PER_SITE:
        raise PortalError(409, f"You already have {DEVICES_PER_SITE} computers paired with this project. Sign one out first.")
    cred = {"pk": CRED + item["hash"][:32], "hash": item["hash"], "cid": item["hash"][:32], "site_id": site["id"], "owner": user["sub"], "device": item["device"],
            "created_at": now, "expires_at": now + PERSONAL_DAYS * DAY, "last_used": 0}
    if not db().put(cred, if_absent=True):
        raise PortalError(409, "That computer is already paired.")
    if not db().replace_if({**item, "state": "approved", "site_id": site["id"]}, "state", "pending"):
        db().delete(CRED + item["hash"][:32])
        raise PortalError(409, "That code was already answered.")
    return {"device": item["device"], "site": site["slug"], "expires_at": cred["expires_at"]}


def deny_pair(body, now):
    item = _pair(body.get("code"), now)
    if not db().replace_if({**item, "state": "denied"}, "state", "pending"):
        raise PortalError(409, "That code was already answered.")
    return {"state": "denied"}


def devices(user, now):
    sites = {s["id"]: s for s in member_sites(user["sub"])}
    rows = [c for c in db().list_prefix(CRED) if c["owner"] == user["sub"]]
    rows.sort(key=lambda c: -c["created_at"])
    return [{"id": c["cid"], "device": c["device"], "site": sites[c["site_id"]]["slug"] if c["site_id"] in sites else "", "created_at": c["created_at"],
             "expires_at": c["expires_at"], "last_used": c.get("last_used") or None, "expired": c["expires_at"] <= now} for c in rows]


def revoke_device(user, cid):
    cred = db().get(CRED + cid) if re.fullmatch(r"[0-9a-f]{32}", str(cid)) else None
    if not cred or cred["owner"] != user["sub"]:
        raise PortalError(404, "No such computer.")  # another student's computer is not confirmed to exist
    db().delete(cred["pk"])
    return {"revoked": cid}


def sign_out_everywhere(user):
    count = 0
    for c in db().list_prefix(CRED):
        if c["owner"] == user["sub"]:
            db().delete(c["pk"])
            count += 1
    return {"revoked": count}


def credential_login(digest, now):
    """A personal token presented at login. Returns (credential, site) or None when it is not a personal token. Anything wrong is a plain 401."""
    cred = db().get(CRED + digest[:32])
    if not cred or cred["hash"] != digest:
        return None
    site = db().get("tok#" + cred["site_id"])
    if now >= cred["expires_at"]:
        raise PortalError(401, "This computer's sign-in has expired. Run  npm run login  to pair it again.")
    if not site or site.get("revoked") or not identity.owner_allowed(cred["owner"]) or not is_member(site, cred["owner"]):
        raise PortalError(401, "This sign-in is no longer valid. Run  npm run login  to pair this computer again.")
    if now - cred.get("last_used", 0) > 3600:
        db().update(cred["pk"], {"last_used": now})
    return cred, site


def credential_ok(cid, site_id, now):
    """Checked on every request of a session that came from a personal token: signing a computer out takes effect at once."""
    cred = db().get(CRED + cid) if re.fullmatch(r"[0-9a-f]{32}", str(cid)) else None
    if not cred or cred["site_id"] != site_id or now >= cred["expires_at"] or not identity.owner_allowed(cred["owner"]):
        return False
    site = db().get("tok#" + site_id)
    return bool(site and is_member(site, cred["owner"]))  # taking a person off the project ends their computers' sessions at the next request


# ---------- the routes ----------
def student_route(method, path, body, user, level, now, site_url):
    """Returns (status, data) or None. `user` and `level` come from a checked Google session."""
    if (method, path) == ("GET", "/api/student/google/home"):
        return 200, home(user, level, now, site_url)
    if (method, path) == ("POST", "/api/student/google/requests"):
        return 201, {"request": request_view(submit(user, level, body, now))}
    found = re.fullmatch(r"/api/student/google/requests/([0-9a-f]{12})/withdraw", path)
    if found and method == "POST":
        return 200, {"request": request_view(withdraw(user, found.group(1)))}
    if (method, path) == ("POST", "/api/student/google/pair"):
        return 200, approve_pair(user, level, body, now)
    if (method, path) == ("POST", "/api/student/google/pair/deny"):
        return 200, deny_pair(body, now)
    found = re.fullmatch(r"/api/student/google/pair/([A-Za-z0-9-]{8,12})", path)
    if found and method == "GET":
        return 200, pair_info(found.group(1), now)
    if (method, path) == ("GET", "/api/student/google/devices"):
        return 200, {"devices": devices(user, now)}
    found = re.fullmatch(r"/api/student/google/devices/([0-9a-f]{32})/revoke", path)
    if found and method == "POST":
        return 200, revoke_device(user, found.group(1))
    if (method, path) == ("POST", "/api/student/google/sign-out-everywhere"):
        return 200, sign_out_everywhere(user)
    return None


def public_route(method, path, body, event, now):
    """The two calls a computer makes before anybody is signed in: /api/student/pair/start and /api/student/pair/poll."""
    if method != "POST" or path not in ("/api/student/pair/start", "/api/student/pair/poll"):
        return None
    if identity.get_rules()["mode"] == "token-only":
        raise PortalError(403, "Sign in with Google is not turned on for this platform. Use your token.")
    if path.endswith("/start"):
        if identity.too_many(event, now):
            raise PortalError(429, "Too many attempts. Wait a minute and try again.")
        return 200, start_pair(body, now)
    return 200, poll_pair(body, now)  # polling needs the secret hash and reads one item; a computer waits patiently, so it is not counted


def admin_route(method, path, body, now, site_url):
    """/api/admin/access-requests (GET ?state=), /api/admin/access-requests/{id}/decide (POST)."""
    if path == "/api/admin/access-requests" and method == "GET":
        heal_stale(now)
        rows = sorted(all_requests(), key=lambda r: (r["state"] not in OPEN_STATES, -r["created_at"]))
        return 200, {"requests": [request_view(r, True) for r in rows], "open": sum(1 for r in rows if r["state"] == "pending")}
    found = re.fullmatch(r"/api/admin/access-requests/([0-9a-f]{12})/decide", path)
    if found and method == "POST":
        return 200, {"request": decide(found.group(1), body, now, site_url)}
    return None


def erase_identity(sub):
    """Remove everything this module and identity.py hold about one account: the account, its requests, its paired computers, its pairings.
    Its sites are NOT deleted (that is the teacher's decision, with the existing controls); they are only unlinked from the account."""
    counts = {"requests": 0, "computers": 0, "pairings": 0, "sites_unlinked": 0}
    for r in all_requests():
        if r["requester"] == sub:
            db().delete(r["pk"])
            counts["requests"] += 1
    for c in db().list_prefix(CRED):
        if c["owner"] == sub:
            db().delete(c["pk"])
            counts["computers"] += 1
    for t in identity.tokens_of(sub):
        db().update(t["pk"], {"owner_sub": None, "revoked": True} if t.get("kind") == "personal" else {"owner_sub": None})
        counts["sites_unlinked"] += 1
    for t in db().list_prefix("tok#"):
        if sub in (t.get("members") or {}):
            db().update(t["pk"], {"members": {k: v for k, v in t["members"].items() if k != sub}})
    db().delete(identity.USER_PREFIX + sub)
    return counts


# ---------- the people on a project (the teacher's card) ----------
def clean_email(value):
    address = str(value or "").strip().lower()
    if not identity.EMAIL_RE.match(address):
        raise PortalError(400, f"'{address[:60]}' is not an e-mail address.")
    return address


def clean_emails(value):
    if isinstance(value, str):
        value = re.split(r"[\s,;]+", value)
    if not isinstance(value, list):
        raise PortalError(400, "emails is a list of e-mail addresses.")
    out = []
    for v in value:
        if str(v or "").strip() == "":
            continue
        address = clean_email(v)
        if address not in out:
            out.append(address)
    if not out:
        raise PortalError(400, "Give at least one e-mail address.")
    if len(out) > 100:
        raise PortalError(400, "Add at most 100 addresses at a time.")
    return out


def invite_key(address):
    return INVITE + sha256_hex(address)[:32]


def get_invite(address):
    address = str(address or "").strip().lower()
    return db().get(invite_key(address)) if address else None


def has_invite(address):
    return bool(get_invite(address))


def user_by_email(address):
    return next((u for u in identity.list_users() if u.get("email") == address), None)


def people_view(site):
    members = site.get("members") or {}
    subs = list(members)
    if site.get("owner_sub") and site["owner_sub"] not in members:
        subs.insert(0, site["owner_sub"])
    people = []
    for sub in subs:
        user = db().get(identity.USER_PREFIX + sub) or {}
        people.append({"sub": sub, "email": user.get("email", ""), "name": user.get("name", ""), "status": user.get("status", "active"),
                       "owner": sub == site.get("owner_sub"), "since": (members.get(sub) or {}).get("at") or site.get("linked_at")})
    return {"members": people, "invites": sorted(site.get("invites") or []), "limit": PEOPLE_PER_PROJECT}


def add_people(site, emails, now):
    """Put people on a project by e-mail. Someone who has already signed in becomes a member at once; the others are invited and become
    members at their first sign-in. Either way the address is let in even if it is on no list. Returns what happened to each address."""
    site = db().get(site["pk"])
    if site.get("revoked"):
        raise PortalError(409, "This project is revoked.")
    members, invites = dict(site.get("members") or {}), list(site.get("invites") or [])
    result = {"added": [], "invited": [], "already": [], "refused": []}
    for address in emails:
        user = user_by_email(address)
        if user and (user["sub"] in members or site.get("owner_sub") == user["sub"]):
            result["already"].append(address)
            continue
        if not user and address in invites:
            result["already"].append(address)
            continue
        if len(members) + len(invites) >= PEOPLE_PER_PROJECT:
            result["refused"].append({"email": address, "reason": f"The project is full ({PEOPLE_PER_PROJECT} people)."})
            continue
        if user:
            if user.get("status") == "disabled":
                result["refused"].append({"email": address, "reason": "This account is turned off. Turn it on in the Google tab first."})
                continue
            members[user["sub"]] = {"at": now, "by": "teacher"}
            if not user.get("granted"):
                db().update(user["pk"], {"granted": True})  # a person the teacher put on a project is let in
            result["added"].append(address)
        else:
            invites.append(address)
            record = get_invite(address) or {"pk": invite_key(address), "email": address, "sites": []}
            if site["id"] not in record["sites"]:
                record["sites"] = [*record["sites"], site["id"]]
            db().put(record)
            result["invited"].append(address)
    db().update(site["pk"], {"members": members, "invites": invites})
    return result


def apply_invites(user, now):
    """A person who has just signed in with a verified address: every project that invited this address now has them as a member."""
    invite = get_invite(user.get("email"))
    if not invite:
        return 0
    joined = 0
    for site_id in invite.get("sites", []):
        site = db().get("tok#" + site_id)
        if not site or site.get("revoked"):
            continue
        members = dict(site.get("members") or {})
        members.setdefault(user["sub"], {"at": now, "by": "invite"})
        db().update(site["pk"], {"members": members, "invites": [e for e in site.get("invites") or [] if e != user["email"]]})
        joined += 1
    db().delete(invite["pk"])
    if joined and not user.get("granted"):
        db().update(user["pk"], {"granted": True})
    return joined


def remove_person(site, body, now):
    """Take a person (or a waiting invitation) off a project. Their computers stop at their next request; their other projects are untouched."""
    site = db().get(site["pk"])
    sub, address = body.get("sub"), body.get("email")
    if not sub and not address:
        raise PortalError(400, "Say who to remove: sub (an account id) or email.")
    members, invites = dict(site.get("members") or {}), list(site.get("invites") or [])
    if address:
        address = clean_email(address)
        if address in invites:
            invites.remove(address)
            record = get_invite(address)
            if record:
                left = [s for s in record["sites"] if s != site["id"]]
                db().put({**record, "sites": left}) if left else db().delete(record["pk"])
        user = user_by_email(address)
        if user and not sub:
            sub = user["sub"]
    removed = False
    if sub:
        if sub in members:
            members.pop(sub)
            removed = True
        fields = {"members": members, "invites": invites}
        if site.get("owner_sub") == sub:
            fields["owner_sub"] = None
            removed = True
        db().update(site["pk"], fields)
        for c in db().list_prefix(CRED):
            if c["owner"] == sub and c["site_id"] == site["id"]:
                db().delete(c["pk"])
    else:
        db().update(site["pk"], {"invites": invites})
    return {"removed": removed or bool(address)}


def people_route(site, method, remove, body, now):
    """/api/admin/tokens/{id}/people (GET list, POST {emails} add) and /people/remove (POST {sub or email})."""
    if remove and method == "POST":
        out = remove_person(site, body, now)
        return 200, {**out, **people_view(db().get(site["pk"]))}
    if not remove and method == "GET":
        return 200, people_view(site)
    if not remove and method == "POST":
        out = add_people(site, clean_emails(body.get("emails")), now)
        return 200, {"result": out, **people_view(db().get(site["pk"]))}
    return None


def sha256_hex(text):
    return hashlib.sha256(text.encode()).hexdigest()
