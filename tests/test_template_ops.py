"""Guards the operations safety nets in template.yaml: S3 versioning (the undo) and the alarms.

If someone removes versioning, drops an alarm, points an alarm at nothing, or commits an email address, this fails.
Reads the text of the template (CloudFormation tags make it awkward to parse as YAML)."""
import glob
import os
import re

root = os.path.join(os.path.dirname(__file__), "..")
template = open(os.path.join(root, "template.yaml"), encoding="utf8").read().replace("\r\n", "\n")


def block(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n)", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


# ---- S3 versioning is the undo button, and old versions do not pile up forever ----
bucket = block("SitesBucket")
assert re.search(r"VersioningConfiguration:\n\s+Status: Enabled", bucket), "versioning must stay enabled"
assert re.search(r"Id: ExpireOldVersions[\s\S]*?NoncurrentDays: 30(?!\d)", bucket), "old versions expire after 30 days (recoverable meanwhile)"
assert re.search(r"Id: ExpireUploadedZips[\s\S]*?ExpirationInDays: 1(?!\d)[\s\S]*?NoncurrentDays: 1(?!\d)", bucket), "temporary upload zips vanish quickly"
assert "ExpiredObjectDeleteMarker: true" in bucket and "AbortIncompleteMultipartUpload" in bucket

# ---- one topic, one confirmed email, nothing hard-coded ----
for name in ("AlertsTopic", "AlertsTopicPolicy", "AlertsEmail"):
    assert "Condition: UseAlerts" in block(name), name
assert "UseAlerts: !Not [!Equals [!Ref AlertEmailParameter, '']]" in template
subscription = block("AlertsEmail")
assert "Protocol: email" in subscription and "{{resolve:ssm:${AlertEmailParameter}}}" in subscription, "the address is read from SSM, never written here"
policy = block("AlertsTopicPolicy")
assert "cloudwatch.amazonaws.com" in policy, "CloudWatch must be allowed to publish"
assert policy.count("aws:SourceAccount: !Ref AWS::AccountId") == 1, "CloudWatch may publish only for this account"

assert "AWS::Budgets::Budget" not in template, "no budget here: the account keeps its own"

# ---- the alarms: each exists, watches the right thing, and notifies the topic ----
alarms = {
    "LambdaConcurrencyAlarm": ("ConcurrentExecutions", "AWS/Lambda"),
    "LambdaThrottlesAlarm": ("Throttles", "AWS/Lambda"),
    "DeployFunctionErrorsAlarm": ("Errors", "AWS/Lambda"),
    "SweeperErrorsAlarm": ("Errors", "AWS/Lambda"),
    "GatewayErrorsAlarm": ("Errors", "AWS/Lambda"),
    "BackendServerErrorsAlarm": ("5xx", "AWS/ApiGateway"),
}
for name, (metric, namespace) in alarms.items():
    text = block(name)
    assert f"MetricName: {metric}" in text and f"Namespace: {namespace}" in text, name
    assert "AlarmActions: [!Ref AlertsTopic]" in text, f"{name} must notify the topic"
    assert "TreatMissingData: notBreaching" in text, f"{name}: quiet periods are not failures"
for name in ("GatewayErrorsAlarm", "BackendServerErrorsAlarm"):
    assert "Condition: UseAlertsAndBackend" in block(name), f"{name} only exists when backends are deployed"
assert "Threshold: !Ref ConcurrencyAlarmThreshold" in block("LambdaConcurrencyAlarm")
assert re.search(r"ConcurrencyAlarmThreshold:\n    Type: Number\n    Default: 8", template), "8 suits the current account limit of 10"

# ---- no email address may be committed (the alert address lives in SSM; samconfig names only the parameter) ----
EMAIL = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")
checked = [os.path.join(root, f) for f in ("template.yaml", "samconfig.toml", "README.md", "docs/implement.md", "docs/backend_wire.md", "docs/learning.md")]
checked += glob.glob(os.path.join(root, "docs", "*.md")) + glob.glob(os.path.join(root, "custom-domain", "*"))
checked += [p for p in glob.glob(os.path.join(root, "tests", "*")) if os.path.isfile(p)]
for path in checked:
    if os.path.isfile(path):
        found = EMAIL.search(open(path, encoding="utf8", errors="ignore").read())
        assert not found, f"an email address appears in {os.path.relpath(path, root)}; keep addresses out of the repo"
_sam = open(os.path.join(root, "samconfig.toml"), encoding="utf8").read()
if re.search(r"^parameter_overrides", _sam, re.M):  # a teacher's own samconfig; the public repository ships a template with no overrides
    assert "/student-deploy/alert-email" in _sam, "samconfig names the SSM parameter only"

# ---- the kit and the portal pages (portal/) are published only by the teacher's own credentials ----
# The functions that take untrusted uploads must never be able to write there: a student's computer runs the kit it downloads from it.
assert "${SitesBucket.Arn}/*'" not in template, "no grant on the whole bucket: name the prefixes (sites, archive, uploads, userdata)"
grants = re.findall(r"Action: \[s3:GetObject, s3:PutObject, s3:DeleteObject\][^\n]*\n\s+Resource:\n((?:\s+- [^\n]+\n)+)", template)
assert len(grants) == 2, "the upload-handling functions list their prefixes; a new S3 write grant must be added to this check"
for statement in grants:
    for line in statement.strip().split("\n"):
        if "SitesBucket.Arn" in line:  # (the list ends where the next statement starts)
            assert re.search(r"/(sites|archive|uploads)/\*'", line), f"a function that handles uploads may only write its own prefixes: {line.strip()}"
for name in ("DeployFunction", "SweeperFunction"):
    assert not [l for l in block(name).split("\n") if "SitesBucket.Arn}/" in l and "portal" in l], f"{name} must not be granted portal/"
assert re.search(r"Action: s3:DeleteObject\n\s+Resource: !Sub '\$\{SitesBucket\.Arn\}/userdata/\*'", block("DeployFunction")), "the portal may only delete students' stored files (the teacher's clear), never read or write them"
assert template.count("s3:PutObject") == 3, "every S3 write grant is reviewed (two lists above, class storage's userdata/): extend this check for a new one"

# ---- the kit signing key: only the account's administrators (the teacher's own credentials) can use it ----
signing = block("KitSigningKey")
assert "KeyUsage: SIGN_VERIFY" in signing and "KeySpec: ECC_NIST_P256" in signing, "an ECDSA P-256 signing key (what students' tools verify)"
assert re.findall(r"kms:[A-Za-z*]+", template) == ["kms:*"] and "Action: 'kms:*'" in signing, "no function role may have any kms: permission: only the key policy's account administrators"
assert re.search(r"KitSigningKeyId:\n\s+Description: [^\n]*\n\s+Value: !Ref KitSigningKey", template), "portal-publish reads the key id from the stack outputs"

print("test_template_ops.py: all passed")
