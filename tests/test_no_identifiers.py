"""No personal identifiers in tracked files.

Always checked (shapes that never belong in the repository):
  - an ARN with a real 12-digit account id (only <ACCOUNT_ID> is allowed)
  - an execute-api host with a real api id, a *.cloudfront.net host with a real distribution id
Also checked when the git-ignored identifiers.local exists (the maintainer's machine): its domain and every literal in it.
Files listed in tools/private-files.json exist only in the private copy and are skipped (the public repository is created without them).
"""
import fnmatch
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

GENERIC = [
    (re.compile(r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):"), "ARN with a 12-digit account id"),
    (re.compile(r"\b[a-z0-9]{10}\.execute-api\."), "execute-api host with a real api id"),
    (re.compile(r"\bd[a-z0-9]{12,13}\.cloudfront\.net\b"), "cloudfront.net host with a real distribution id"),
]


def private_patterns():
    with open(os.path.join(ROOT, "tools", "private-files.json"), encoding="utf8") as f:
        return json.load(f)["paths"]


def is_private(path, patterns):
    for p in patterns:
        if p.endswith("/") and path.startswith(p):
            return True
        if not p.endswith("/") and fnmatch.fnmatch(path, p):
            return True
    return False


def local_denylist():
    path = os.path.join(ROOT, "identifiers.local")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf8") as f:
        data = json.load(f)
    values = list(data.get("literals", {}).keys())
    if data.get("domain"):
        values.append(data["domain"])
    return values


def main():
    patterns = private_patterns()
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode("utf8").split("\0")
    deny = [v.lower() for v in local_denylist()]
    problems = []
    for rel in filter(None, files):
        if is_private(rel, patterns):
            continue
        with open(os.path.join(ROOT, rel), "rb") as f:
            raw = f.read()
        if b"\0" in raw:
            continue
        text = raw.decode("utf8", "replace")
        lower = text.lower()
        for value in deny:
            if value in lower:
                problems.append(f"{rel}: contains a deny-listed identifier")
        for regex, what in GENERIC:
            if regex.search(text):
                problems.append(f"{rel}: {what}")
    if problems:
        print("Personal identifiers found (run: node tools/scrub-identifiers.mjs --apply):")
        for line in sorted(set(problems)):
            print("  " + line)
        sys.exit(1)
    print(f"no identifiers: ok ({'local deny-list + ' if deny else ''}generic shapes)")


if __name__ == "__main__":
    main()
