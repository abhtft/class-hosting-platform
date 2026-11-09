"""Trace format and privacy rules (pure functions, no AWS). See docs/TRACE_SCHEMA.md.

A trace is one answer. It has a summary and a list of spans (steps). A span is a small dict shaped like an OpenTelemetry span:
  {"name", "kind", "source", "ts", "dur_ms", "status", "attrs": {...}}
Text-bearing attribute keys (TEXT_KEYS) are removed at level "metadata", masked at "masked", kept (but capped) at "full".
Nothing here ever raises on bad input: tracing must never break an answer.
"""
import json
import re
import secrets
import platform_settings

LEVELS = ("metadata", "masked", "full")
VIEWS = ("off", "metadata", "stored")  # what a student may see of their own app's traces
RETENTION_CHOICES = (30, 60, 90)
DEFAULT_RETENTION = platform_settings.value("traces.retentionDays")
DEFAULT_LEVEL = "metadata"
DEFAULT_VIEW = "metadata"
DEFAULT_DAILY_LIMIT = platform_settings.default("traces.dailyLimit")
MAX_DAILY_LIMIT = platform_settings.maximum("traces.dailyLimit")
KINDS = ("retriever", "llm", "guard", "tool", "agent", "chain", "other")
STATUSES = ("ok", "error", "skipped")
TEXT_KEYS = ("query", "question", "prompt", "messages", "answer", "output", "input", "passages", "text", "reason", "arguments", "result")
MAX_TEXT_CHARS = platform_settings.value("traces.maxTextChars")        # one text value
MAX_ITEM_BYTES = platform_settings.value("traces.maxItemBytes")      # a span item stays far below DynamoDB's 400 KB
MAX_ATTR_KEYS = platform_settings.value("traces.maxAttrKeys")
TRACE_ID_RE = re.compile(r"^tr_[0-9a-f]{16}$")

_MASKS = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"\b(?:ks|ga|cs|mds|sk|pk|rk)[_-][A-Za-z0-9_.-]{16,}"), "[key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[key]"),
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "[pan]"),                 # Indian PAN
    (re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)"), "[id]"),     # Aadhaar-like, 12 digits
    (re.compile(r"(?<!\d)(?:\+?91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?!\d)"), "[phone]"),
    (re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), "[bank]"),                  # IFSC code
    (re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b"), "[gstin]"),
    (re.compile(r"\b[A-Z][0-9]{7}\b"), "[passport]"),
    (re.compile(r"\b[\w.-]{2,}@[a-z]{2,}\b"), "[upi]"),                  # UPI id such as name@bank (after the email rule)
    (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "[ip]"),
    (re.compile(r"(?<!\d)(?:\d[ -]?){9,19}(?!\d)"), "[number]"),          # card, account and other long numbers
)
_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")  # Devanagari digits read as ordinary digits


def new_trace_id():
    return "tr_" + secrets.token_hex(8)


def valid_trace_id(value):
    return isinstance(value, str) and bool(TRACE_ID_RE.match(value))


def trace_id_or_new(value):
    """A client may bring its own id (so app-side and platform-side spans join), but only in our exact shape."""
    return value if valid_trace_id(value) else new_trace_id()


def mask_text(text):
    out = str(text).translate(_DIGITS)
    for pattern, label in _MASKS:
        out = pattern.sub(label, out)
    return out


def _cap(value):
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_TEXT_CHARS else s[:MAX_TEXT_CHARS] + "…[cut]"


def _apply(value, level):
    if isinstance(value, dict):
        return {k: _apply(v, level) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply(v, level) for v in value]
    if isinstance(value, str):
        return mask_text(_cap(value)) if level == "masked" else _cap(value)
    return value


# Strings that may stay at the "metadata" level: names and labels the platform or the books make, never what a visitor typed. Any other string key is
# reduced to its length, so even an app that notes something under a key of its own cannot leave text in a numbers-only trace.
SAFE_STRING_KEYS = frozenset(("name", "model", "provider", "finish_reason", "tool", "status", "kind", "source", "doc", "collection", "section", "title", "level", "error", "id",
                              "stop_reason", "reason_code"))
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_./:-]{1,60}$")  # a dictionary key that may stay (shelf names, ids), unlike a key made of text such as an email address
MAX_SAFE_STRING = platform_settings.value("traces.maxSafeString")


def apply_level(attrs, level):
    """The attributes as they may be stored at this privacy level. Unknown levels behave as "metadata" (the safe one).
    metadata: no text anywhere (text keys become a length or count; other strings stay only under SAFE_STRING_KEYS); masked: every string is masked; full: as given (capped)."""
    if not isinstance(attrs, dict):
        return {}
    level = level if level in LEVELS else "metadata"
    out = {}
    for key in list(attrs)[:MAX_ATTR_KEYS]:
        if not isinstance(key, str) or not SAFE_KEY_RE.match(key):
            continue
        value = attrs[key]
        if level == "metadata":
            if isinstance(value, str):
                if key in SAFE_STRING_KEYS:
                    out[key] = value[:MAX_SAFE_STRING]
                else:
                    out[f"{key}_chars"] = len(value)
            elif isinstance(value, list) and any(isinstance(v, str) for v in value):
                out[f"{key}_count"] = len(value)
                rest = [_numbers_only(v, key) for v in value[:50] if not isinstance(v, str)]
                if rest:
                    out[key] = rest
            elif isinstance(value, (bool, int, float)) or value is None:
                out[key] = value
            elif isinstance(value, (dict, list)):
                out[key] = _numbers_only(value, key)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = _apply(value, level) if isinstance(value, str) else value
        elif isinstance(value, (dict, list)):
            out[key] = _apply(value, level)
    return out


def _numbers_only(value, key=""):
    """Metadata keeps shape, numbers and short labels from SAFE_STRING_KEYS (scores, counts, ids, shelf names), never other strings."""
    if isinstance(value, dict):
        return {k: _numbers_only(v, k) for k, v in value.items() if isinstance(k, str) and SAFE_KEY_RE.match(k) and (not isinstance(v, str) or k in SAFE_STRING_KEYS)}
    if isinstance(value, list):
        return [_numbers_only(v, key) for v in value[:50] if not isinstance(v, str)]
    return value


def make_span(name, kind="other", source="platform", ts_ms=0, dur_ms=0, status="ok", attrs=None, level=DEFAULT_LEVEL):
    """One span, already reduced to the privacy level. Always returns a valid span."""
    span = {
        "name": str(name or "step")[:60],
        "kind": kind if kind in KINDS else "other",
        "source": source if source in ("platform", "app") else "platform",
        "ts": int(ts_ms or 0),
        "dur_ms": max(int(dur_ms or 0), 0),
        "status": status if status in STATUSES else "ok",
        "attrs": apply_level(attrs or {}, level),
    }
    while len(json.dumps(span, ensure_ascii=False, default=str).encode()) > MAX_ITEM_BYTES and span["attrs"]:
        span["attrs"].popitem()
    return span


def span_sort_key(ts_ms, source, salt=None):
    """Sort key of a span item: time first, so a query returns the steps in order."""
    return f"{int(ts_ms):013d}#{source}#{salt or secrets.token_hex(2)}"


def normalise_settings(raw):
    """A student's trace settings with every value in range (used when reading, so old or odd data is harmless)."""
    raw = raw if isinstance(raw, dict) else {}
    level = raw.get("level") if raw.get("level") in LEVELS else DEFAULT_LEVEL
    days = raw.get("retention_days") if raw.get("retention_days") in RETENTION_CHOICES else DEFAULT_RETENTION
    view = raw.get("student_view") if raw.get("student_view") in VIEWS else DEFAULT_VIEW
    limit = raw.get("daily_limit")
    limit = limit if isinstance(limit, int) and not isinstance(limit, bool) and 0 <= limit <= MAX_DAILY_LIMIT else DEFAULT_DAILY_LIMIT
    return {"enabled": raw.get("enabled") is not False, "level": level, "retention_days": days, "student_view": view, "daily_limit": limit}


def validate_settings(body):
    """Teacher input: returns the changes to apply, or raises ValueError with a plain message."""
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object.")
    out = {}
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ValueError("enabled must be true or false.")
        out["enabled"] = body["enabled"]
    if "level" in body:
        if body["level"] not in LEVELS:
            raise ValueError(f"level must be one of: {', '.join(LEVELS)}.")
        out["level"] = body["level"]
    if "retention_days" in body:
        if body["retention_days"] not in RETENTION_CHOICES:
            raise ValueError(f"retention_days must be one of: {', '.join(map(str, RETENTION_CHOICES))}.")
        out["retention_days"] = body["retention_days"]
    if "student_view" in body:
        if body["student_view"] not in VIEWS:
            raise ValueError(f"student_view must be one of: {', '.join(VIEWS)}.")
        out["student_view"] = body["student_view"]
    if "daily_limit" in body:
        v = body["daily_limit"]
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_DAILY_LIMIT:
            raise ValueError(f"daily_limit must be a whole number from 0 to {MAX_DAILY_LIMIT}.")
        out["daily_limit"] = v
    if not out:
        raise ValueError("Nothing to change.")
    return out


def student_can_see(settings_view, stored_level):
    """What a student gets for their own traces: "off" nothing, "metadata" the numbers only, "stored" what was stored."""
    if settings_view == "off":
        return None
    if settings_view == "stored" and stored_level in LEVELS:
        return stored_level
    return "metadata"


def strip_for_student(span, mode):
    """Remove text from a span when the student view is "metadata" (even if more was stored)."""
    if mode == "stored":
        return span
    return {**span, "attrs": apply_level(span.get("attrs") or {}, "metadata")}
