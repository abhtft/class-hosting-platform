"""docs/TECHNICAL_SPECIFICATION.md is the source of truth, so its numbers must be the code's numbers.

The specification carries a JSON block (```json spec-facts). This test extracts it and compares every value with the Lambda code, the
CloudFormation template, samconfig.toml, the kit and the test runner. Change a limit, a timeout, a default, the kit version or the number
of test steps without updating the specification and this test fails, naming the fact that drifted."""
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


def check(name, expected, actual):
    if expected != actual:
        failures.append(f"{name}: the specification says {expected!r}, the code says {actual!r}")


def num(expression):
    product = 1
    for part in expression.split("*"):
        product *= int(part.strip().replace("_", ""))
    return product


def const(text, name):
    match = re.search(rf"^{name} = ([\d_ *]+)", text, re.M)
    assert match, f"constant {name} not found"
    return num(match.group(1))


MB = 1024 * 1024
app, backends, gateway, portal, ai = (read(f"lambda/{n}.py") for n in ("app", "backends", "gateway", "portal", "ai"))
template = read("template.yaml")

# ---- Lambda constants ----
f = facts["frontend"]
check("frontend.maxZipMB", f["maxZipMB"], const(app, "MAX_ZIP_BYTES") // MB)
check("frontend.maxUnzippedMB", f["maxUnzippedMB"], const(app, "MAX_UNZIPPED_BYTES") // MB)
check("frontend.maxFiles", f["maxFiles"], const(app, "MAX_FILES"))

b = facts["backend"]
check("backend.maxZipMB", b["maxZipMB"], const(backends, "MAX_ZIP_BYTES") // MB)
check("backend.maxUnzippedMB", b["maxUnzippedMB"], const(backends, "MAX_UNZIPPED_BYTES") // MB)
check("backend.maxFiles", b["maxFiles"], const(backends, "MAX_FILES"))
check("backend.memoryMB", b["memoryMB"], const(backends, "MEMORY_MB"))
check("backend.timeoutSeconds", b["timeoutSeconds"], const(backends, "TIMEOUT_SECONDS"))
check("backend.logRetentionDays", b["logRetentionDays"], const(backends, "LOG_RETENTION_DAYS"))
env_limits = re.search(r"^MAX_ENV_VARS, MAX_ENV_BYTES = (\d+), (\d+)", backends, re.M)
check("backend.maxEnvVars", b["maxEnvVars"], int(env_limits.group(1)))
check("backend.maxEnvBytes", b["maxEnvBytes"], int(env_limits.group(2)))

g = facts["gateway"]
check("gateway.maxBodyBytes", g["maxBodyBytes"], const(gateway, "MAX_BODY_CHARS"))
check("gateway.maxResponseBytes", g["maxResponseBytes"], const(gateway, "MAX_RESPONSE_BYTES"))
check("gateway.leakSeconds", g["leakSeconds"], const(gateway, "LEAK_SECONDS"))
globals_ = template[template.index("\nGlobals:"):template.index("\nResources:")]
check("gateway.inflightGlobal (template Globals)", str(g["inflightGlobal"]), re.search(r"INFLIGHT_GLOBAL: '(\d+)'", globals_).group(1))
check("gateway.inflightPerStudent (template Globals)", str(g["inflightPerStudent"]), re.search(r"INFLIGHT_STUDENT: '(\d+)'", globals_).group(1))
check("gateway.perIpPerMinute (template Globals)", str(g["perIpPerMinute"]), re.search(r"PER_IP_PER_MINUTE: '(\d+)'", globals_).group(1))
check("gateway.inflightGlobal (gateway default)", g["inflightGlobal"], int(re.search(r'setting\("INFLIGHT_GLOBAL", (\d+)\)', gateway).group(1)))
check("gateway.inflightPerStudent (gateway default)", g["inflightPerStudent"], int(re.search(r'setting\("INFLIGHT_STUDENT", (\d+)\)', gateway).group(1)))
check("gateway.perIpPerMinute (gateway default)", g["perIpPerMinute"], int(re.search(r'setting\("PER_IP_PER_MINUTE", (\d+)\)', gateway).group(1)))

s = facts["sessions"]
check("sessions.teacherHours", s["teacherHours"], const(portal, "TEACHER_SESSION_SECONDS") // 3600)
check("sessions.studentHours", s["studentHours"], const(portal, "STUDENT_SESSION_SECONDS") // 3600)
check("sessions.maxFailedLogins", s["maxFailedLogins"], const(portal, "MAX_FAILED_LOGINS"))
check("sessions.lockoutMinutes", s["lockoutMinutes"], const(portal, "LOCKOUT_SECONDS") // 60)

t = facts["tokens"]
check("tokens.maxDays", t["maxDays"], const(portal, "MAX_DAYS"))
check("tokens.graceDays", t["graceDays"], int(re.search(r'GRACE_DAYS = int\(os\.environ\.get\("OFFLINE_GRACE_DAYS", "(\d+)"\)\)', portal).group(1)))
check("tokens.warnDays", t["warnDays"], int(re.search(r'WARN_DAYS = int\(os\.environ\.get\("WARN_DAYS", "(\d+)"\)\)', portal).group(1)))
reserved = set(re.findall(r'"([a-z]+)"', re.search(r"^RESERVED = \{(.*?)\}", portal, re.M).group(1)))
check("reservedSlugs", sorted(facts["reservedSlugs"]), sorted(reserved))

a = facts["ai"]
limits = re.search(r"^MIN_LIMIT_USD, MAX_LIMIT_USD = ([\d.]+), ([\d.]+)", ai, re.M)
check("ai.minUsd", a["minUsd"], float(limits.group(1)))
check("ai.maxUsd", a["maxUsd"], float(limits.group(2)))
check("ai.defaultUsd (ai.py)", a["defaultUsd"], float(re.search(r'AI_DEFAULT_LIMIT_USD", "([\d.]+)"', ai).group(1)))
check("ai.refreshSeconds", a["refreshSeconds"], const(ai, "REFRESH_SECONDS"))
check("ai.studentTimeoutSeconds (ai.py)", a["studentTimeoutSeconds"], int(re.search(r'AI_STUDENT_TIMEOUT", "(\d+)"', ai).group(1)))
check("ai.studentTimeoutSeconds (template)", str(a["studentTimeoutSeconds"]), re.search(r"AI_STUDENT_TIMEOUT: '(\d+)'", globals_).group(1))
check("ai.defaultModel (ai.py)", a["defaultModel"], re.search(r'AI_DEFAULT_MODEL", "([^"]+)"', ai).group(1))
check("ai.envNames", a["envNames"], re.findall(r'"([A-Z_]+)"', re.search(r"^AI_ENV_NAMES = \((.*?)\)", ai, re.M).group(1)))

st = facts["storage"]
storage_src = read("lambda/storage.py")
check("storage.maxValueKB", st["maxValueKB"], const(storage_src, "MAX_VALUE_BYTES") // 1024)
check("storage.maxItems", st["maxItems"], const(storage_src, "MAX_ITEMS"))
check("storage.maxFileMB", st["maxFileMB"], int(re.search(r"^MAX_FILE_BYTES = (\d+) \* MB", storage_src, re.M).group(1)))
check("storage.maxFiles", st["maxFiles"], const(storage_src, "MAX_FILES"))
check("storage.maxCollections", st["maxCollections"], const(storage_src, "MAX_COLLECTIONS"))
defaults = lambda name: tuple(int(x.strip().replace("_", "")) for x in re.search(rf"^{name} = ([\d_, ]+)", storage_src, re.M).group(1).split(","))  # noqa: E731
check("storage.defaultDataMB", st["defaultDataMB"], defaults("DEFAULT_DATA_MB, MAX_DATA_MB")[0])
check("storage.maxDataMB", st["maxDataMB"], defaults("DEFAULT_DATA_MB, MAX_DATA_MB")[1])
check("storage.defaultFilesMB", st["defaultFilesMB"], defaults("DEFAULT_FILES_MB, MAX_FILES_MB")[0])
check("storage.maxFilesMB", st["maxFilesMB"], defaults("DEFAULT_FILES_MB, MAX_FILES_MB")[1])
check("storage.defaultOps", st["defaultOps"], defaults("DEFAULT_OPS, MAX_OPS")[0])
check("storage.defaultPeriod", st["defaultPeriod"], re.search(r'^DEFAULT_PERIOD = "(\w+)"', storage_src, re.M).group(1))
check("storage.listPageItems", st["listPageItems"], defaults("LIST_PAGE_ITEMS, LIST_PAGE_BYTES")[0])
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

for name, expected in facts["apiThrottles"].items():
    text = block_of(name)
    check(f"apiThrottles.{name}.rate", expected["rate"], int(re.search(r"ThrottlingRateLimit: (\d+)", text).group(1)))
    check(f"apiThrottles.{name}.burst", expected["burst"], int(re.search(r"ThrottlingBurstLimit: (\d+)", text).group(1)))

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
check("testSteps", facts["testSteps"], len(re.findall(r"^\s*await step\(", read("tests/run.mjs"), re.M)))

# Mermaid reads pk, fk and uk as key constraints, so an attribute with that name is a syntax error and the diagram does not render
for er in re.findall(r"```mermaid\nerDiagram\n(.*?)```", spec, re.S):
    bad = re.findall(r"^\s+\w+\s+(?:pk|fk|uk)(?:\s|$)", er, re.M | re.I)
    assert not bad, f"an entity attribute is named pk, fk or uk, which Mermaid rejects: {bad}"

assert not failures, "the specification has drifted from the code:\n  " + "\n  ".join(failures)
print("test_spec.py: every machine-checked fact in the specification matches the code")
