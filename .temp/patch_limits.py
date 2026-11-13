import json
import os
import re

root = r"D:\Github_Codes\class-hosting-platform-github"
os.chdir(root)

# ---- 1. the registry
p = "config/settings.json"
d = json.load(open(p, encoding="utf8"))
by = {s["key"]: s for s in d["settings"]}
by["backend.timeoutSeconds"].update(default=27)
by["backend.memoryMB"].update(default=3008, max=3008)
by["backend.maxZipMB"].update(default=50, max=50)
by["backend.maxUnzippedMB"].update(default=250, max=250)
by["ai.studentTimeoutSeconds"].update(default=27)
by["backend.timeoutSeconds"]["help"] = ("How long one backend request may run before it is stopped. AWS's HTTP front door ends every request at 30 seconds; the platform keeps 2 seconds "
                                        "of margin for the hop through its gateway, so 27 is the highest safe value.")
by["backend.maxZipMB"]["help"] = "The biggest zip a student may publish as their backend. AWS allows at most 50 MB zipped (and 250 MB unzipped) for a function."
by["backend.memoryMB"]["help"] = ("The memory each student backend function is given when it is published (applies to backends published after the change). AWS accounts "
                                  "start with a cap of 3,008 MB per function (this account was checked); a quota increase raises it.")
open(p, "w", encoding="utf8").write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")


def sub(path, old, new, count=1):
    s = open(path, encoding="utf8").read()
    assert old in s, (path, old[:60])
    open(path, "w", encoding="utf8").write(s.replace(old, new, count))


# ---- 2. the template (AI wait time) and the rules / tools the students see
sub("template.yaml", "AI_STUDENT_TIMEOUT: '25' # AI answers take seconds; the gateway below waits up to 29 (the HTTP API's own limit is 30)",
    "AI_STUDENT_TIMEOUT: '27' # a student's function may run this long; the gateway below waits up to 29 (the HTTP API's own limit is 30), so 27 keeps a safe margin")
sub("starter/rules/AGENTS.md", "10 seconds per request (25 s while the class AI is on; anything longer is a **background job**, see below), 256 MB, request and response at most 1 MB, the backend folder at most 500 files and 5 MB zipped.",
    "27 seconds per request (the same while the class AI is on; anything longer is a **background job**, see below), 3008 MB of memory, request and response at most 1 MB, the backend folder at most 500 files and 50 MB zipped (250 MB unzipped).")
sub("starter/rules/AGENTS.md", "A web request stops after 10 seconds (25 with the class AI).", "A web request stops after 27 seconds.")
sub("starter/rules/AGENTS.md", "Use a timeout of 8 seconds or less per call: the total request limit is 10 s (25 s with the class AI).", "Use a timeout of 15 seconds or less per call: the total request limit is 27 s.")
sub("starter/template/README.md", "10 seconds per request, no SQL database", "27 seconds per request, no SQL database")
sub("starter/template/backend/app.py", "at most 10 s per request (25 s while the", "at most 27 s per request (27 s while the")
sub("starter/template/tools/dev_server.mjs", "if (Date.now() - started > 10000) console.log('  (!) this request took more than 10 s: the platform would stop it');",
    "if (Date.now() - started > 27000) console.log('  (!) this request took more than 27 s: the platform would stop it');")
sub("starter/template/tools/dev_server.py", "this request took more than 10 s: the platform would stop it", "this request took more than 27 s: the platform would stop it")
sub("starter/template/tools/check.mjs", "const LIMITS = { files: 500, unzippedBytes: 50 * 1024 * 1024, zippedBytes: 5 * 1024 * 1024 };", "const LIMITS = { files: 500, unzippedBytes: 250 * 1024 * 1024, zippedBytes: 50 * 1024 * 1024 };")
sub("starter/template/tools/dev_server.py", "if time.time() - started > 10:", "if time.time() - started > 27:")
print("ok")
