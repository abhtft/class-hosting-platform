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

BASE_URL = os.environ.get("AI_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
KEY_CACHE_SECONDS = 300
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


def google_key():
    if _secret["value"] is None or time.time() - _secret["at"] > KEY_CACHE_SECONDS:
        import boto3
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["AI_SECRET_NAME"])["SecretString"]
        _secret["value"], _secret["at"] = json.loads(raw)["GOOGLE_API_KEY"], time.time()
    return _secret["value"]


def chat(body, timeout=25):
    """One non-streaming chat completion. Returns the parsed OpenAI-style answer; raises GeminiError (the message never holds the key)."""
    key = google_key()
    url = BASE_URL + "/chat/completions"
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "student-deploy/1"}
    if transport is not None:
        status, data = transport(url, headers, body)
    else:
        request = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status, text = response.status, response.read().decode().strip()
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode().strip()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise GeminiError(502, f"Could not reach Gemini ({type(e).__name__}).")
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
    if status >= 400:
        err = data[0] if isinstance(data, list) and data else data
        message = ((err or {}).get("error") or {}).get("message") if isinstance(err, dict) else None
        raise GeminiError(status, (message or f"Gemini answered HTTP {status}.").replace(key, "***"))
    return data
