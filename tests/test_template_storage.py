"""Guards the class storage connector in template.yaml: off by default, a student function can never touch the data table or the bucket
directly, the storage function is limited to its two tables and the userdata/ folder, the portal's function gains nothing, and the
data table survives a stack deletion."""
import os
import re

root = os.path.join(os.path.dirname(__file__), "..")
template = open(os.path.join(root, "template.yaml"), encoding="utf8").read().replace("\r\n", "\n")


def block(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n|\n  # )", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


# ---- off by default, and only with backends ----
params = template[template.index("\nParameters:"):template.index("\nConditions:")]
assert re.search(r"  StorageEnabled:\n    Type: String\n    Default: 'false'\n    AllowedValues: \['false', 'true'\]", params), "storage is off unless asked for"
assert "UseStorage: !And [!Condition UseBackend, !Equals [!Ref StorageEnabled, 'true']]" in template
for name in ("StudentDataTable", "StorageApi", "StorageFunction", "StorageErrorsAlarm"):
    assert "Condition: UseStorage" in block(name) or "Condition: UseAlertsAndStorage" in block(name), f"{name} exists only when storage is on"
globals_ = template[template.index("\nGlobals:"):template.index("\nResources:")]
assert "STORAGE_ENABLED: !Ref StorageEnabled" in globals_
assert "STORAGE_URL" not in globals_, "the URL is given to the portal function only: putting it in Globals would make StorageApi depend on itself"

# ---- the data table: keyed by partition and id, on demand, kept when the stack is deleted ----
table = block("StudentDataTable")
assert "BillingMode: PAY_PER_REQUEST" in table
assert re.search(r"AttributeName: s\n\s+KeyType: HASH", table) and re.search(r"AttributeName: id\n\s+KeyType: RANGE", table)
assert "DeletionPolicy: Retain" in table and "UpdateReplacePolicy: Retain" in table, "student data must outlive a stack change"

# ---- what the storage function may do ----
fn = block("StorageFunction")
assert "Handler: storage_api.handler" in fn
assert re.search(r"Action: \[dynamodb:GetItem, dynamodb:UpdateItem\]\n\s+Resource: !GetAtt TokensTable\.Arn", fn), "tokens table: key lookup and counters only"
assert re.search(r"Action: \[dynamodb:GetItem, dynamodb:PutItem, dynamodb:DeleteItem, dynamodb:Query\]\n\s+Resource: !GetAtt StudentDataTable\.Arn", fn)
assert re.search(r"Action: \[dynamodb:PutItem, dynamodb:DeleteItem\]\n\s+Resource: !GetAtt TokensTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['scoll#\*'\]", fn), \
    "the tokens table may be written only under the scoll# keys (the list of a student's collection names)"
assert len(re.findall(r"dynamodb:PutItem", fn)) == 2, "PutItem exactly twice: the data table and the scoll# keys"
assert "dynamodb:Scan" not in fn, "no scans"
objects = re.findall(r"\$\{SitesBucket\.Arn\}/[^']*", fn)
assert objects == ["${SitesBucket.Arn}/userdata/*"], f"objects: userdata/ only, found {objects}"
assert re.search(r"Action: s3:ListBucket\n\s+Resource: !GetAtt SitesBucket\.Arn\n\s+Condition:\n\s+StringLike:\n\s+s3:prefix: \['userdata/\*'\]", fn), "listing is limited to userdata/"
assert "lambda:" not in fn and "secretsmanager" not in fn and "cloudfront" not in fn and "iam:" not in fn
assert "Timeout: 10" in fn

# ---- nobody else gains access ----
for name in ("SweeperFunction", "GatewayFunction", "StudentSandboxRole", "StudentSandboxBoundary"):
    assert "StudentDataTable" not in block(name), f"{name} must not be able to reach the data table"
    assert "userdata" not in block(name), f"{name} must not name the userdata folder"
# the portal's function may only FIND and DELETE a student's items (the teacher's "clear"): it can never read or write a value
deploy = block("DeployFunction")
grants = re.findall(r"Action: \[([^\]]*)\]\n\s+Resource: !GetAtt StudentDataTable\.Arn", deploy)
assert grants == ["dynamodb:Query, dynamodb:BatchWriteItem, dynamodb:DeleteItem"], grants
assert "- UseStorage" in deploy and "DATA_TABLE: !If [UseStorage, !Ref StudentDataTable, '']" in deploy, "only when storage is on"
assert "userdata" not in deploy, "its bucket access is the existing one; nothing new names userdata"
assert "STORAGE_URL: !If [UseStorage" in block("DeployFunction"), "the portal function learns the storage address"
assert re.search(r"AllowedMethods: \[POST, GET\]\n\s+AllowedHeaders: \['\*'\]\n\s+AllowedOrigins: \['\*'\]", block("SitesBucket")), "browsers may POST to and GET from presigned links"
assert "Principal: '*'" not in template and "PublicAccessBlockConfiguration" in block("SitesBucket"), "the bucket stays private"
assert "userdata" not in block("SitesBucketPolicy"), "CloudFront never serves userdata/"

# ---- its own API and throttle, so a student's loop cannot starve the portal ----
api = block("StorageApi")
assert re.search(r"ThrottlingBurstLimit: 40\n\s+ThrottlingRateLimit: 20", api)
assert "ApiId: !Ref StorageApi" in fn

# ---- the alarm watches the right function ----
alarm = block("StorageErrorsAlarm")
assert "MetricName: Errors" in alarm and "Value: !Ref StorageFunction" in alarm and "AlarmActions: [!Ref AlertsTopic]" in alarm
assert "StorageApiUrl:" in template and "StudentDataTableName:" in template

print("test_template_storage.py: the storage connector is off by default, tightly scoped and private")
