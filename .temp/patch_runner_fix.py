root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


edit("worker/runner.py", "SETUP_VERSION = 1", "SETUP_VERSION = 2  # 2: a group of packages is installed together (version 1 took only the first of 'jq, unzip, tar' and of the compilers)")
edit("worker/runner.py", '''# (label, [packages to try in this order: the first that installs wins])
PACKAGES = [
    ("git", ["git"]),
    ("jq, unzip, tar", ["jq", "unzip", "tar"]),
    ("compilers", ["gcc", "gcc-c++", "make"]),
    ("Node.js", ["nodejs22", "nodejs20"]),
    ("npm", ["nodejs22-npm", "nodejs20-npm"]),
    ("Python 3.12", ["python3.12", "python3.11"]),
    ("pip", ["python3.12-pip", "python3.11-pip"]),
    ("Python headers", ["python3.12-devel", "python3.11-devel"]),
]''', '''# (label, [choices in this order: the first choice that installs wins]); a choice is a list of packages installed together
PACKAGES = [
    ("git", [["git"]]),
    ("jq, unzip, tar", [["jq", "unzip", "tar"]]),
    ("compilers", [["gcc", "gcc-c++", "make"]]),
    ("Node.js", [["nodejs22"], ["nodejs20"]]),
    ("npm", [["nodejs22-npm"], ["nodejs20-npm"]]),
    ("Python 3.12", [["python3.12"], ["python3.11"]]),
    ("pip", [["python3.12-pip"], ["python3.11-pip"]]),
    ("Python headers", [["python3.12-devel"], ["python3.11-devel"]]),
]''')
edit("worker/runner.py", '''        for package in choices:
            code, tail = runner(install_command([package]))
            if code == 0:
                lines.append(f"setup: {label}: installed {package}")
                break
            lines.append(f"setup: {label}: {package} failed ({tail[-1] if tail else code})")''', '''        for packages in choices:
            code, tail = runner(install_command(packages))
            name = " ".join(packages)
            if code == 0:
                lines.append(f"setup: {label}: installed {name}")
                break
            lines.append(f"setup: {label}: {name} failed ({tail[-1] if tail else code})")''')
edit("worker/runner.py", 'TOOLS = ("git", "node", "npm", "python3.12", "python3.11", "pip3.12", "pip3.11", "jq", "gcc", "aws")', 'TOOLS = ("git", "node", "npm", "python3.12", "python3.11", "pip3.12", "pip3.11", "jq", "unzip", "tar", "gcc", "make", "aws")')
# tests follow the new shape
edit("tests/test_worker_runner.py", '''        package = command[-1]
        return (1, [f"No match for argument: {package}"]) if package in missing else (0, ["Complete!"])''', '''        package = command[-1]
        return (1, [f"No match for argument: {package}"]) if any(p in missing for p in command[5:]) else (0, ["Complete!"])''')
edit("tests/test_worker_runner.py", '''    assert all(c[:3] == ["dnf", "install", "-y"] for c in calls) and ["dnf", "install", "-y", "--setopt=install_weak_deps=False", "git"] in calls''', '''    assert all(c[:3] == ["dnf", "install", "-y"] for c in calls) and ["dnf", "install", "-y", "--setopt=install_weak_deps=False", "git"] in calls
    assert ["dnf", "install", "-y", "--setopt=install_weak_deps=False", "jq", "unzip", "tar"] in calls and ["dnf", "install", "-y", "--setopt=install_weak_deps=False", "gcc", "gcc-c++", "make"] in calls, "a group is installed together"''')
print("ok")
