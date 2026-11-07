"""The compute worker's runner (worker/runner.py): it installs the needed software once (a marker file stops a second install), tries a fallback package when the
first is missing, reports a package that cannot be installed without stopping, and never raises. The package manager is a fake."""
import os
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "worker"))
import runner  # noqa: E402

calls = []


def fake_run(missing=()):
    def run(command, timeout=900):
        calls.append(command)
        package = command[-1]
        return (1, [f"No match for argument: {package}"]) if package in missing else (0, ["Complete!"])
    return run


with tempfile.TemporaryDirectory() as tmp:
    marker = os.path.join(tmp, "work", "setup.done")

    # everything installs: one call per package, the first choice wins, the marker is written
    out = runner.setup(fake_run(), marker)
    assert os.path.exists(marker) and not [line for line in out if "NOT installed" in line], out
    assert all(c[:3] == ["dnf", "install", "-y"] for c in calls) and ["dnf", "install", "-y", "--setopt=install_weak_deps=False", "git"] in calls
    assert any("Node.js: installed nodejs22" in line for line in out) and any("Python 3.12: installed python3.12" in line for line in out), out
    assert not any(c[-1] == "nodejs20" for c in calls), "the fallback is not tried when the first choice works"
    first_calls = len(calls)

    # a second start installs nothing
    assert runner.setup(fake_run(), marker) == ["setup: already done"] and len(calls) == first_calls
    os.remove(marker)

    # a missing first choice falls back to the next one
    calls.clear()
    out = runner.setup(fake_run(missing={"nodejs22", "nodejs22-npm"}), marker)
    assert any("Node.js: nodejs22 failed" in line for line in out) and any("Node.js: installed nodejs20" in line for line in out), out
    assert os.path.exists(marker)
    os.remove(marker)

    # a package that cannot be installed at all is reported, the marker is NOT written, and the next start tries again
    out = runner.setup(fake_run(missing={"nodejs22", "nodejs20", "nodejs22-npm", "nodejs20-npm"}), marker)
    assert any("Node.js: NOT installed" in line for line in out) and any("try again" in line for line in out) and not os.path.exists(marker), out

# running a program that does not exist is a result, not a crash
code, tail = runner.run(["definitely-not-a-program-xyz"])
assert code == 1 and tail and "Error" in tail[0], (code, tail)
# the version lines only list tools that exist
assert all(": " in line for line in runner.versions(lambda cmd, timeout=30: (0, ["v1"])))
assert runner.install_command(["a", "b"])[-2:] == ["a", "b"]
print("ok")
