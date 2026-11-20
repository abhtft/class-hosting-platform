"""Amazon Bedrock (lambda/bedrock.py): an OpenAI-shaped request becomes a Converse call (system messages apart, roles alternate, the US inference profile), the answer comes back
OpenAI-shaped with usage, the price is the AWS list price of the profile (and may be overridden), and every failure is a gemini.GeminiError with a status and no AWS detail."""
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import bedrock  # noqa: E402
import gemini  # noqa: E402

assert bedrock.DEFAULT_MODEL == "claude-haiku-5-5" and bedrock.models() == ["claude-haiku-5-5", "claude-sonnet-5-5"]
assert bedrock.profile_of("claude-haiku-5-5") == "us.anthropic.claude-haiku-5-5" and bedrock.profile_of("claude-sonnet-5-5") == "us.anthropic.claude-sonnet-5-5"

# the request
request = bedrock.converse_request({"model": "claude-haiku-5-5", "max_tokens": 300, "temperature": 0.2, "messages": [
    {"role": "system", "content": "rule one"}, {"role": "developer", "content": "rule two"},
    {"role": "assistant", "content": "Welcome"}, {"role": "user", "content": "hi"}, {"role": "user", "content": [{"type": "text", "text": "and more"}]},
    {"role": "assistant", "content": ""}, {"role": "tool", "content": "odd"}]})
assert request["modelId"] == "us.anthropic.claude-haiku-5-5" and request["system"] == [{"text": "rule one"}, {"text": "rule two"}]
assert request["inferenceConfig"] == {"maxTokens": 300, "temperature": 0.2}
assert [(m["role"], m["content"][0]["text"]) for m in request["messages"]] == [("user", "."), ("assistant", "Welcome"), ("user", "hi\n\nand more\n\nodd")], request["messages"]
bare = bedrock.converse_request({"messages": [{"role": "user", "content": "hi"}]})
assert "system" not in bare and bare["inferenceConfig"] == {"maxTokens": 1024} and bare["modelId"] == "us.anthropic.claude-haiku-5-5", "a model left out is the default; no system, no temperature"
assert bedrock.converse_request({"messages": [{"role": "user", "content": "x"}], "temperature": 9})["inferenceConfig"]["temperature"] == 1.0
print("  ok  a request becomes a Converse call: system apart, roles alternate and start with the customer, the profile of the model")

# the answer
seen = []


class Client:
    def __init__(self, response=None, code=None):
        self.response, self.code = response, code

    def converse(self, **request):
        seen.append(request)
        if self.code:
            error = Exception("secret arn:aws:iam::123:role/x")
            error.response = {"Error": {"Code": self.code, "Message": "arn:aws:iam::123:role/x"}}
            raise error
        return self.response


ok = {"output": {"message": {"content": [{"text": "Hello "}, {"text": "there"}]}}, "usage": {"inputTokens": 1000, "outputTokens": 200}, "stopReason": "max_tokens"}
bedrock.client_factory = lambda: Client(ok)
trace = []
data = bedrock.chat({"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "hi"}]}, trace=trace)
assert data["choices"][0]["message"]["content"] == "Hello there" and data["choices"][0]["finish_reason"] == "length" and trace == ["BEDROCK"]
assert data["usage"] == {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}
assert bedrock.cost_micro("claude-haiku-5-5", data["usage"]) == 220 and bedrock.cost_micro("claude-sonnet-5-5", data["usage"]) == 4400 and bedrock.cost_micro("claude-haiku-5-5", None) == 0
os.environ["AI_BEDROCK_PRICES"] = '{"claude-haiku-5-5": [1, 2], "nope": [1, 1]}'
assert bedrock.cost_micro("claude-haiku-5-5", data["usage"]) == 1400 and bedrock.models() == ["claude-haiku-5-5", "claude-sonnet-5-5"]
os.environ["AI_BEDROCK_PRICES"] = "not json"
assert bedrock.cost_micro("claude-haiku-5-5", data["usage"]) == 220, "a broken override leaves the built-in prices"
del os.environ["AI_BEDROCK_PRICES"]
print("  ok  the answer comes back OpenAI-shaped with usage; cost is input and output tokens at the list price; an override works and a broken one is ignored")

# the failures
for code, status in (("ThrottlingException", 429), ("ServiceUnavailableException", 503), ("ModelTimeoutException", 503), ("AccessDeniedException", 403), ("ValidationException", 400), ("SomethingNew", 502)):
    bedrock.client_factory = lambda code=code: Client(code=code)
    try:
        bedrock.chat({"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "hi"}]})
        raise AssertionError(code)
    except gemini.GeminiError as error:
        assert error.status == status and "arn:aws" not in str(error) and "123" not in str(error), (code, error.status, str(error))
        assert status in gemini.FAILOVER_STATUSES or status in (400,), "a refused provider is one the callers pass on"
try:
    bedrock.chat({"model": "gpt-9", "messages": []})
    raise AssertionError("an unknown model must be refused")
except gemini.GeminiError as error:
    assert error.status == 400

class NoTemperature:
    def __init__(self):
        self.requests = []

    def converse(self, **request):
        self.requests.append(dict(request["inferenceConfig"]))
        if "temperature" in request["inferenceConfig"]:
            error = Exception("x")
            error.response = {"Error": {"Code": "ValidationException", "Message": "temperature is deprecated for this model"}}
            raise error
        return ok


fake = NoTemperature()
bedrock.client_factory = lambda: fake
assert bedrock.chat({"model": "claude-haiku-5-5", "temperature": 0.2, "messages": [{"role": "user", "content": "hi"}]})["choices"][0]["message"]["content"] == "Hello there"
assert fake.requests == [{"maxTokens": 1024, "temperature": 0.2}, {"maxTokens": 1024}], "a model that takes no temperature is asked again without it"
bedrock.client_factory = None
print("  ok  every failure is a GeminiError with a status (429, 503, 403, 400, 502) and never any AWS detail")

print("test_bedrock.py: the Amazon Bedrock provider")
