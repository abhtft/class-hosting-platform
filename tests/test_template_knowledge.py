"""Guards the class knowledge connector in template.yaml: off by default and only with backends, its API and two functions exist only when it is on, they can
write nowhere but knowledge/ in the bucket and only the embedding cache and counters in the tokens table, they cannot scan, the book reader cannot be
retried automatically (it would spend twice), only the portal and the API can start it, and nobody else gains anything."""
import os
import re

root = os.path.join(os.path.dirname(__file__), "..")
template = open(os.path.join(root, "template.yaml"), encoding="utf8").read().replace("\r\n", "\n")


def block(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n|\n  # )", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


# ---- off by default, only with backends, and everything about it exists only when it is on ----
params = template[template.index("\nParameters:"):template.index("\nConditions:")]
assert re.search(r"  KnowledgeEnabled:\n    Type: String\n    Default: 'false'\n    AllowedValues: \['false', 'true'\]", params), "knowledge is off unless asked for"
assert re.search(r"  KnowledgeMonthlyCapUsd:\n    Type: String\n    Default: '3'", params), "the class-wide monthly ceiling defaults to 3 dollars"
assert re.search(r"  KnowledgeEmbeddingDims:\n    Type: Number\n    Default: 768\n    MinValue: 64\n    MaxValue: 3072", params)
assert "UseKnowledge: !And [!Condition UseBackend, !Equals [!Ref KnowledgeEnabled, 'true']]" in template
for name in ("KnowledgeApi", "KnowledgeFunction", "KnowledgeWorkerFunction"):
    assert "Condition: UseKnowledge" in block(name), f"{name} exists only when knowledge is on"
assert "Condition: UseAlertsAndKnowledge" in block("KnowledgeErrorsAlarm")
assert "KnowledgeApiUrl:\n    Condition: UseKnowledge" in template, "its output exists only when it is on"
globals_ = template[template.index("\nGlobals:"):template.index("\nResources:")]
assert "KNOWLEDGE_ENABLED: !Ref KnowledgeEnabled" in globals_ and "KNOWLEDGE_MONTHLY_CAP_USD: !Ref KnowledgeMonthlyCapUsd" in globals_
assert "KNOWLEDGE_URL" not in globals_ and "KNOWLEDGE_WORKER_FUNCTION" not in globals_, "the addresses go to the functions that need them only: in Globals they would make the resources depend on themselves"

# ---- the API function: answers searches and hands out upload forms ----
api = block("KnowledgeFunction")
assert "Handler: knowledge_api.handler" in api and "Timeout: 29" in api and "MemorySize: 1024" in api
assert "KNOWLEDGE_WORKER_FUNCTION: !Ref KnowledgeWorkerFunction" in api
assert re.search(r"Path: /\{proxy\+\}\n\s+Method: ANY", api) and "ApiId: !Ref KnowledgeApi" in api
assert "ThrottlingRateLimit: 20" in block("KnowledgeApi"), "its own throttle"

LEADING_UPDATE = "['kdoc#*', 'kpass#*', 'kbytes#*', 'ksearch#*', 'ktok#*', 'kspend#*', 'kwork#*']"
LEADING_API = LEADING_UPDATE[:-1] + ", 'trd#*']"  # the API function also counts the daily traces of a student (traces.py)
for label, fn in (("the API function", api), ("the book reader", block("KnowledgeWorkerFunction"))):
    LEADING_UPDATE_HERE = LEADING_API if fn is api else LEADING_UPDATE
    assert re.search(r"Action: dynamodb:GetItem\n\s+Resource: !GetAtt TokensTable\.Arn", fn), f"{label}: single-item reads in the tokens table"
    assert re.search(r"Action: dynamodb:UpdateItem\n\s+Resource: !GetAtt TokensTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: " + re.escape(LEADING_UPDATE_HERE), fn), \
        f"{label}: it may update counters only"
    assert re.search(r"Action: dynamodb:PutItem\n\s+Resource: !GetAtt TokensTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['emb#\*', 'kwork#\*'\]", fn), \
        f"{label}: it may write whole items only for the embedding cache and the reader slot"
    for forbidden in ("dynamodb:Scan", "dynamodb:Query", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem", "dynamodb:*", "kms:", "iam:", "cloudfront", "ssm:", "logs:", "StudentDataTable"):
        assert forbidden not in fn, f"{label} must not have {forbidden}"
    objects = re.findall(r"\$\{SitesBucket\.Arn\}/[^']*", fn)
    assert objects == ["${SitesBucket.Arn}/knowledge/*"], f"{label}: objects under knowledge/ only, found {objects}"
    assert re.search(r"Action: s3:ListBucket\n\s+Resource: !GetAtt SitesBucket\.Arn\n\s+Condition:\n\s+StringLike:\n\s+s3:prefix: \['knowledge/\*'\]", fn), f"{label}: listing is limited to knowledge/"
    code = re.sub(r"#[^\n]*", "", fn)  # comments may say what is NOT granted
    assert "portal" not in code and "sites/" not in code and "userdata" not in code, f"{label} never names the kit, the sites or the students' files"
assert re.search(r"Action: lambda:InvokeFunction\n\s+Resource: !GetAtt KnowledgeWorkerFunction\.Arn", api) and api.count("lambda:") == 1, "the API may start the book reader and nothing else"

# ---- the book reader: long, big, never retried by the platform, not reachable from the internet ----
worker = block("KnowledgeWorkerFunction")
assert "Handler: knowledge_worker.handler" in worker and "Timeout: 900" in worker and "MemorySize: 2048" in worker
assert re.search(r"EventInvokeConfig:\n\s+MaximumRetryAttempts: 0", worker), "a failed book is reported on the book; an automatic second try would spend twice"
assert "Events:" not in worker and "HttpApi" not in worker, "nothing on the internet reaches it directly"
assert "lambda:" not in worker, "it cannot start anything"

# ---- the portal gains only: knowledge/ objects, and starting the book reader for a library book ----
deploy = block("DeployFunction")
assert "KNOWLEDGE_URL: !If [UseKnowledge" in deploy and "KNOWLEDGE_WORKER_FUNCTION: !If [UseKnowledge, !Ref KnowledgeWorkerFunction, '']" in deploy
assert re.search(r"- UseKnowledge\n\s+- Effect: Allow\n\s+Action: \[s3:GetObject, s3:PutObject, s3:DeleteObject\]\n\s+Resource: !Sub '\$\{SitesBucket\.Arn\}/knowledge/\*'", deploy)

# ---- nobody else gains access ----
for name in ("SweeperFunction", "GatewayFunction", "StorageFunction", "AiProxyFunction", "StudentSandboxRole", "StudentSandboxBoundary", "BackendApi"):
    assert "Knowledge" not in block(name) and "knowledge/" not in block(name), f"{name} must not touch the knowledge connector"
assert "Principal: '*'" not in template and "PublicAccessBlockConfiguration" in block("SitesBucket"), "the bucket stays private"
assert "Knowledge" not in block("Distribution") and "knowledge" not in block("PortalDistribution"), "knowledge/ is never served by CloudFront"

print("test_template_knowledge.py: the knowledge connector is off by default, tightly scoped, and reaches nothing else")
