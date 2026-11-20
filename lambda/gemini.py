"""Google Gemini for the class AI connector (standard library only).

Google has no per-student capped keys (OpenRouter has), so students never receive the platform's Google key. Instead the platform runs its
own small OpenAI-compatible proxy (ai_proxy.py): the student's function gets a private key `ga_<slug>.<secret>` and the proxy's address as
OPENAI_BASE_URL; the proxy checks the key, the teacher's switch and the cap, forwards the call with the real key (Secrets Manager, same
secret as the OpenRouter management key, field GOOGLE_API_KEY) and adds the cost of the answer to the student's counter.

Money: one micro-dollar per token equals one US dollar per million tokens, so cost_micro = prompt * price_in + output * price_out.
Gemini counts "thinking" tokens as output; they are in total_tokens but not in completion_tokens, so output = total - prompt.
"""
import json
import math
import os
import time
import urllib.error
import urllib.request
import platform_settings

BASE_URL = os.environ.get("AI_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
KEY_CACHE_SECONDS = platform_settings.value("ai.keyCacheSeconds")
OPENROUTER_URL = os.environ.get("AI_BASE_URL", "https://openrouter.ai/api/v1")
FAILOVER_STATUSES = (401, 403, 429, 502, 503)  # a refusal of this key, not of the request: try the next one
# USD per million tokens (input, output): Gemini 3.5 Flash-Lite is the one model offered. Override or add models with the AI_GEMINI_PRICES
# environment variable, a JSON object such as {"gemini-3.5-flash-lite": [0.3, 2.5]}.
DEFAULT_PRICES = {"gemini-3.5-flash-lite": (0.30, 2.50)}
DEFAULT_MODEL = "gemini-3.5-flash-lite"

transport = None  # tests: fake(url, headers, body) -> (status, json)
_secret = {"value": None, "at": 0.0}


class GeminiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def prices():
    table = {k: tuple(v) for k, v in DEFAULT_PRICES.items()}
    try:
        table.update({k: (float(v[0]), float(v[1])) for k, v in json.loads(os.environ.get("AI_GEMINI_PRICES") or "{}").items()})
    except (ValueError, TypeError, IndexError, AttributeError):
        pass  # a broken override must not take the proxy down: the built-in prices stay
    return table


def models():
    return sorted(prices())


def cost_micro(model, usage):
    """Micro-dollars for one answer, rounded up (never under-count). Unknown usage counts as zero tokens."""
    price_in, price_out = prices()[model]
    usage = usage if isinstance(usage, dict) else {}
    prompt = int(usage.get("prompt_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)
    output = max(total - prompt, int(usage.get("completion_tokens") or 0))
    return math.ceil(prompt * price_in + output * price_out)


KEYS_MISSING = "The Google Gemini key is not set up yet. The teacher saves it once, in a terminal, with the secrets command."


def _secret_values():
    """The platform's AI secret as a dict (cached): raises GeminiError(503) without detail when it cannot be read."""
    if _secret["value"] is None or time.time() - _secret["at"] > KEY_CACHE_SECONDS:
        import boto3
        try:
            raw = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["AI_SECRET_NAME"])["SecretString"]
            value = json.loads(raw)
            value["GOOGLE_API_KEY"]
        except Exception:  # not set up (or not readable): said plainly, with no detail
            raise GeminiError(503, KEYS_MISSING)
        _secret["value"], _secret["at"] = value, time.time()
    value = _secret["value"]
    return {"GOOGLE_API_KEY": value} if isinstance(value, str) else value  # a plain string is one key (tests)


def google_key():
    """The main Google key (GOOGLE_API_KEY)."""
    return _secret_values()["GOOGLE_API_KEY"]


def google_keys():
    """[(name, key)]: GOOGLE_API_KEY and every other secret field whose name starts GOOGLE_API_KEY_ (alphabetical). Only names are ever shown."""
    values = _secret_values()
    names = ["GOOGLE_API_KEY"] + sorted(n for n in values if n.startswith("GOOGLE_API_KEY_"))
    return [(n, values[n]) for n in names if isinstance(values.get(n), str) and values[n].strip()]


def _post(url, headers, body, timeout):
    """(status, data) of one POST."""
    if transport is not None:
        return transport(url, headers, body)
    request = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, text = response.status, response.read().decode().strip()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode().strip()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GeminiError(502, f"Could not reach the model ({type(e).__name__}).")
    try:
        return status, (json.loads(text) if text else {})
    except ValueError:
        return status, {}


def _fail(status, data, hidden):
    """The GeminiError for an answer of 400 or more; the message never holds a key."""
    err = data[0] if isinstance(data, list) and data else data
    message = ((err or {}).get("error") or {}).get("message") if isinstance(err, dict) else None
    message = message if isinstance(message, str) else None
    for secret in hidden:
        message = message.replace(secret, "***") if message else message
    return GeminiError(status, message or f"The model answered HTTP {status}.")


_turn = {"n": 0}  # round robin: each call starts one key further on (per container)


def chat(body, timeout=25, trace=None):
    """One non-streaming chat completion. Returns the parsed OpenAI-style answer; raises GeminiError (the message never holds a key).
    The Google keys take turns (round robin); when one is refused (too many requests, busy, unreachable, key not accepted) the same call goes to the next, and when every
    Google key was refused it goes to OpenRouter (field OPENROUTER_API_KEY of the same secret, the same model) when that key exists. `trace`, if given, gets the names of
    the keys tried (never values)."""
    keys = google_keys()
    start = _turn["n"] % len(keys) if keys else 0
    _turn["n"] += 1
    order = keys[start:] + keys[:start]
    hidden = [k for _, k in keys]
    last = None
    for name, key in order:
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "student-deploy/1"}
        if trace is not None:
            trace.append(name)
        try:
            status, data = _post(BASE_URL + "/chat/completions", headers, body, timeout)
        except GeminiError as e:
            last = e
            continue
        if status < 400:
            return data
        last = _fail(status, data, hidden)
        if status not in FAILOVER_STATUSES:
            raise last
    fallback = _secret_values().get("OPENROUTER_API_KEY")
    if isinstance(fallback, str) and fallback.strip():
        if trace is not None:
            trace.append("OPENROUTER_API_KEY")
        headers = {"Authorization": "Bearer " + fallback, "Content-Type": "application/json", "User-Agent": "student-deploy/1"}
        status, data = _post(OPENROUTER_URL + "/chat/completions", headers, {**body, "model": "google/" + str(body.get("model"))}, timeout)
        if status < 400:
            return data
        raise _fail(status, data, hidden + [fallback])
    raise last or GeminiError(503, KEYS_MISSING)
