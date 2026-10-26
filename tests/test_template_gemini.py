"""Guards the Gemini proxy in template.yaml: off by default, only the proxy function can read the secret for it (never a student function),
it gets no more table rights than it needs, and it has its own throttled API."""
import os
import re

root = os.path.join(os.path.dirname(__file__), "..")
template = open(os.path.join(root, "template.yaml"), encoding="utf8").read().replace("\r\n", "\n")


def block(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n)", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


params = template[template.index("\nParameters:"):template.index("\nConditions:")]
assert re.search(r"  AiGeminiEnabled:\n    Type: String\n    Default: 'false'\n    AllowedValues: \['false', 'true'\]", params), "Gemini is off unless asked for"
assert "UseGemini: !And [!Condition UseAi, !Condition UseBackend, !Equals [!Ref AiGeminiEnabled, 'true']]" in template
assert "AI_GEMINI_ENABLED: !Ref AiGeminiEnabled" in template[template.index("\nGlobals:"):template.index("\nResources:")]

for name, kind in (("AiProxyApi", "AWS::Serverless::HttpApi"), ("AiProxyFunction", "AWS::Serverless::Function")):
    part = block(name)
    assert f"Type: {kind}" in part and "Condition: UseGemini" in part, f"{name} exists only when Gemini is on"
api, fn = block("AiProxyApi"), block("AiProxyFunction")
assert "ThrottlingRateLimit" in api, "the proxy API has its own throttle"
assert "Handler: ai_proxy.handler" in fn and "Timeout: 29" in fn

# the proxy may read exactly one secret and the tokens table (read + counter update), nothing else
assert fn.count("secretsmanager:GetSecretValue") == 1 and "${AiSecretName}-??????" in fn
assert "Action: [dynamodb:GetItem, dynamodb:UpdateItem]" in fn and "dynamodb:Scan" not in fn and "dynamodb:DeleteItem" not in fn
assert "s3:" not in fn and "lambda:" not in fn and "iam:" not in fn, "the proxy needs no storage, function or role rights"

# no student-facing role/function gets the secret through some other statement
for name in ("StudentSandboxRole", "GatewayFunction", "StorageFunction"):
    assert "secretsmanager" not in block(name), f"{name} must not read secrets"
assert "AI_PROXY_URL: !If [UseGemini," in block("DeployFunction"), "the portal learns the proxy address only when Gemini is on"
assert "AiProxyUrl:" in template[template.index("\nOutputs:"):]
print("test_template_gemini.py: all passed")
