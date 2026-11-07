"""The compute worker's runner. It runs as root on the worker machine every time the machine starts (docs/COMPUTE_WORKER_PLAN.md).

It is published to s3://<bucket>/worker/runner.py by `portal-publish` (so `apply` keeps it current) and downloaded by the machine's boot script, which then uploads
what this prints as the report and shuts the machine down. Only platform-owned code lives here. It never runs student code.

Step 1, once per SETUP_VERSION: install the software the tasks need (git, Node.js with npm, Python 3.12 with pip, jq, compilers). A marker file keeps the
machine from reinstalling on every start. Each package is tried on its own, so one that is missing from the repository is reported, not fatal.
Step 2: print what is installed (this is the report the teacher sees).
Tasks (daily quality probes, judging flagged answers) are added here later, one function each, chosen by worker/control/tasks.json.
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import time

SETUP_VERSION = 1
MARKER = f"/opt/worker/setup-v{SETUP_VERSION}.done"
# (label, [packages to try in this order: the first that installs wins])
PACKAGES = [
    ("git", ["git"]),
    ("jq, unzip, tar", ["jq", "unzip", "tar"]),
    ("compilers", ["gcc", "gcc-c++", "make"]),
    ("Node.js", ["nodejs22", "nodejs20"]),
    ("npm", ["nodejs22-npm", "nodejs20-npm"]),
    ("Python 3.12", ["python3.12", "python3.11"]),
    ("pip", ["python3.12-pip", "python3.11-pip"]),
    ("Python headers", ["python3.12-devel", "python3.11-devel"]),
]
TOOLS = ("git", "node", "npm", "python3.12", "python3.11", "pip3.12", "pip3.11", "jq", "gcc", "aws")


def run(command, timeout=900):
    """(exit code, last lines of output). Never raises."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return done.returncode, (done.stdout + done.stderr).strip().splitlines()[-3:]
    except Exception as error:  # a missing program or a timeout is a result, not a crash
        return 1, [f"{type(error).__name__}: {error}"]


def install_command(packages):
    return ["dnf", "install", "-y", "--setopt=install_weak_deps=False", *packages]


def setup(runner=run, marker=MARKER, installed=lambda name: shutil.which(name)):
    """Install the packages once. Returns the lines to report. `runner` is injectable for tests."""
    if os.path.exists(marker):
        return ["setup: already done"]
    lines = []
    for label, choices in PACKAGES:
        for package in choices:
            code, tail = runner(install_command([package]))
            if code == 0:
                lines.append(f"setup: {label}: installed {package}")
                break
            lines.append(f"setup: {label}: {package} failed ({tail[-1] if tail else code})")
        else:
            lines.append(f"setup: {label}: NOT installed")
    ok = not any("NOT installed" in line for line in lines)
    if ok:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf8") as f:
            f.write(json.dumps({"version": SETUP_VERSION, "at": int(time.time())}))
    return lines + ([] if ok else ["setup: some packages are missing, it will try again on the next start"])


def versions(runner=run):
    out = []
    for tool in TOOLS:
        path = shutil.which(tool)
        if not path:
            continue
        code, tail = runner([tool, "--version"], 30)
        out.append(f"{tool}: {tail[0] if code == 0 and tail else path}")
    return out


def main():
    print("compute worker runner, setup version", SETUP_VERSION)
    print("python", platform.python_version(), platform.machine(), "| cpus", os.cpu_count(), "| disk free GB", round(shutil.disk_usage("/").free / 1e9, 1))
    for line in setup():
        print(line)
    for line in versions():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
