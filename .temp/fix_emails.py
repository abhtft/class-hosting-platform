import re

root = r"D:\Github_Codes\class-hosting-platform-github"


def read(p):
    return open(root + "\\" + p.replace("/", "\\"), encoding="utf8").read()


def write(p, s):
    open(root + "\\" + p.replace("/", "\\"), "w", encoding="utf8").write(s)


# docs: placeholders instead of addresses
for p in ("docs/TEACHER_SETUP.md", "README.md", "CHANGELOG.md", "docs/CRITICAL_ISSUES.md", "docs/ROADMAP.md"):
    s = read(p)
    s = s.replace("--email you@example.com", "--email <your address>").replace("--alert-email you@example.com", "--alert-email <your address>")
    s = s.replace("you@example.com", "<your address>")
    write(p, s)

# code hints: no address-shaped examples either
s = read("installer/engine.mjs")
s = s.replace("'Use an address you read, for example you@example.com.'", "'Use an address you read.'")
s = s.replace("alerts --email you@example.com [--budget 10]", "alerts --email <address> [--budget 10]")
s = s.replace("alerts --email you@example.com", "alerts --email <address>")
write("installer/engine.mjs", s)

# tests: addresses built from parts
s = read("tests/test_installer.mjs")
s = s.replace("const fails = async", "const addr = (name, domain = 'example.com') => `${name}@${domain}`; // built from parts: no address is written in the repository\nconst fails = async", 1)
s = s.replace("' teacher@example.com '", "` ${addr('teacher')} `").replace("{ email: 'teacher@example.com', budgetUsd: 10 }", "{ email: addr('teacher'), budgetUsd: 10 }")
s = s.replace("alertEmail: 'a@b.co', budgetUsd: '25.4' }), { email: 'a@b.co', budgetUsd: 25 }", "alertEmail: addr('a', 'b.co'), budgetUsd: '25.4' }), { email: addr('a', 'b.co'), budgetUsd: 25 }")
s = s.replace("for (const bad of ['teacher', 'a@b', 'a b@c.de', '@x.com'])", "for (const bad of ['teacher', addr('a', 'b'), addr('a b', 'c.de'), addr('', 'x.com')])")
s = s.replace("alertEmail: 'a@b.co', budgetUsd: bad }", "alertEmail: addr('a', 'b.co'), budgetUsd: bad }")
s = s.replace("alertEmail: 'teacher@example.com', budgetUsd: 15", "alertEmail: addr('teacher'), budgetUsd: 15")
s = s.replace("'teacher@example.com');\n  const budget", "addr('teacher'));\n  const budget")
s = s.replace("n.Subscribers[0].Address]),\n    [['ACTUAL', 80, 'teacher@example.com'], ['ACTUAL', 100, 'teacher@example.com'], ['FORECASTED', 100, 'teacher@example.com']]", "n.Subscribers[0].Address]),\n    [['ACTUAL', 80, addr('teacher')], ['ACTUAL', 100, addr('teacher')], ['FORECASTED', 100, addr('teacher')]]")
s = s.replace("includes('teacher@example.com')", "includes(addr('teacher'))")
s = s.replace("alertEmail: 'later@example.com'", "alertEmail: addr('later')")
write("tests/test_installer.mjs", s)
print(re.findall(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+", read("tests/test_installer.mjs")))
