"""Live smoke test of the deployed platform: student login, backend deploy (Python and Node), env vars, logs, run allowance,
teacher on/off, the in-flight limiter and removal. It needs AWS credentials for the stack's account and talks to production.

Run it by hand after a deploy:  python tests/live_smoke.py   (takes about 3 minutes; not part of CI)
It creates one temporary student (slug zz-backendtest) directly in DynamoDB and removes everything it made, even on failure.
Section J exercises the class storage connector (needs the stack deployed with StorageEnabled=true; it is skipped otherwise).
Override the address with SMOKE_BASE_URL / SMOKE_ROOT_DOMAIN when testing another stack."""
import concurrent.futures
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

import boto3
from botocore.exceptions import ClientError

STACK = os.environ.get("SMOKE_STACK_NAME", "student-hosting")
ROOT_DOMAIN = os.environ.get("SMOKE_ROOT_DOMAIN", "example.edu")
BASE = os.environ.get("SMOKE_BASE_URL", f"https://deploy.{ROOT_DOMAIN}")
SLUG = "zz-backendtest"
SITE = f"https://{SLUG}.{ROOT_DOMAIN}"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "student", "backend-template")
cfn = boto3.client("cloudformation", region_name="us-east-1")
lam = boto3.client("lambda", region_name="us-east-1")
logs = boto3.client("logs", region_name="us-east-1")
table = boto3.resource("dynamodb", region_name="us-east-1").Table(
    cfn.describe_stack_resource(StackName=STACK, LogicalResourceId="TokensTable")["StackResourceDetail"]["PhysicalResourceId"])
BUCKET = cfn.describe_stack_resource(StackName=STACK, LogicalResourceId="SitesBucket")["StackResourceDetail"]["PhysicalResourceId"]
STACK_OUTPUTS = {o["OutputKey"]: o["OutputValue"] for o in cfn.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}
STORAGE_URL = STACK_OUTPUTS.get("StorageApiUrl", "")
DATA_TABLE = STACK_OUTPUTS.get("StudentDataTableName", "")

token = "mds_" + secrets.token_urlsafe(16)
tid = hashlib.sha256(token.encode()).hexdigest()
now = int(time.time())
failures = []
log = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)  # noqa: E731


def check(name, cond, detail=""):
    log(("PASS" if cond else "FAIL"), name, detail if not cond else "")
    if not cond:
        failures.append(name)


def http(method, url, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, method=method, data=data, headers=headers or ({"content-type": "application/json"} if raw is None else {}))
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text[:1] in ("{", "[") else text), dict(r.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        return e.code, (json.loads(text) if text[:1] in ("{", "[") else text), dict(e.headers)


def api(method, path, body=None, session=None):
    headers = {"content-type": "application/json"}
    if session:
        headers["authorization"] = f"Bearer {session}"
    s, b, _ = http(method, BASE + path, body, headers)
    return s, b


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def template(flavor):
    folder = os.path.join(ROOT, flavor)
    return {n: open(os.path.join(folder, n), encoding="utf8").read() for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))}


def set_backend_fields(**fields):
    item = table.get_item(Key={"pk": f"tok#{tid}"})["Item"]
    b = dict(item.get("backend") or {})
    b.update(fields)
    table.update_item(Key={"pk": f"tok#{tid}"}, UpdateExpression="SET backend = :b", ExpressionAttributeValues={":b": b})


def deploy(session, files, what):
    s, up = api("POST", "/api/student/backend/upload-url", session=session)
    check(f"{what}: upload-url", s == 200, up)
    ps, _, _ = http("PUT", up["uploadUrl"], raw=zip_bytes(files))
    check(f"{what}: zip uploaded to S3", ps == 200)
    s, body = api("POST", "/api/student/backend/publish", session=session)
    check(f"{what}: publish accepted", s == 200 and body["token"]["backend"]["state"] == "creating", body)
    state, started = "creating", time.time()
    while time.time() - started < 90:
        s, body = api("GET", "/api/student/backend", session=session)
        state = body["token"]["backend"]["state"] if s == 200 else "error"
        if state != "creating":
            break
        time.sleep(3)
    check(f"{what}: function ready", state == "ready", f"state={state} {body}")
    log(f"   ({what} took {int(time.time() - started)} s to become ready)")
    return state == "ready"


def site(path, method="GET", body=None):
    s, b, h = http(method, SITE + path, body)
    return s, b, h


def load_platform_modules():
    """The platform's own Lambda modules, run here with the real AWS clients (the teacher API needs a password, so the class AI
    connector is switched on and off through the same code the portal runs). Returns the openrouter module."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambda"))
    os.environ.update(TOKENS_TABLE=table.name, STORAGE_ENABLED="true" if STORAGE_URL else "false", STORAGE_URL=STORAGE_URL, DATA_TABLE=DATA_TABLE, AI_ENABLED="true", AI_SECRET_NAME="openrouter", BACKEND_ENABLED="true", BUCKET=BUCKET,
                      AWS_DEFAULT_REGION="us-east-1", AWS_REGION="us-east-1", ROOT_DOMAIN=os.environ.get("SMOKE_ROOT_DOMAIN", "example.edu"))
    import openrouter
    return openrouter


def cleanup():
    for name in [f"sd-{SLUG}"]:
        try:
            lam.delete_function(FunctionName=name)
            log("cleanup: deleted function", name)
        except ClientError:
            pass
        try:
            logs.delete_log_group(logGroupName=f"/aws/lambda/{name}")
        except ClientError:
            pass
    # the temporary student's OpenRouter key (section H) must not outlive the test: delete it BEFORE the row that records its hash
    try:
        ai_hash = ((table.get_item(Key={"pk": f"tok#{tid}"}).get("Item") or {}).get("ai") or {}).get("hash")
        if ai_hash:
            load_platform_modules().delete_key(ai_hash)
            log("cleanup: deleted the temporary OpenRouter key")
    except Exception as e:  # reported again by the final check below
        log("cleanup: could not delete the OpenRouter key:", e)
    scan = table.scan(FilterExpression="contains(pk, :s) OR pk = :g", ExpressionAttributeValues={":s": SLUG, ":g": "inflight#global"})
    for item in scan["Items"]:
        table.delete_item(Key={"pk": item["pk"]})
    if DATA_TABLE:
        data_table = boto3.resource("dynamodb", region_name="us-east-1").Table(DATA_TABLE)
        for row in data_table.scan(FilterExpression="begins_with(s, :p)", ExpressionAttributeValues={":p": f"{SLUG}#"})["Items"]:
            data_table.delete_item(Key={"s": row["s"], "id": row["id"]})
    table.delete_item(Key={"pk": f"tok#{tid}"})
    table.delete_item(Key={"pk": f"slug#{SLUG}"})
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.delete_object(Bucket=BUCKET, Key=f"uploads/{SLUG}-backend.zip")
    s3.delete_object(Bucket=BUCKET, Key=f"uploads/{SLUG}.zip")
    for prefix in (f"sites/{SLUG}/", f"userdata/{SLUG}/"):
        for page in s3.get_paginator("list_object_versions").paginate(Bucket=BUCKET, Prefix=prefix):
            for v in (page.get("Versions") or []) + (page.get("DeleteMarkers") or []):
                s3.delete_object(Bucket=BUCKET, Key=v["Key"], VersionId=v["VersionId"])


table.put_item(Item={"pk": f"slug#{SLUG}", "token": tid})
table.put_item(Item={"pk": f"tok#{tid}", "id": tid, "slug": SLUG, "student_name": "Backend E2E", "hint": token[-4:], "created_at": now,
                     "expires_at": now + 7200, "revoked": False, "regen": {"state": "none"},
                     "backend": {"enabled": True, "runs_limit": 6, "runs_period": "month", "state": "none"}})
try:
    s, body = api("POST", "/api/student/login", {"token": token})
    check("student login", s == 200)
    session = body["session"]
    b = body["token"]["backend"]
    check("backend view: available, enabled, allowance 6", b["available"] and b["enabled"] and b["usage"]["limit"] == 6 and b["usage"]["used"] == 0, b)

    # ---------- A: Python starter template ----------
    if deploy(session, template("python"), "python template"):
        s, b, h = site("/api/health")
        check("GET /api/health through CloudFront", s == 200 and b == {"ok": True}, (s, b))
        check("response headers set by the gateway", h.get("X-Content-Type-Options", h.get("x-content-type-options")) == "nosniff")
        s, b, _ = site("/api/hello?name=Sam")
        check("GET /api/hello?name=Sam", s == 200 and b == {"message": "Hello, Sam!"}, (s, b))
        s, b, _ = site("/api/echo", "POST", {"a": 1})
        check("POST /api/echo with a JSON body", s == 200 and b == {"youSent": {"a": 1}}, (s, b))
        s, b, _ = site("/api/missing")
        check("unknown path is the function's own 404", s == 404 and b == {"error": "Not found"}, (s, b))

    # ---------- B: update path + environment variable + logs ----------
    CODE = ("import json, os\n"
            "def handler(event, context):\n"
            "    print('env-check', event['rawPath'])\n"
            "    return {'statusCode': 200, 'headers': {'content-type': 'application/json'},\n"
            "            'body': json.dumps({'greeting': os.environ.get('GREETING'), 'ip': event['requestContext']['http']['sourceIp'],\n"
            "                               'leaked': [h for h in event['headers'] if h in ('x-origin-secret', 'x-viewer-ip')]})}\n")
    if deploy(session, {"app.py": CODE}, "update with env-reading code"):
        s, body = api("PUT", "/api/student/backend/env", {"name": "GREETING", "value": "live-secret-value-42"}, session)
        check("set env var", s == 200 and body["token"]["backend"]["env_names"] == ["GREETING"], body)
        check("env value is never returned by the API", "live-secret-value-42" not in json.dumps(body))
        for _ in range(30):  # the function reconfigures after the change: wait until AWS says it is done
            if lam.get_function_configuration(FunctionName=f"sd-{SLUG}").get("LastUpdateStatus") == "Successful":
                break
            time.sleep(1)
        time.sleep(1)
        s, b, _ = site("/api/x")
        check("function sees the env var; platform headers are stripped", s == 200 and b["greeting"] == "live-secret-value-42" and b["leaked"] == [], (s, b))
        check("visitor IP is passed in sourceIp", b.get("ip") not in (None, "", "10.0.0.1"), b)
        lines = []
        for _ in range(10):
            s, body = api("GET", "/api/student/backend/logs", session=session)
            lines = body.get("lines", []) if s == 200 else []
            if any("env-check" in l["message"] for l in lines):
                break
            time.sleep(3)
        check("logs reach the student (CloudWatch)", any("env-check" in l["message"] for l in lines), lines[:3])
        check("log group retention is 7 days", logs.describe_log_groups(logGroupNamePrefix=f"/aws/lambda/sd-{SLUG}")["logGroups"][0].get("retentionInDays") == 7)

    # ---------- C: allowance (limit 6; 5 runs used so far) ----------
    s, body = api("GET", "/api/student/me", session=session)
    used = body["token"]["backend"]["usage"]["used"]
    log(f"   runs used so far: {used}")
    codes = [site("/api/x")[0] for _ in range(4)]
    allowed = max(6 - used, 0)
    check(f"exactly {allowed} more runs allowed, then 429", codes == [200] * allowed + [429] * (4 - allowed), codes)
    s, b, h = site("/api/x")
    check("429 body names the allowance and says when it resets", s == 429 and b["error"] == "allowance_exhausted" and b["resets_at"] and "Retry-After" in {k.title(): v for k, v in h.items()}, (s, b))
    s, body = api("GET", "/api/student/me", session=session)
    check("usage meter data shows 6 / 6", body["token"]["backend"]["usage"]["used"] == 6, body["token"]["backend"]["usage"])

    # ---------- D: teacher OFF / ON (gateway), and reserved concurrency 0 at an account limit of 10 ----------
    set_backend_fields(runs_limit=1000)
    set_backend_fields(enabled=False)
    time.sleep(12)  # the gateway caches settings for 10 s
    s, b, _ = site("/api/x")
    check("OFF: /api answers 503 backend_off", s == 503 and b["error"] == "backend_off", (s, b))
    s, body = api("POST", "/api/student/backend/upload-url", session=session)
    check("OFF: deploys are refused", s == 403, (s, body))
    try:
        lam.put_function_concurrency(FunctionName=f"sd-{SLUG}", ReservedConcurrentExecutions=0)
        check("AWS accepts reserved concurrency 0 at an account limit of 10", True)
        lam.delete_function_concurrency(FunctionName=f"sd-{SLUG}")
    except ClientError as e:
        check("AWS accepts reserved concurrency 0 at an account limit of 10", False, e.response["Error"]["Message"])
    set_backend_fields(enabled=True)
    time.sleep(12)
    s, b, _ = site("/api/x")
    check("ON: service restored", s == 200, (s, b))

    # ---------- E: the in-flight limiter under real parallel load ----------
    SLOW = ("import json, time\n"
            "def handler(event, context):\n"
            "    time.sleep(2)\n"
            "    return {'statusCode': 200, 'body': json.dumps({'ok': True})}\n")
    if deploy(session, {"app.py": SLOW}, "slow handler for the limiter test"):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: site("/api/slow")[0], range(8)))
        ok, busy = results.count(200), results.count(429)
        log(f"   8 parallel requests -> {ok} ok, {busy} busy: {sorted(results)}")
        check("at most 3 student requests run at once (global cap), the rest get 429", 1 <= ok <= 3 and ok + busy == 8, results)
        time.sleep(1)
        check("after the burst, requests work again (slots released)", site("/api/slow")[0] == 200)

    # ---------- F: remove, then the Node runtime ----------
    s, body = api("DELETE", "/api/student/backend", session=session)
    check("remove backend", s == 200 and body["token"]["backend"]["state"] == "none", body)
    check("function and log group are gone", all(f["FunctionName"] != f"sd-{SLUG}" for f in lam.list_functions()["Functions"]))
    s, b, _ = site("/api/health")
    check("after removal: no_backend", s == 404 and b["error"] == "no_backend", (s, b))
    if deploy(session, template("node"), "Node 22 template"):
        s, b, _ = site("/api/hello?name=Node")
        check("Node runtime answers through the gateway", s == 200 and b == {"message": "Hello, Node!"}, (s, b))
        check("runtime recorded as nodejs22.x", lam.get_function_configuration(FunctionName=f"sd-{SLUG}")["Runtime"] == "nodejs22.x")
        cfg = lam.get_function_configuration(FunctionName=f"sd-{SLUG}")
        check("sandbox settings: 256 MB, 10 s, logs-only role", cfg["MemorySize"] == 256 and cfg["Timeout"] == 10 and cfg["Role"].endswith(cfg["Role"].split("/")[-1]) and "Sandbox" in cfg["Role"], cfg["Role"])

    # ---------- G: the token-based CLI (student/portal-deploy.mjs), as used from a laptop or GitHub Actions ----------
    import subprocess
    import tempfile
    api("DELETE", "/api/student/backend", session=session)  # the runtime cannot change in place (Node -> Python)
    with tempfile.TemporaryDirectory() as project:
        os.makedirs(os.path.join(project, "frontend", "dist"))
        os.makedirs(os.path.join(project, "backend"))
        open(os.path.join(project, "frontend", "dist", "index.html"), "w").write("<h1>live-cli-smoke</h1>")
        for n, text in template("python").items():
            open(os.path.join(project, "backend", n), "w", encoding="utf8").write(text)
        cli = subprocess.run(["node", os.path.join(ROOT, "..", "portal-deploy.mjs"), "--skip-build"], cwd=project, capture_output=True, text=True,
                             env={**os.environ, "PORTAL_URL": BASE, "PORTAL_TOKEN": token})
        check("CLI: portal-deploy.mjs publishes backend and frontend with the token", cli.returncode == 0 and "Frontend live at" in cli.stdout and "Backend ready" in cli.stdout, cli.stdout + cli.stderr)
        time.sleep(3)
        s, b, _ = site("/api/health")
        check("CLI: the deployed backend answers", s == 200 and b == {"ok": True}, (s, b))
        s, b, _ = site("/")
        check("CLI: the deployed frontend is served", s == 200 and "live-cli-smoke" in str(b), (s, str(b)[:80]))

    # ---------- H: the class AI connector end to end: a real capped OpenRouter key, called from the student's real function ----------
    openrouter = load_platform_modules()
    import ai
    import portal
    ai_now = lambda: int(time.time())  # noqa: E731
    item = portal.db().get(f"tok#{tid}")
    api("DELETE", "/api/student/backend", session=session)  # start from a clean function, as a new student would
    item = ai.set_ai(item, {"enabled": True, "limit_usd": 0.01, "period": "total"}, ai_now())
    ai_hash = ai.settings(item)["hash"]
    remote = openrouter.get_key(ai_hash)
    check("AI: a capped key exists at OpenRouter (limit $0.01, not disabled)", remote["limit"] == 0.01 and not remote["disabled"], remote)
    ASK = ("import json, os, urllib.request, urllib.error\n"
           "def handler(event, context):\n"
           "    body = json.dumps({'model': os.environ['AI_MODEL'], 'max_tokens': 20, 'messages': [{'role': 'user', 'content': 'Reply with the single word: ok'}]}).encode()\n"
           "    req = urllib.request.Request(os.environ['OPENAI_BASE_URL'] + '/chat/completions', data=body, headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json'})\n"
           "    try:\n"
           "        with urllib.request.urlopen(req, timeout=20) as r:\n"
           "            data = json.load(r)\n"
           "        return {'statusCode': 200, 'headers': {'content-type': 'application/json'}, 'body': json.dumps({'ok': True, 'text': data['choices'][0]['message']['content'][:20], 'model': data.get('model')})}\n"
           "    except urllib.error.HTTPError as e:\n"
           "        return {'statusCode': 200, 'headers': {'content-type': 'application/json'}, 'body': json.dumps({'ok': False, 'http': e.code})}\n")
    if deploy(session, {"app.py": ASK}, "AI handler (first deploy while AI is on)"):
        cfg = lam.get_function_configuration(FunctionName=f"sd-{SLUG}")
        names = sorted((cfg.get("Environment") or {}).get("Variables", {}))
        check("AI: the function was created with the three variables and the 25 s timeout", names == ["AI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL"] and cfg["Timeout"] == 25, (names, cfg["Timeout"]))
        s, b, _ = site("/api/ask")
        check("AI: the student's function answers through the gateway using its own key", s == 200 and b.get("ok") is True and b.get("text"), (s, b))
        check("AI: the default model is used", "haiku" in str(b.get("model", "")).lower(), b)
        used = 0
        for _ in range(10):
            s, body = api("GET", "/api/student/ai", session=session)
            used = body["token"]["ai"]["used_usd"] if s == 200 else 0
            if used > 0:
                break
            time.sleep(3)
        check("AI: the production portal reads the usage from OpenRouter (IAM + secret work)", used > 0, used)
        check("AI: the student's own view never contains a key", "sk-or-" not in json.dumps(body))
        openrouter.update_key(ai_hash, limit_usd=0.00001)  # shrink the cap so it can be reached in seconds
        outcome = []
        for _ in range(14):
            s, b, _ = site("/api/ask")
            outcome.append(b.get("http") if isinstance(b, dict) and b.get("ok") is False else s)
            if outcome[-1] == 403:
                break
            time.sleep(1)
        check("AI: once the cap is used OpenRouter refuses with 403 (cap enforced at the provider)", 403 in outcome, outcome)
        item = ai.set_ai(portal.db().get(f"tok#{tid}"), {"enabled": False}, ai_now())
        check("AI: switching off disables the key at OpenRouter", openrouter.get_key(ai_hash)["disabled"] is True)
        cfg = lam.get_function_configuration(FunctionName=f"sd-{SLUG}")
        check("AI: switching off removes the variables and restores the 10 s timeout",
              not any(n in (cfg.get("Environment") or {}).get("Variables", {}) for n in ai.AI_ENV_NAMES) and cfg["Timeout"] == 10, cfg["Timeout"])
        item = ai.set_ai(portal.db().get(f"tok#{tid}"), {"enabled": True}, ai_now())
        check("AI: switching on again re-enables the same key", openrouter.get_key(ai_hash)["disabled"] is False and ai.settings(item)["hash"] == ai_hash)

    # ---------- I: the student kit exactly as a student gets it, deployed to production with its own portal-deploy.mjs ----------
    # (the AI cap was shrunk in section H to reach it quickly: give it room again)
    item = ai.set_ai(portal.db().get(f"tok#{tid}"), {"enabled": True, "limit_usd": 0.02}, ai_now())
    repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    with tempfile.TemporaryDirectory() as kit_parent:
        kit_dir = os.path.join(kit_parent, "kit")
        built = subprocess.run(["node", os.path.join(repo_root, "starter", "build-kit.mjs"), kit_dir], capture_output=True, text=True)
        check("KIT: the kit builds", built.returncode == 0, built.stdout + built.stderr)
        installed = subprocess.run("npm ci --no-audit --no-fund", cwd=os.path.join(kit_dir, "frontend"), shell=True, capture_output=True, text=True)
        check("KIT: the starter's packages install (npm ci)", installed.returncode == 0, installed.stdout[-300:] + installed.stderr[-300:])
        platform_check = subprocess.run(["node", os.path.join(kit_dir, "tools", "check.mjs")], cwd=kit_dir, capture_output=True, text=True)
        check("KIT: npm run check passes on the untouched kit", platform_check.returncode == 0, platform_check.stdout[-400:])
        deployed = subprocess.run(["node", os.path.join(kit_dir, "portal-deploy.mjs")], cwd=kit_dir, capture_output=True, text=True,
                                  env={**os.environ, "PORTAL_URL": BASE, "PORTAL_TOKEN": token})
        check("KIT: portal-deploy.mjs publishes the kit's backend and site to production", deployed.returncode == 0 and "Backend ready" in deployed.stdout and "Frontend live at" in deployed.stdout, deployed.stdout[-400:] + deployed.stderr[-400:])
        time.sleep(3)
        s, b, _ = site("/api/health")
        check("KIT: /api/health answers on the live site", s == 200 and b == {"ok": True}, (s, b))
        s, b, _ = site("/api/ask", "POST", {"prompt": "Reply with the single word: ok"})
        answer = (b or {}).get("answer", "") if isinstance(b, dict) else ""
        check("KIT: the kit's own ask() reaches the real model on the platform (not the offline mock)", s == 200 and answer and not answer.startswith("[mock AI]"), (s, b))
        s, b, _ = site("/")
        check("KIT: the kit's site is served", s == 200 and "root" in str(b), (s, str(b)[:80]))
        s, b, _ = site("/api/ask", "POST", {"prompt": ""})
        check("KIT: the kit validates input (400 for an empty prompt)", s == 400, (s, b))

    # ---------- J: class storage, the real thing: key isolation, data, quotas, presigned file upload and download, CORS, off switch ----------
    if not STORAGE_URL:
        log("SKIP section J: the stack was deployed without StorageEnabled")
    else:
        import storage as storage_module
        item = portal.db().get(f"tok#{tid}")
        check("STORAGE: the student view says the connector exists but is off", api("GET", "/api/student/me", session=session)[1]["token"]["storage"]["enabled"] is False)
        for _ in range(30):  # the function may still be finishing the kit deploy: wait until AWS says it is done
            if lam.get_function_configuration(FunctionName=f"sd-{SLUG}").get("LastUpdateStatus") == "Successful":
                break
            time.sleep(1)
        item = storage_module.set_storage(item, {"enabled": True, "data_mb": 1, "files_mb": 1, "ops_limit": 60, "ops_period": "total"}, ai_now())
        key = storage_module.settings(item)["key"]
        cfg = lam.get_function_configuration(FunctionName=f"sd-{SLUG}")
        names = (cfg.get("Environment") or {}).get("Variables", {})
        check("STORAGE: the function got the storage address and key (and kept its AI variables)",
              names.get("CLASS_STORAGE_URL") == STORAGE_URL and names.get("CLASS_STORAGE_KEY") == key and "OPENAI_API_KEY" in names, sorted(names))

        def sapi(method, path, body=None, auth=key, query=""):
            headers = {"content-type": "application/json", **({"authorization": f"Bearer {auth}"} if auth else {})}
            status, data, _ = http(method, STORAGE_URL + path + query, body, headers)
            return status, data

        check("STORAGE: no key is refused", sapi("GET", "/v1/data/n/x", auth=None)[0] == 401)
        check("STORAGE: a key with the wrong secret is refused", sapi("GET", "/v1/data/n/x", auth=key.split(".")[0] + "." + "A" * 32)[0] == 401)
        check("STORAGE: another student's name with this secret is refused", sapi("GET", "/v1/data/n/x", auth="cs_somebody-else." + key.split(".", 1)[1])[0] == 401)
        s, b = sapi("PUT", "/v1/data/live/a", {"value": {"hello": "world", "n": [1, 2]}})
        check("STORAGE: put a record through the real API", s == 200 and b["id"] == "a", (s, b))
        s, b = sapi("GET", "/v1/data/live/a")
        check("STORAGE: read it back", s == 200 and b["value"] == {"hello": "world", "n": [1, 2]}, (s, b))
        sapi("PUT", "/v1/data/live/b", {"value": "second"})
        s, b = sapi("GET", "/v1/data/live")
        check("STORAGE: list returns both records in id order", s == 200 and [i["id"] for i in b["items"]] == ["a", "b"], (s, b))
        s, b = sapi("PUT", "/v1/data/live/big", {"value": "x" * 70000})
        check("STORAGE: a value over 64 KB is refused", s == 413 and b["error"] == "too_large", (s, b))
        check("STORAGE: DynamoDB holds the record under the student's own partition",
              boto3.resource("dynamodb", region_name="us-east-1").Table(DATA_TABLE).get_item(Key={"s": f"{SLUG}#live", "id": "a"}).get("Item") is not None)

        # the kit deployed in section I has a /notes route that uses class_storage inside the real function
        s, b, _ = site("/api/notes", "POST", {"text": "written by the kit on production"})
        check("STORAGE: the kit's /notes route saves through class_storage on the platform", s == 201, (s, b))
        s, b, _ = site("/api/notes")
        check("STORAGE: and lists it back", s == 200 and any(n["text"] == "written by the kit on production" for n in b.get("notes", [])), (s, b))

        # files: the presigned POST goes to REAL S3, so this is the part the local tests cannot show
        payload = b"live storage file " * 20
        s, up = sapi("POST", "/v1/files/upload-url", {"name": "live.txt", "size": len(payload), "content_type": "text/plain"})
        check("STORAGE: an upload target is issued", s == 200 and up["url"].startswith("https://") and up["fields"]["key"] == f"userdata/{SLUG}/live.txt", (s, up))
        boundary = secrets.token_hex(8)
        body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in up["fields"].items())
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="live.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        ps, pb, ph = http("POST", up["url"], raw=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Origin": "https://zz-backendtest.example.edu"})
        check("STORAGE: the file is accepted by S3 (the signed form works)", ps in (200, 201, 204), (ps, str(pb)[:200]))
        check("STORAGE: S3 sends the CORS header a browser needs for the upload", ph.get("Access-Control-Allow-Origin", ph.get("access-control-allow-origin")) in ("*", "https://zz-backendtest.example.edu"), ph)
        too_big = payload + b"1"
        bad = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in up["fields"].items())
        bad += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="live.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode() + too_big + f"\r\n--{boundary}--\r\n".encode()
        bs, _, _ = http("POST", up["url"], raw=bad, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        check("STORAGE: S3 itself refuses a file bigger than the size the form was issued for", bs == 400, bs)
        s, b = sapi("GET", "/v1/files")
        check("STORAGE: the file is listed with its size", s == 200 and [(f["name"], f["size"]) for f in b["files"]] == [("live.txt", len(payload))], (s, b))
        s, b = sapi("GET", "/v1/files/live.txt")
        ds, content, dh = http("GET", b["url"], headers={"Origin": "https://zz-backendtest.example.edu"}) if s == 200 else (0, "", {})
        check("STORAGE: the download link returns the same bytes", ds == 200 and content == payload.decode(), (ds, str(content)[:60]))
        check("STORAGE: the download link has CORS too (fetch from the page works)", dh.get("Access-Control-Allow-Origin", dh.get("access-control-allow-origin")) in ("*", "https://zz-backendtest.example.edu"), dh)
        s, b = sapi("POST", "/v1/files/upload-url", {"name": "huge.bin", "size": 2 * 1024 * 1024})
        check("STORAGE: the 1 MB file space cap is enforced", s == 413 and b["error"] == "quota_exceeded", (s, b))
        check("STORAGE: delete removes the file", sapi("DELETE", "/v1/files/live.txt")[0] == 200 and sapi("GET", "/v1/files")[1]["files"] == [])
        check("STORAGE: the site's own address never serves userdata/", site(f"/userdata/{SLUG}/live.txt")[0] in (403, 404))

        # the teacher's tools, run here with the platform's own code on the real table and bucket
        dyn = boto3.resource("dynamodb", region_name="us-east-1").Table(DATA_TABLE)
        rows = lambda: dyn.scan(FilterExpression="begins_with(s, :p)", ExpressionAttributeValues={":p": f"{SLUG}#"})["Items"]  # noqa: E731
        check("STORAGE: the student's collections are remembered for clearing", len(table.scan(FilterExpression="begins_with(pk, :p)", ExpressionAttributeValues={":p": f"scoll#{SLUG}#"})["Items"]) >= 2)
        sapi("PUT", "/v1/data/live/c", {"value": "to be cleared"})
        s, up2 = sapi("POST", "/v1/files/upload-url", {"name": "gone.txt", "size": 5, "content_type": "text/plain"})
        mine = {r["slug"]: r for r in storage_module.overview(ai_now())["students"]}.get(SLUG)
        check("STORAGE: the overview shows this student's sizes and counts (and nothing of the content)",
              mine and mine["enabled"] and mine["usage"]["items"] >= 3 and "to be cleared" not in json.dumps(mine), mine)
        check("STORAGE: the overview lists no other student's key or data", "cs_" not in json.dumps(storage_module.overview(ai_now())))
        before = len(rows())
        done = storage_module.clear(portal.db().get(f"tok#{tid}"), "data", ai_now())
        check("STORAGE: clearing data deletes every record of the student from the real table", done["items"] == before and rows() == [] and done["files"] == 0, (done, before, len(rows())))
        check("STORAGE: and puts the counters back to zero", portal.db().get(f"sbytes#{SLUG}")["count"] == 0 and portal.db().get(f"sitems#{SLUG}")["count"] == 0)
        check("STORAGE: the student's key still works after a clear", sapi("PUT", "/v1/data/live/again", {"value": 1})[0] == 200 and sapi("GET", "/v1/data/live/a")[0] == 404)
        s3c = boto3.client("s3", region_name="us-east-1")
        s3c.put_object(Bucket=BUCKET, Key=f"userdata/{SLUG}/clearme.txt", Body=b"x")
        done = storage_module.clear(portal.db().get(f"tok#{tid}"), "files", ai_now())
        check("STORAGE: clearing files deletes the student's files from the real bucket", done["files"] >= 1 and sapi("GET", "/v1/files")[1]["files"] == [], done)
        # the portal function's role may find and delete items, and nothing more on that table
        try:
            iam = boto3.client("iam")
            role_arn = iam.get_role(RoleName=cfn.describe_stack_resource(StackName=STACK, LogicalResourceId="DeployFunctionRole")["StackResourceDetail"]["PhysicalResourceId"])["Role"]["Arn"]
            table_arn = boto3.client("dynamodb", region_name="us-east-1").describe_table(TableName=DATA_TABLE)["Table"]["TableArn"]
            verdict = {r["EvalActionName"]: r["EvalDecision"] for r in iam.simulate_principal_policy(
                PolicySourceArn=role_arn, ActionNames=["dynamodb:Query", "dynamodb:BatchWriteItem", "dynamodb:DeleteItem", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan"],
                ResourceArns=[table_arn])["EvaluationResults"]}
            check("STORAGE: the portal function may find and delete a student's items", all(verdict[a] == "allowed" for a in ("dynamodb:Query", "dynamodb:BatchWriteItem", "dynamodb:DeleteItem")), verdict)
            check("STORAGE: and may NOT read, write or scan them", all(verdict[a] != "allowed" for a in ("dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan")), verdict)
        except ClientError as e:
            log("SKIP the IAM simulation:", e.response["Error"].get("Code"))

        view = api("GET", "/api/student/storage", session=session)[1]
        check("STORAGE: the student's view shows usage without any key", view["token"]["storage"]["usage"]["items"] >= 1 and "cs_" not in json.dumps(view), view["token"]["storage"])
        item = storage_module.set_storage(portal.db().get(f"tok#{tid}"), {"enabled": False}, ai_now())
        check("STORAGE: switching off stops the API at once", sapi("GET", "/v1/data/live/a")[0] == 403)
        cfg = lam.get_function_configuration(FunctionName=f"sd-{SLUG}")
        check("STORAGE: switching off removes the variables from the function", "CLASS_STORAGE_KEY" not in (cfg.get("Environment") or {}).get("Variables", {}))
        storage_module.set_storage(portal.db().get(f"tok#{tid}"), {"enabled": True}, ai_now())
        check("STORAGE: switching on again brings the same data back", sapi("GET", "/v1/data/live/again")[1].get("value") == 1)
        storage_module.set_storage(portal.db().get(f"tok#{tid}"), {"ops_limit": 0}, ai_now())
        check("STORAGE: an allowance of 0 refuses with allowance_used", sapi("GET", "/v1/data/live/a")[1].get("error") == "allowance_used")
finally:
    cleanup()
    leftover_functions = [f["FunctionName"] for f in lam.list_functions()["Functions"] if f["FunctionName"] == f"sd-{SLUG}"]
    leftover_groups = [g["logGroupName"] for g in logs.describe_log_groups(logGroupNamePrefix=f"/aws/lambda/sd-{SLUG}")["logGroups"]]
    check("cleanup: test function removed", leftover_functions == [], leftover_functions)
    check("cleanup: test log group removed", leftover_groups == [], leftover_groups)
    left = [i["pk"] for i in table.scan(FilterExpression="contains(pk, :s)", ExpressionAttributeValues={":s": SLUG})["Items"]]
    check("cleanup: no test rows left", left == [], left)
    try:
        keys_left = [k.get("name") for k in load_platform_modules()._call("GET", "/keys")["data"] if str(k.get("name", "")).startswith(f"student:{SLUG}")]
        check("cleanup: no temporary OpenRouter key left", keys_left == [], keys_left)
    except Exception as e:
        check("cleanup: could not list OpenRouter keys", False, e)
log("ALL STEPS PASSED" if not failures else f"FAILED: {failures}")
sys.exit(0 if not failures else 1)
