"""Class AI connector: the teacher can give a student's backend an AI key that is capped, revocable and metered by OpenRouter.

State lives on the token item (item["ai"]):
  teacher:  enabled, limit_usd (stored as whole micro-dollars), period (day | month | total), model
  platform: hash + key of the student's OpenRouter key, key_disabled, used_micro, checked_at

What the student's function receives (only while the connector is on): OPENAI_API_KEY, OPENAI_BASE_URL, AI_MODEL. Any OpenAI-compatible
SDK, or a plain fetch, works with them. The key is the student's own capped key, never the platform's management key, so it cannot
spend more than the cap and stops the moment the teacher switches AI off or the token is revoked or expires. The key is never part
of any API view. Money is kept as integers (DynamoDB has no floats) and shown in dollars.
"""
import os
import re
import secrets
import time

from botocore.exceptions import ClientError

import backends
import gemini
import openrouter
import portal
from portal import PortalError, db

AI_ENV_NAMES = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "AI_MODEL")
PERIODS = ("day", "month", "total")
PROVIDERS = ("openrouter", "gemini")
MICRO = 1_000_000
MIN_LIMIT_USD, MAX_LIMIT_USD = 0.01, 20.0
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/~-]{0,79}$")
REFRESH_SECONDS = 20  # the student page asks again at most this often


def available():
    return os.environ.get("AI_ENABLED", "") == "true" and backends.available()


def default_model():
    return os.environ.get("AI_DEFAULT_MODEL", "anthropic/claude-haiku-5.5")


def proxy_url():
    return os.environ.get("AI_PROXY_URL", "").rstrip("/")


def gemini_enabled():
    """The feature flag alone (the proxy function does not know its own address: a function cannot refer to the API that calls it)."""
    return available() and os.environ.get("AI_GEMINI_ENABLED", "") == "true"


def gemini_available():
    """Gemini needs the platform's own proxy (Google has no per-student keys); see gemini.py and ai_proxy.py."""
    return available() and os.environ.get("AI_GEMINI_ENABLED", "") == "true" and bool(proxy_url())


def provider(item):
    """Gemini is the default for a student who never had AI (when the server offers it); students already on OpenRouter stay there."""
    s = settings(item)
    chosen = s.get("provider") or ("openrouter" if (s.get("hash") or s.get("key")) else "gemini" if gemini_available() else "openrouter")
    return chosen if chosen in PROVIDERS else "openrouter"


def model_of(item):
    """The model this student gets: the teacher's choice, else the default of their provider."""
    chosen = settings(item).get("model")
    if provider(item) == "gemini":
        return chosen if chosen in gemini.models() else gemini.DEFAULT_MODEL
    return chosen or default_model()


def limit_micro(item):
    return int(settings(item).get("limit_micro", round(default_limit_usd() * MICRO)))


def _usage_pk(item, now):
    period_id, resets_at = backends.period_info(settings(item).get("period", "month"), now)
    return f"aiu#{item['slug']}#{period_id}", resets_at


def gemini_used_micro(item, now):
    return int((db().get(_usage_pk(item, now)[0]) or {}).get("count", 0))


def add_gemini_usage(item, micro, now):
    """Add the cost of one answer (no limit here: it was already spent; the check happens before the call)."""
    if micro <= 0:
        return
    pk, resets_at = _usage_pk(item, now)
    db().counter_add(pk, micro, ttl=(resets_at + 86400) if resets_at else None, now=now)


def default_limit_usd():
    return float(os.environ.get("AI_DEFAULT_LIMIT_USD", "0.25"))


def base_url():
    return os.environ.get("AI_BASE_URL", openrouter.BASE_URL)


def student_timeout():
    return int(os.environ.get("AI_STUDENT_TIMEOUT", "25"))


def typical_call_usd():
    """Rough price of one ordinary answer (about 1,000 tokens in, 500 out) with the default model. Shown only as a hint."""
    return float(os.environ.get("AI_TYPICAL_CALL_USD", "0.00035"))


def settings(item):
    return dict(item.get("ai") or {})


def _save(item, **fields):
    return db().update(item["pk"], {"ai": {**settings(item), **fields}})


def is_on(item):
    s = settings(item)
    return bool(s.get("enabled") and s.get("gkey" if provider(item) == "gemini" else "key"))


def env_for(item):
    """The variables the student's function gets, or {} when the connector is off."""
    if not (available() and is_on(item)):
        return {}
    if provider(item) == "gemini":
        if not gemini_available():
            return {}
        return {"OPENAI_API_KEY": settings(item)["gkey"], "OPENAI_BASE_URL": proxy_url() + "/v1", "AI_MODEL": model_of(item)}
    return {"OPENAI_API_KEY": settings(item)["key"], "OPENAI_BASE_URL": base_url(), "AI_MODEL": model_of(item)}


def timeout_for(item):
    return student_timeout() if (available() and is_on(item)) else backends.TIMEOUT_SECONDS


def reserved_name(item, name):
    """While the connector is on these three names belong to the platform, so a student cannot overwrite or delete them."""
    return available() and is_on(item) and name in AI_ENV_NAMES


# ---------- views ----------
def view(item, now):
    if not available():
        return {"available": False}
    s = settings(item)
    limit_micro = s.get("limit_micro", round(default_limit_usd() * MICRO))
    used_micro = int(s.get("used_micro", 0))
    model = model_of(item)
    gem = provider(item) == "gemini"
    if gem:
        used_micro = gemini_used_micro(item, now)  # our own counter: always current
    out = {
        "available": True, "enabled": bool(s.get("enabled")), "model": model, "base_url": (proxy_url() + "/v1") if gem else base_url(),
        "default_model": default_model(), "provider": provider(item), "providers": ["openrouter"] + (["gemini"] if gemini_available() else []), "gemini_models": gemini.models(),
        "limit_usd": limit_micro / MICRO, "period": s.get("period", "month"),
        "used_usd": used_micro / MICRO, "remaining_usd": max(limit_micro - used_micro, 0) / MICRO,
        "checked_at": s.get("checked_at"), "env_names": list(AI_ENV_NAMES) if s.get("enabled") else [], "timeout_seconds": student_timeout(),
        "key_active": True if gem else bool(s.get("key")) and not s.get("key_disabled", False),
    }
    if not gem and model == default_model() and typical_call_usd() > 0:
        out["approx_calls"] = int(limit_micro / MICRO / typical_call_usd())
    return out


# ---------- teacher ----------
def _parse(body, item):
    changes = {}
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise PortalError(400, "enabled must be true or false.")
        changes["enabled"] = body["enabled"]
    if "limit_usd" in body:
        limit = body["limit_usd"]
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not MIN_LIMIT_USD <= limit <= MAX_LIMIT_USD:
            raise PortalError(400, f"limit_usd must be a number from {MIN_LIMIT_USD} to {MAX_LIMIT_USD}.")
        changes["limit_micro"] = round(limit * MICRO)
    if "period" in body:
        if body["period"] not in PERIODS:
            raise PortalError(400, "period must be day, month or total.")
        changes["period"] = body["period"]
    if "provider" in body:
        if body["provider"] not in PROVIDERS:
            raise PortalError(400, "provider must be openrouter or gemini.")
        if body["provider"] == "gemini" and not gemini_available():
            raise PortalError(503, "Gemini is not set up on this server.")
        changes["provider"] = body["provider"]
        if changes["provider"] != provider(item) and "model" not in body:
            changes["model"] = ""  # a model id of one provider means nothing to the other: back to that provider's default
    if "model" in body:
        model = str(body["model"] or "").strip()
        chosen = changes.get("provider", provider(item))
        if chosen == "gemini":
            if model and model not in gemini.models():
                raise PortalError(400, f"model must be one of: {', '.join(gemini.models())}.")
        elif model and not MODEL_RE.match(model):
            raise PortalError(400, "model must be an OpenRouter model id such as anthropic/claude-haiku-5.5.")
        changes["model"] = model  # empty means: use the provider's default
    return changes


def set_ai(item, body, now):
    """Teacher: switch AI on/off and set the cap. Safe to repeat: key creation, limit changes and the function update are idempotent.
    Order matters for safety: the key is disabled BEFORE anything else when AI goes off."""
    if not available():
        raise PortalError(503, "The class AI connector is not set up on this server.")
    current = settings(item)
    changes = _parse(body, item)
    if not changes:  # an empty request refreshes the usage numbers
        return refresh_usage(item, now, force=True)
    want_on = changes.get("enabled", bool(current.get("enabled")))
    limit_micro = changes.get("limit_micro", current.get("limit_micro", round(default_limit_usd() * MICRO)))
    period = changes.get("period", current.get("period", "month"))
    chosen = changes.get("provider", provider(item))
    # the OpenRouter key works only while AI is on, the token is active AND this student uses OpenRouter
    desired_disabled = not (want_on and chosen == "openrouter" and portal.status_of(item, now) == "active")
    try:
        key_hash = current.get("hash")
        if want_on and chosen == "openrouter" and not key_hash:
            made = openrouter.create_key(f"student:{item['slug']}", limit_micro / MICRO, period)
            # Keep the new key at once, so a failure further down never leaves an orphan and a retry reuses it.
            item = _save(item, hash=made["hash"], key=made["key"], key_disabled=False, limit_micro=limit_micro, period=period)
            current, key_hash = settings(item), made["hash"]
        if key_hash:
            fields = {}
            if "limit_micro" in changes or "period" in changes:
                fields.update(limit_usd=limit_micro / MICRO, period=period)
            if desired_disabled != bool(current.get("key_disabled", False)):
                fields["disabled"] = desired_disabled
            if fields:
                openrouter.update_key(key_hash, **fields)
    except openrouter.OpenRouterError as e:
        raise PortalError(502, f"OpenRouter refused the change: {e}")
    saved = {**changes, "limit_micro": limit_micro, "period": period}
    if want_on:
        saved["provider"] = chosen  # remember the choice, so a later change of the server default never moves a student
    if want_on and chosen == "gemini" and not current.get("gkey"):
        saved["gkey"] = f"ga_{item['slug']}.{secrets.token_urlsafe(24)}"  # made once, kept while the student exists
    if key_hash:
        saved["key_disabled"] = desired_disabled
    item = _save(item, **saved)
    apply_to_function(item)
    return refresh_usage(item, now, force=True, quiet=True)


def apply_to_function(item):
    """Make the student's function match the connector state (env variables and timeout). A no-op until a function exists."""
    name = backends.settings(item).get("function_name")
    if not name:
        return item
    lam = backends.client("lambda")
    try:
        variables = backends._current_env(name)
        for var in AI_ENV_NAMES:
            variables.pop(var, None)
        variables.update(env_for(item))
        lam.update_function_configuration(FunctionName=name, Environment={"Variables": variables}, Timeout=timeout_for(item))
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            raise PortalError(409, "The student's function is updating. Press the switch again in a few seconds; nothing is lost.")
        raise PortalError(502, f"Could not update the student's function: {e.response['Error'].get('Message', 'AWS error')}")
    return backends._save(item, env_names=sorted(variables))


# ---------- usage ----------
def refresh_usage(item, now, force=False, quiet=False):
    """Read the student's usage from OpenRouter (cached for REFRESH_SECONDS). Never raises on a provider failure when quiet."""
    s = settings(item)
    if available() and provider(item) == "gemini":  # our own counter: no provider call, always current
        used = gemini_used_micro(item, now)
        return item if used == int(s.get("used_micro", -1)) else _save(item, used_micro=used, checked_at=now)
    if not (available() and s.get("hash")):
        return item
    if not force and now - int(s.get("checked_at") or 0) < REFRESH_SECONDS:
        return item
    try:
        data = openrouter.get_key(s["hash"])
    except openrouter.OpenRouterError as e:
        if quiet:
            return item
        raise PortalError(502, f"Could not read the usage: {e}")
    limit, remaining = data.get("limit"), data.get("limit_remaining")
    used = (limit - remaining) if (limit is not None and remaining is not None) else (data.get("usage") or 0)
    return _save(item, used_micro=max(round(float(used) * MICRO), 0), checked_at=now)


# ---------- lifecycle ----------
def sync_lifecycle(item, now):
    """The key works only while AI is on AND the token is active. Called after revoke/extend/approve/offline and by the daily sweeper.
    Best effort: a provider outage must never block the teacher's own action, so failures are logged and retried by the sweeper."""
    s = settings(item)
    if not (available() and s.get("hash")):
        return item
    desired_disabled = not (s.get("enabled") and provider(item) == "openrouter" and portal.status_of(item, now) == "active")
    if desired_disabled == bool(s.get("key_disabled", False)):
        return item
    try:
        openrouter.update_key(s["hash"], disabled=desired_disabled)
    except openrouter.OpenRouterError as e:
        print(f"could not {'disable' if desired_disabled else 'enable'} the AI key of {item['slug']}: {e}")
        return item
    return _save(item, key_disabled=desired_disabled)


def sweep(now=None):
    now = int(now if now is not None else time.time())
    changed, failed = [], []
    if not available():
        return {"changed": changed, "failed": failed}
    for item in db().list_prefix("tok#"):
        if not settings(item).get("hash"):
            continue
        before = bool(settings(item).get("key_disabled", False))
        try:
            after = bool(settings(sync_lifecycle(item, now)).get("key_disabled", False))
        except Exception as e:  # one bad record must not stop the rest
            failed.append(f"{item['slug']}: {e}")
            continue
        if after != before:
            changed.append(item["slug"])
    return {"changed": changed, "failed": failed}
