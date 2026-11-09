"""The settings registry (config/settings.json) is the one place every platform limit, default and time is described. This test keeps it honest:

  * the registry itself is well formed (every setting explained, defaults inside their ranges, scopes and risks known, each tied to code or to the template);
  * the generated files (lambda/platform_settings_data.py and docs/SETTINGS.md) are current;
  * the loader (lambda/platform_settings.py) behaves as documented (defaults, the environment variable a template passes in, units, groups);
  * the CloudFormation template agrees with the registry (every parameter is a registry setting, with the same default, range and choices);
  * and no Lambda file holds a limit of its own: a module-level number must come from the loader or be listed in the registry as kept in code on purpose,
    and an environment variable may not be read with a number as its fallback (add the setting to the registry instead)."""
import ast
import importlib.util
import json
import os
import re
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))


def read(path):
    return open(os.path.join(root, path), encoding="utf8").read().replace("\r\n", "\n")


registry = json.loads(read("config/settings.json"))
settings = registry["settings"]
by_key = {s["key"]: s for s in settings}
group_ids = {g["id"] for g in registry["groups"]}
template = read("template.yaml")
problems = []


def bad(text):
    problems.append(text)


# ---------------------------------------------------------------- 1. the registry is well formed
assert registry["formatVersion"] == 1
assert len(by_key) == len(settings), "a key is used twice"
KEY = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$")
for s in settings:
    k = s["key"]
    if not KEY.match(k):
        bad(f"{k}: not a valid key")
    if s["group"] not in group_ids:
        bad(f"{k}: unknown group {s['group']!r}")
    if not s["title"] or len(s["title"]) > 60:
        bad(f"{k}: a title of up to 60 characters is needed")
    if not s["help"] or len(s["help"]) > 330:
        bad(f"{k}: a plain explanation of up to 330 characters is needed")
    if s["scope"] not in ("runtime", "deploy"):
        bad(f"{k}: scope must be runtime or deploy")
    if s["risk"] not in ("low", "careful", "security"):
        bad(f"{k}: risk must be low, careful or security")
    if not s.get("code") and not s.get("template"):
        bad(f"{k}: tied to nothing: it needs a code reference or a template reference")
    d, lo, hi = s["default"], s.get("min"), s.get("max")
    if isinstance(d, (int, float)) and not isinstance(d, bool):
        if lo is not None and d < lo:
            bad(f"{k}: default {d} is below its minimum {lo}")
        if hi is not None and d > hi:
            bad(f"{k}: default {d} is above its maximum {hi}")
        if lo is not None and hi is not None and lo > hi:
            bad(f"{k}: minimum above maximum")
    if s.get("choices") and d not in s["choices"]:
        bad(f"{k}: default {d!r} is not one of its choices")
    if s.get("env") and not re.match(r"^[A-Z][A-Z0-9_]+$", s["env"]):
        bad(f"{k}: env must be an environment variable name")
for g in group_ids:
    if not any(s["group"] == g for s in settings):
        bad(f"group {g} has no settings")
assert len(settings) >= 150, "the registry should hold every setting"
print(f"  ok  the registry: {len(settings)} settings in {len(group_ids)} groups, every one explained, inside its range and tied to code or the template")

# ---------------------------------------------------------------- 2. generated files are current
spec = importlib.util.spec_from_file_location("gen_settings", os.path.join(root, "tools", "gen_settings.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
for path, text in ((gen.DATA, gen.render_data(registry)), (gen.DOCS, gen.render_docs(registry))):
    if read(os.path.relpath(path, root)) != text:
        bad(f"{os.path.relpath(path, root)} is out of date: run  python tools/gen_settings.py")
print("  ok  lambda/platform_settings_data.py and docs/SETTINGS.md are generated from the registry and current")

# ---------------------------------------------------------------- 3. the loader
import platform_settings as ps  # noqa: E402

for s in settings:
    if ps.default(s["key"]) != s["default"] or ps.minimum(s["key"]) != s.get("min") or ps.maximum(s["key"]) != s.get("max"):
        bad(f"{s['key']}: the loader and the registry disagree")
try:
    ps.value("no.such.setting")
    bad("an unknown key must raise")
except KeyError as error:
    assert "config/settings.json" in str(error)
assert ps.value("frontend.maxZipMB") == 50 and ps.mb("frontend.maxZipMB") == 50 * 1024 * 1024
assert ps.kb("gateway.maxBodyKB") == 1024 * 1024 and ps.hours("sessions.teacherHours") == 8 * 3600 and ps.minutes("sessions.lockoutMinutes") == 15 * 60
assert ps.defaults("knowledge.student")["collections"] == 5 and "searches_period" in ps.defaults("knowledge.student")
assert "searches_period" not in ps.bounds("knowledge.student") and ps.bounds("knowledge.student")["collections"] == (1, 20)
assert not ps.defaults("knowledge.stud"), "a prefix matches whole name parts only"
# a deploy-time setting is read from the environment variable the template sets; empty or missing means the default
os.environ.pop("OFFLINE_GRACE_DAYS", None)
assert ps.value("tokens.graceDays") == 3
os.environ["OFFLINE_GRACE_DAYS"] = "7"
assert ps.value("tokens.graceDays") == 7 and isinstance(ps.value("tokens.graceDays"), int)
os.environ["OFFLINE_GRACE_DAYS"] = ""
assert ps.value("tokens.graceDays") == 3
os.environ["AI_DEFAULT_LIMIT_USD"] = "1.5"
assert ps.value("ai.defaultLimitUsd") == 1.5
os.environ["AI_DEFAULT_MODEL"] = "some/model"
assert ps.value("ai.defaultModel") == "some/model"
for name in ("OFFLINE_GRACE_DAYS", "AI_DEFAULT_LIMIT_USD", "AI_DEFAULT_MODEL"):
    os.environ.pop(name, None)
assert ps.value("ai.defaultLimitUsd") == 0.25 and ps.value("ai.defaultModel") == "anthropic/claude-haiku-5.5"
print("  ok  the loader: defaults, ranges, units, groups, the environment variable a template passes in, unknown keys refused")

# ---------------------------------------------------------------- 4. the code and the registry
lambda_dir = os.path.join(root, "lambda")
files = sorted(f for f in os.listdir(lambda_dir) if f.endswith(".py") and not f.startswith("platform_settings"))
sources = {f: read(f"lambda/{f}") for f in files}
fixed = {item["code"] for item in registry["fixed"]}
assert len(fixed) == len(registry["fixed"]), "a fixed entry is listed twice"
P = "platform_settings"


def module_assigns(source):
    out = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = []
            for t in targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
                elif isinstance(t, ast.Tuple):
                    names += [e.id for e in t.elts if isinstance(e, ast.Name)]
            for n in names:
                out[n] = node
    return out


def has_number(node):
    return any(isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool) for n in ast.walk(node))


def uses_loader(node):
    return any(isinstance(n, ast.Name) and n.id == P for n in ast.walk(node))


assigns = {f: module_assigns(sources[f]) for f in files}
# every module-level number comes from the loader, or is listed as kept in code on purpose
for f, names in assigns.items():
    for name, node in names.items():
        if not name.replace("_", "").isupper() or not has_number(node.value):
            continue
        if not uses_loader(node.value) and f"{f[:-3]}.{name}" not in fixed:
            bad(f"lambda/{f}:{node.lineno}: {name} holds a number of its own. Add it to config/settings.json (and read it through platform_settings), or list it under \"fixed\" with the reason")
# every fixed entry still exists in the code
for item in fixed:
    module, name = item.split(".")
    if f"{module}.py" not in assigns or name not in assigns[f"{module}.py"]:
        bad(f"fixed entry {item}: no such module-level constant any more")
# an environment variable is never read with a number as its fallback
for f, source in sources.items():
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "environ":
            if len(node.args) == 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, (int, float, str)) and re.fullmatch(r"-?\d+(\.\d+)?", str(node.args[1].value)):
                bad(f"lambda/{f}:{node.lineno}: an environment variable with the number {node.args[1].value!r} as its fallback: make it a setting in config/settings.json")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setting":
            bad(f"lambda/{f}:{node.lineno}: a local setting(...) helper: read the loader instead")
# every setting with code references is really used by the code
all_text = "\n".join(sources.values())
group_reads = set(re.findall(rf'{P}\.(?:defaults|bounds)\("([^"]+)"\)', all_text))  # a whole group read at once, for example defaults("knowledge.student")
for s in settings:
    if s.get("code") and f'"{s["key"]}"' not in all_text and not any(s["key"].startswith(g + ".") for g in group_reads):
        bad(f"{s['key']}: no Lambda file reads it (platform_settings.*(\"{s['key']}\"))")
    for ref in s.get("code", []):
        module, name = ref.split(".")
        if name.replace("_", "").isupper() and name in assigns.get(f"{module}.py", {}) and not uses_loader(assigns[f"{module}.py"][name].value):
            bad(f"{s['key']}: {ref} does not read the loader")
# every key used in code exists
for f, source in sources.items():
    for key in re.findall(rf'{P}\.\w+\("([^"]+)"\)', source):
        if key not in by_key and key not in group_reads:
            bad(f"lambda/{f}: reads {key!r}, which is not in the registry")
print("  ok  no Lambda file holds a limit of its own: every module-level number comes from the loader or is listed as kept in code on purpose; no environment variable has a number as its fallback")

# ---------------------------------------------------------------- 5. the template agrees
parameters = template[template.index("\nParameters:"):template.index("\nRules:")]
globals_text = template[template.index("\nGlobals:"):template.index("\nResources:")]


def parameter(name):
    found = re.search(rf"\n  {name}:\n((?:    [^\n]*\n|      [^\n]*\n)*)", parameters)
    assert found, f"parameter {name} not found"
    body = found.group(1)
    pick = lambda key: (re.search(rf"\n?    {key}: ('?)(.*?)\1\n", "\n" + body) or [None, None, None])[2]  # noqa: E731
    allowed = re.search(r"    AllowedValues: \[(.*?)\]", body)
    return {"default": pick("Default"), "min": pick("MinValue"), "max": pick("MaxValue"), "allowed": [a.strip().strip("'\"") for a in allowed.group(1).split(",")] if allowed else None, "type": pick("Type")}


def same(registry_value, text):
    if isinstance(registry_value, (int, float)) and not isinstance(registry_value, bool):
        return float(text) == float(registry_value)
    return str(registry_value) == text


def block_of(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n)", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


template_parameters = set(re.findall(r"^  ([A-Za-z]+):\n    Type:", parameters, re.M))
claimed = set()
for s in settings:
    ref = s.get("template")
    if not ref:
        continue
    kind, _, rest = ref.partition(":")
    k = s["key"]
    if kind == "Parameter":
        claimed.add(rest)
        if rest not in template_parameters:
            bad(f"{k}: the template has no parameter {rest}")
            continue
        p = parameter(rest)
        if not same(s["default"], p["default"] if p["default"] is not None else ""):
            bad(f"{k}: the registry default {s['default']!r} differs from the template parameter {rest} default {p['default']!r}")
        if p["min"] is not None and not same(s["min"], p["min"]):
            bad(f"{k}: the registry minimum {s.get('min')!r} differs from the template's MinValue {p['min']}")
        if p["max"] is not None and not same(s["max"], p["max"]):
            bad(f"{k}: the registry maximum {s.get('max')!r} differs from the template's MaxValue {p['max']}")
        if p["allowed"] is not None and sorted(map(str, s.get("choices", []))) != sorted(p["allowed"]):
            bad(f"{k}: the registry choices {s.get('choices')} differ from the template's AllowedValues {p['allowed']}")
        if s.get("env"):
            lines = re.findall(rf"\n\s+{s['env']}: (.*)", template)
            if not lines or any(f"!Ref {rest}" not in line for line in lines):
                bad(f"{k}: the template should set {s['env']} from !Ref {rest} wherever it sets it")
    elif kind == "Env":
        found = re.search(rf"\n\s+{rest}: '?([^'\s#]+)'?", template)
        if not found or not same(s["default"], found.group(1)):
            bad(f"{k}: the registry default {s['default']!r} differs from the template value of {rest} ({found.group(1) if found else None!r})")
    elif kind == "Globals":
        found = re.search(rf"\n\s+{rest}: '?([^'\s#]+)'?", globals_text)
        if not found or not same(s["default"], found.group(1)):
            bad(f"{k}: the registry default {s['default']!r} differs from the template Globals {rest} ({found.group(1) if found else None!r})")
    elif kind == "Lambda":
        name, field = rest.split(".")
        found = re.search(rf"\n      {field}: (\d+)", block_of(name))
        if not found or int(found.group(1)) != s["default"]:
            bad(f"{k}: the registry default {s['default']!r} differs from the template {name}.{field}")
    elif kind == "Api":
        name, field = rest.split(".")
        text = block_of(name)
        literal = re.search(rf"{field}: (\d+)", text)
        if not literal or int(literal.group(1)) != s["default"]:
            bad(f"{k}: the registry default {s['default']!r} differs from the template {name}.{field}")
    elif kind == "Schedule":
        found = re.search(r"Schedule: (cron\([^)]*\))", block_of(rest))
        if not found or found.group(1) != s["default"]:
            bad(f"{k}: the registry default differs from the template schedule")
    elif kind == "Bucket":
        rule, field = rest.split(".")
        found = re.search(rf"Id: {rule}[\s\S]*?{field}: (\d+)", block_of("SitesBucket"))
        if not found or int(found.group(1)) != s["default"]:
            bad(f"{k}: the registry default {s['default']!r} differs from the template bucket rule {rule}.{field}")
    else:
        bad(f"{k}: unknown template reference {ref!r}")
for name in sorted(template_parameters - claimed):
    bad(f"the template parameter {name} is not in the registry: add a setting with \"template\": \"Parameter:{name}\"")
# every environment variable the template sets from a parameter, where that variable is a setting, points at the parameter the registry names
for env, expr in re.findall(r"\n\s+([A-Z][A-Z0-9_]+): (.*)", template):
    owners = [s for s in settings if s.get("env") == env]
    if owners and "!Ref " in expr:
        ref = expr.split("!Ref ")[1].split()[0].rstrip(",]")
        if not any(o.get("template") == f"Parameter:{ref}" for o in owners):
            bad(f"the template sets {env} from !Ref {ref}, but the registry setting for {env} points elsewhere")
print("  ok  the template agrees: every parameter is a registry setting with the same default, range and choices; Globals, function sizes, throttles, schedule and bucket rules match")

assert not problems, "settings drift:\n  " + "\n  ".join(problems)
print("test_settings.py: the registry, the code, the loader, the generated files and the template agree")
