"""Amazon Bedrock for the class AI and the Chatwoot adapter (boto3 only, no key: the function's IAM role is the credential).

It speaks the same language as gemini.py: `chat(body)` takes an OpenAI-shaped request (model, messages, max_tokens, temperature) and returns an OpenAI-shaped answer
(`choices[0].message.content`, `usage`), and a failure is a `gemini.GeminiError` with an HTTP-like status (429 too many requests, 503 busy, 403 not allowed), so the callers'
"refused, try the next" logic is the same for every provider.

Models are offered under a short name (claude-haiku-5-5); the call goes to the geographic inference profile of the platform's region (us.anthropic.claude-haiku-5-5),
which keeps the traffic inside the US regions. Money: USD per million tokens, input and output, as AWS lists them for that profile in us-east-1; override or add models
with the AI_BEDROCK_PRICES environment variable, a JSON object such as {"claude-haiku-5-5": [0.11, 0.55]}.
"""
import json
import math
import os

import gemini

DEFAULT_MODEL = "claude-haiku-5-5"
# short name: (profile id, USD per million input tokens, USD per million output tokens)
MODELS = {
    "claude-haiku-5-5": ("us.anthropic.claude-haiku-5-5", 0.11, 0.55),
    "claude-sonnet-5-5": ("us.anthropic.claude-sonnet-5-5", 2.20, 11.00),
}
ERROR_STATUS = {"ThrottlingException": 429, "ServiceQuotaExceededException": 429, "TooManyRequestsException": 429, "ModelTimeoutException": 503,
                "ServiceUnavailableException": 503, "InternalServerException": 503, "ModelNotReadyException": 503, "AccessDeniedException": 403,
                "ValidationException": 400, "ResourceNotFoundException": 404}

client_factory = None  # tests: a function returning a fake bedrock-runtime client


def table():
    out = dict(MODELS)
    try:
        for name, prices in json.loads(os.environ.get("AI_BEDROCK_PRICES") or "{}").items():
            if name in out and isinstance(prices, list) and len(prices) == 2 and all(isinstance(p, (int, float)) and p >= 0 for p in prices):
                out[name] = (out[name][0], float(prices[0]), float(prices[1]))
    except (ValueError, TypeError, AttributeError):
        pass  # a broken override must not take the proxy down: the built-in prices stay
    return out


def models():
    return sorted(table())


def profile_of(model):
    return table()[model][0]


def cost_micro(model, usage):
    """Micro-dollars for one answer, rounded up (never under-count). Unknown usage counts as zero tokens."""
    _, price_in, price_out = table()[model]
    usage = usage if isinstance(usage, dict) else {}
    return math.ceil(int(usage.get("prompt_tokens") or 0) * price_in + int(usage.get("completion_tokens") or 0) * price_out)


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI content parts
        return "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def converse_request(body):
    """The Converse call for an OpenAI-shaped body: system messages go to `system`, the others alternate user / assistant (neighbours of the same role are joined)."""
    system, turns = [], []
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        text = _text(message.get("content")).strip()
        if not text:
            continue
        role = message.get("role")
        if role in ("system", "developer"):
            system.append({"text": text})
            continue
        role = "assistant" if role == "assistant" else "user"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"][0]["text"] += "\n\n" + text
        else:
            turns.append({"role": role, "content": [{"text": text}]})
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": [{"text": "."}]})
    config = {"maxTokens": int(body.get("max_tokens") or 1024)}
    if isinstance(body.get("temperature"), (int, float)) and not isinstance(body.get("temperature"), bool):
        config["temperature"] = min(max(float(body["temperature"]), 0.0), 1.0)
    request = {"modelId": profile_of(body.get("model") or DEFAULT_MODEL), "messages": turns, "inferenceConfig": config}
    if system:
        request["system"] = system
    return request


def _client(timeout):
    if client_factory is not None:
        return client_factory()
    import boto3
    from botocore.config import Config
    return boto3.client("bedrock-runtime", config=Config(read_timeout=timeout, connect_timeout=5, retries={"max_attempts": 1}))


def chat(body, timeout=25, trace=None):
    """One non-streaming completion. Returns the OpenAI-shaped answer; raises gemini.GeminiError (never with any AWS detail)."""
    model = body.get("model") or DEFAULT_MODEL
    if model not in table():
        raise gemini.GeminiError(400, f"model must be one of: {', '.join(models())}.")
    if trace is not None:
        trace.append("BEDROCK")
    request = converse_request({**body, "model": model})
    try:
        try:
            response = _client(timeout).converse(**request)
        except Exception as error:
            if "temperature" not in str((getattr(error, "response", None) or {}).get("Error", {}).get("Message", "")).lower() or "temperature" not in request["inferenceConfig"]:
                raise
            del request["inferenceConfig"]["temperature"]  # a model that takes no temperature: ask again without it
            response = _client(timeout).converse(**request)
    except Exception as error:  # botocore ClientError and friends: a status, never the AWS message in the answer (a short line goes to the platform's own log)
        code = ((getattr(error, "response", None) or {}).get("Error") or {}).get("Code") or type(error).__name__
        print(f"bedrock: {code} {str((getattr(error, 'response', None) or {}).get('Error', {}).get('Message', ''))[:200]}")
        status = ERROR_STATUS.get(code, 502)
        raise gemini.GeminiError(status, "The model is not allowed for this platform yet (Bedrock access)." if status == 403 else f"Bedrock answered {code}.")
    parts = (((response.get("output") or {}).get("message") or {}).get("content")) or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    usage = response.get("usage") or {}
    prompt, completion = int(usage.get("inputTokens") or 0), int(usage.get("outputTokens") or 0)
    reason = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}.get(response.get("stopReason"), "stop")
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": reason}], "model": model,
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}}
