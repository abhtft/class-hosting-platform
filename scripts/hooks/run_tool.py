"""Run a developer tool for a pre-commit hook, finding it even when the shell's PATH is old.

Usage:  python scripts/hooks/run_tool.py <tool> [arguments...]

Why this exists: the tools (gitleaks, cfn-lint, checkov) are installed per user in `%USERPROFILE%\\tools\\bin` (see docs/RUNBOOK.md). A terminal or editor opened before they
were installed does not have that folder on its PATH, and a hook that cannot find its tool would block every commit with a confusing message. This script looks on the PATH first,
then in that folder, and otherwise stops with a plain instruction. It never skips a check silently.
"""
import pathlib
import shutil
import subprocess
import sys

FALLBACK_DIRS = (pathlib.Path.home() / "tools" / "bin",)


def find_tool(name: str) -> str | None:
    """The full path of the tool, or None."""
    found = shutil.which(name)
    if found:
        return found
    for folder in FALLBACK_DIRS:
        found = shutil.which(name, path=str(folder))
        if found:
            return found
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: run_tool.py <tool> [arguments...]", file=sys.stderr)
        return 2
    tool = find_tool(argv[1])
    if tool is None:
        print(f"pre-commit: '{argv[1]}' was not found on the PATH or in {FALLBACK_DIRS[0]}. Install it (see docs/RUNBOOK.md, 'Tools on the development machine') "
              "and commit again. The check was NOT skipped.", file=sys.stderr)
        return 1
    return subprocess.run([tool, *argv[2:]], check=False).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
