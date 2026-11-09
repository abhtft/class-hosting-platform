"""docs/TECHNICAL_SPECIFICATION.md is the source of truth, so its numbers must be the code's numbers.

The specification carries a JSON block (```json spec-facts). This test extracts it and compares every value with the settings registry
(config/settings.json, which tests/test_settings.py proves equals the Lambda code and the CloudFormation template), the template itself, samconfig.toml,
the kit and the test runner. Change a limit, a timeout, a default, the kit version or the number of test steps without updating the specification and
this test fails, naming the fact that drifted."""
import json
import os
import re

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read(path):
    return open(os.path.join(root, path), encoding="utf8").read().replace("\r\n", "\n")


spec = read("docs/TECHNICAL_SPECIFICATION.md")
block = re.search(r"```json spec-facts\n(.*?)\n```", spec, re.S)
assert block, "the specification must contain a ```json spec-facts block"
facts = json.loads(block.group(1))
failures = []
registry = {s["key"]: s for s in json.loads(read("config/settings.json"))["settings"]}


def check(name, expected, actual):
    if expected != actual:
        failures.append(f"{name}: the specification says {expected!r}, the code says {actual!r}")


def setting(key, field="default", scale=1):
    value = registry[key][field]
    return value * scale if scale != 1 else value


MB = 1024 * 1024
template = read("template.yaml")

# ---- limits (the registry; tests/test_settings.py ties it to the code) ----
f = facts["frontend"]
check("frontend.maxZipMB", f["maxZipMB"], setting("frontend.maxZipMB"))
check("frontend.maxUnzippedMB", f["maxUnzippedMB"], setting("frontend.maxUnzippedMB"))
check("frontend.maxFiles", f["maxFiles"], setting("frontend.maxFiles"))

b = facts["backend"]
for fact, key in (("maxZipMB", "backend.maxZipMB"), ("maxUnzippedMB", "backend.maxUnzippedMB"), ("maxFiles", "backend.maxFiles"), ("memoryMB", "backend.memoryMB"),
                  ("timeoutSeconds", "backend.timeoutSeconds"), ("logRetentionDays", "backend.logRetentionDays"), ("maxEnvVars", "backend.maxEnvVars"), ("maxEnvBytes", "backend.maxEnvBytes")):
    check(f"backend.{fact}", b[fact], setting(key))

g = facts["gateway"]
globals_ = template[template.index("\nGlobals:"):template.index("\nResources:")]
check("gateway.maxBodyBytes", g["maxBodyBytes"], setting("gateway.maxBodyKB", scale=1024))
check("gateway.maxResponseBytes", g["maxResponseBytes"], setting("gateway.maxResponseKB", scale=1024))
check("gateway.leakSeconds", g["leakSeconds"], setting("gateway.leakSeconds"))
check("gateway.perIpPerMinute", g["perIpPerMinute"], setting("gateway.perIpPerMinute"))
check("gateway.inflightGlobal", g["inflightGlobal"], setting("gateway.inflightGlobal"))
check("gateway.inflightPerStudent", g["inflightPerStudent"], setting("gateway.inflightPerStudent"))
check("gateway.inflightGlobal (template parameter default)", str(g["inflightGlobal"]), re.search(r"  BackendInflightGlobal:\n    Type: Number\n    Default: (\d+)", template).group(1))
check("gateway.inflightPerStudent (template parameter default)", str(g["inflightPerStudent"]), re.search(r"  BackendInflightPerStudent:\n    Type: Number\n    Default: (\d+)", template).group(1))
assert "INFLIGHT_GLOBAL: !Ref BackendInflightGlobal" in globals_ and "INFLIGHT_STUDENT: !Ref BackendInflightPerStudent" in globals_
check("gateway.perIpPerMinute (template Globals)", str(g["perIpPerMinute"]), re.search(r"PER_IP_PER_MINUTE: '(\d+)'", globals_).group(1))

s = facts["sessions"]
check("sessions.teacherHours", s["teacherHours"], setting("sessions.teacherHours"))
check("sessions.studentHours", s["studentHours"], setting("sessions.studentHours"))
check("sessions.maxFailedLogins", s["maxFailedLogins"], setting("sessions.maxFailedLogins"))
check("sessions.lockoutMinutes", s["lockoutMinutes"], setting("sessions.lockoutMinutes"))

t = facts["tokens"]
check("tokens.maxDays", t["maxDays"], setting("tokens.maxDays"))
check("tokens.graceDays", t["graceDays"], setting("tokens.graceDays"))
check("tokens.warnDays", t["warnDays"], setting("tokens.warnDays"))
portal = read("lambda/portal.py")
reserved = set(re.findall(r'"([a-z]+)"', re.search(r"^RESERVED = \{(.*?)\}", portal, re.M).group(1)))
check("reservedSlugs", sorted(facts["reservedSlugs"]), sorted(reserved))

a = facts["ai"]
ai = read("lambda/ai.py")
check("ai.minUsd", a["minUsd"], setting("ai.defaultLimitUsd", "min"))
check("ai.maxUsd", a["maxUsd"], setting("ai.defaultLimitUsd", "max"))
check("ai.defaultUsd", a["defaultUsd"], setting("ai.defaultLimitUsd"))
check("ai.refreshSeconds", a["refreshSeconds"], setting("ai.refreshSeconds"))
check("ai.studentTimeoutSeconds", a["studentTimeoutSeconds"], setting("ai.studentTimeoutSeconds"))
check("ai.studentTimeoutSeconds (template)", str(a["studentTimeoutSeconds"]), re.search(r"AI_STUDENT_TIMEOUT: '(\d+)'", globals_).group(1))
check("ai.defaultModel", a["defaultModel"], setting("ai.defaultModel"))
check("ai.envNames", a["envNames"], re.findall(r'"([A-Z_]+)"', re.search(r"^AI_ENV_NAMES = \((.*?)\)", ai, re.M).group(1)))

st = facts["storage"]
storage_src = read("lambda/storage.py")
check("storage.maxValueKB", st["maxValueKB"], setting("storage.maxValueKB"))
check("storage.maxItems", st["maxItems"], setting("storage.maxItems"))
check("storage.maxFileMB", st["maxFileMB"], setting("storage.maxFileMB"))
check("storage.maxFiles", st["maxFiles"], setting("storage.maxFiles"))
check("storage.maxCollections", st["maxCollections"], setting("storage.maxCollections"))
check("storage.defaultDataMB", st["defaultDataMB"], setting("storage.dataMB"))
check("storage.maxDataMB", st["maxDataMB"], setting("storage.dataMB", "max"))
check("storage.defaultFilesMB", st["defaultFilesMB"], setting("storage.filesMB"))
check("storage.maxFilesMB", st["maxFilesMB"], setting("storage.filesMB", "max"))
check("storage.defaultOps", st["defaultOps"], setting("storage.ops"))
check("storage.defaultPeriod", st["defaultPeriod"], re.search(r'^DEFAULT_PERIOD = "(\w+)"', storage_src, re.M).group(1))
check("storage.listPageItems", st["listPageItems"], setting("storage.listPageItems"))
check("storage.envNames", st["envNames"], re.findall(r'"([A-Z_]+)"', re.search(r"^ENV_NAMES = \((.*?)\)", storage_src, re.M).group(1)))


# ---- template ----
def block_of(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n)", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


for name, expected in facts["lambdas"].items():
    text = block_of(name)
    check(f"lambdas.{name}.memory", expected["memory"], int(re.search(r"\n      MemorySize: (\d+)", text).group(1)))
    check(f"lambdas.{name}.timeout", expected["timeout"], int(re.search(r"\n      Timeout: (\d+)", text).group(1)))

def throttle(text, key):
    """The number written in the template, or (for the backend API, whose caps are parameters the installer sets) the parameter's default."""
    literal = re.search(rf"{key}: (\d+)", text)
    if literal:
        return int(literal.group(1))
    ref = re.search(rf"{key}: !Ref (\w+)", text).group(1)
    return int(re.search(rf"\n  {ref}:\n    Type: Number\n    Default: (\d+)", template).group(1))


for name, expected in facts["apiThrottles"].items():
    text = block_of(name)
    check(f"apiThrottles.{name}.rate", expected["rate"], throttle(text, "ThrottlingRateLimit"))
    check(f"apiThrottles.{name}.burst", expected["burst"], throttle(text, "ThrottlingBurstLimit"))

parameters = template[template.index("\nParameters:"):template.index("\nConditions:")]
for name, expected in facts["templateParameterDefaults"].items():
    found = re.search(rf"\n  {name}:\n(?:    [^\n]*\n)*?    Default: '?([^'\n]*)'?\n", parameters)
    check(f"templateParameterDefaults.{name}", expected, found.group(1) if found else None)

check("sweeperCron", facts["sweeperCron"], re.search(r"Schedule: (cron\([^)]*\))", block_of("SweeperFunction")).group(1))
bucket = block_of("SitesBucket")
check("s3Lifecycle.uploadsExpireDays", facts["s3Lifecycle"]["uploadsExpireDays"], int(re.search(r"Id: ExpireUploadedZips[\s\S]*?ExpirationInDays: (\d+)", bucket).group(1)))
check("s3Lifecycle.noncurrentVersionDays", facts["s3Lifecycle"]["noncurrentVersionDays"], int(re.search(r"Id: ExpireOldVersions[\s\S]*?NoncurrentDays: (\d+)", bucket).group(1)))
suffixes = sorted(re.findall(r"AlarmName: !Sub '\$\{AWS::StackName\}-([a-z0-9-]+)'", template))
check("alarms", sorted(facts["alarms"]), suffixes)

# ---- deployed values, the kit, the tests ----
samconfig = read("samconfig.toml")
if re.search(r"^parameter_overrides", samconfig, re.M):  # only where a deployment's own samconfig exists (not in the public template)
    for name, expected in facts["deployedParameters"].items():
        found = re.search(rf'{name}=\\"([^\\"]*)\\"', samconfig)
        check(f"deployedParameters.{name} (samconfig.toml)", expected, found.group(1) if found else None)
check("mcpPackage.version", facts["mcpPackage"]["version"], json.loads(read("starter/mcp/package.json"))["version"])
check("kitVersion", facts["kitVersion"], json.loads(read("starter/template/platform.json"))["kitVersion"])
check("teacherTools", facts["teacherTools"], len(re.findall(r"^  \{ name: '", read("teacher-mcp/server.mjs"), re.M)))
check("testSteps", facts["testSteps"], len(re.findall(r"^\s*await step\(", read("tests/run.mjs"), re.M)))

# Mermaid reads pk, fk and uk as key constraints, so an attribute with that name is a syntax error and the diagram does not render
for er in re.findall(r"```mermaid\nerDiagram\n(.*?)```", spec, re.S):
    bad = re.findall(r"^\s+\w+\s+(?:pk|fk|uk)(?:\s|$)", er, re.M | re.I)
    assert not bad, f"an entity attribute is named pk, fk or uk, which Mermaid rejects: {bad}"

assert not failures, "the specification has drifted from the code:\n  " + "\n  ".join(failures)
print("test_spec.py: every machine-checked fact in the specification matches the registry, the template and the code")
