"""Guards the class AI connector in template.yaml: it is off by default, only the portal and sweeper functions can read the
OpenRouter management key (one named secret, nothing broader), a student can never reach it, and no key is committed."""
import glob
import os
import re

root = os.path.join(os.path.dirname(__file__), "..")
template = open(os.path.join(root, "template.yaml"), encoding="utf8").read().replace("\r\n", "\n")


def block(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n)", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


# ---- off by default, switchable, configurable ----
params = template[template.index("\nParameters:"):template.index("\nConditions:")]
assert re.search(r"  AiEnabled:\n    Type: String\n    Default: 'false'\n    AllowedValues: \['false', 'true'\]", params), "AI is off unless asked for"
assert re.search(r"  AiSecretName:\n    Type: String\n    Default: openrouter\n", params)
assert re.search(r"  AiDefaultModel:\n    Type: String\n    Default: anthropic/claude-haiku-5\.5\n", params), "the model is a parameter, not code"
assert re.search(r"  AiDefaultLimitUsd:\n    Type: String\n    Default: '0\.25'\n", params)
assert "UseAi: !Equals [!Ref AiEnabled, 'true']" in template

globals_ = template[template.index("\nGlobals:"):template.index("\nResources:")]
for name in ("AI_ENABLED: !Ref AiEnabled", "AI_SECRET_NAME: !Ref AiSecretName", "AI_DEFAULT_MODEL: !Ref AiDefaultModel",
             "AI_DEFAULT_LIMIT_USD: !Ref AiDefaultLimitUsd", "AI_STUDENT_TIMEOUT: '25'"):
    assert name in globals_, name

# ---- who can read the management key ----
statement = re.compile(
    r"            - !If # class AI connector[^\n]*\n"
    r"              - UseAi\n"
    r"              - Effect: Allow\n"
    r"                Action: secretsmanager:GetSecretValue\n"
    r"                Resource: !Sub 'arn:\$\{AWS::Partition\}:secretsmanager:\$\{AWS::Region\}:\$\{AWS::AccountId\}:secret:\$\{AiSecretName\}-\?\?\?\?\?\?'\n"
    r"              - !Ref AWS::NoValue\n")
for name in ("DeployFunction", "SweeperFunction"):
    assert len(statement.findall(block(name))) == 1, f"{name} reads exactly the one named secret, and only when AI is on"
for name in ("GatewayFunction", "StudentSandboxRole"):
    assert "secretsmanager" not in block(name), f"{name} must never be able to read secrets"
assert template.count("Action: secretsmanager:") == 3, "only the two statements above and the Gemini proxy (test_template_gemini.py) mention Secrets Manager"
assert not re.search(r"secretsmanager:\*|secretsmanager:GetSecretValue\n\s+Resource: '\*'", template)

# ---- long AI answers must fit: student function 25 s, gateway 29 s, HTTP API limit 30 s ----
gateway_timeout = int(re.search(r"\n      Timeout: (\d+)", block("GatewayFunction")).group(1))
assert 28 <= gateway_timeout < 30, gateway_timeout
assert 25 < gateway_timeout and "AI_STUDENT_TIMEOUT: '25'" in globals_

# ---- no OpenRouter key may be committed (the management key lives in Secrets Manager) ----
KEY = re.compile(r"sk-or-v1-(?!x+\b)[A-Za-z0-9]{16,}")
files = [os.path.join(root, f) for f in ("template.yaml", "samconfig.toml", "README.md", "implement.md", "backend_wire.md", "learning.md")]
for pattern in ("docs/*.md", "lambda/*.py", "web/*", "student/*", "custom-domain/*", "tests/*"):
    files += [p for p in glob.glob(os.path.join(root, pattern)) if os.path.isfile(p)]
for path in files:
    text = open(path, encoding="utf8", errors="ignore").read()
    found = KEY.search(text)
    # tests use the obviously fake prefix sk-or-v1-fake-NNN, which has a dash right after "fake" and fails the 16-char run
    assert not found, f"an OpenRouter key appears in {os.path.relpath(path, root)}"

print("test_template_ai.py: all passed")
