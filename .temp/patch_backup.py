import re

root = r"D:\Github_Codes\class-hosting-platform-github"


def read(p):
    return open(root + "\\" + p.replace("/", "\\"), encoding="utf8").read()


def write(p, s):
    open(root + "\\" + p.replace("/", "\\"), "w", encoding="utf8").write(s)


def sub(p, a, b):
    s = read(p)
    assert a in s, (p, a[:70])
    write(p, s.replace(a, b, 1))


D = "installer/data.mjs"
sub(D, "const FOLDERS = ['sites', 'archive', 'userdata']; // the bucket's prefixes that hold a class's files (portal/ is republished, uploads/ is temporary)",
    "const FOLDERS = ['sites', 'archive', 'userdata', 'knowledge']; // the bucket's prefixes that hold a class's files and books (portal/ is republished, uploads/ is temporary, worker/ is rebuilt)")
sub(D, "// What is NOT in a backup, on purpose: the AI keys in Secrets Manager",
    "// Class knowledge (every student's books, the class library and their search indexes under knowledge/) is in the backup: it is plain files, so a restore brings the\n// books back exactly. Traces are NOT: they expire by themselves after 30 to 90 days and are meant to be downloaded from the teacher page when they are worth keeping.\n//\n// What is NOT in a backup, on purpose: the AI keys in Secrets Manager")
sub(D, "counts: { tokens: tokens.length, storedItems: stored.length }, hasStudentData: Boolean(o.StudentDataTableName) };",
    "counts: { tokens: tokens.length, storedItems: stored.length }, hasStudentData: Boolean(o.StudentDataTableName), folders: FOLDERS };")
sub(D, "Backed up ${tokens.length} token records, ${stored.length} stored student items, the published files and the teacher login to ${folder}.",
    "Backed up ${tokens.length} token records, ${stored.length} stored student items, the published files, the class knowledge (books and the class library) and the teacher login to ${folder}. Traces are not included (download them from the Traces tab if you want to keep them).")
sub(D, "  return { ok: true, counts: { tokens: items.length, storedItems: stored.length }, domains,\n    message: `Imported ${items.length} token records and ${stored.length} stored student items, the published files and the teacher login into ${ctx.stack}.",
    "  const booksNote = manifest.features?.includes('KnowledgeEnabled') && found.parameters.KnowledgeEnabled !== 'true' ? ' The backup holds class knowledge (books), but it is not switched on in this platform: run plan --enable knowledge, then apply, and the books are used again.' : '';\n  return { ok: true, counts: { tokens: items.length, storedItems: stored.length }, domains,\n    message: `Imported ${items.length} token records and ${stored.length} stored student items, the published files, the class knowledge and the teacher login into ${ctx.stack}.${booksNote}")

# template: point-in-time recovery for the student data table
sub("template.yaml", """      KeySchema:
        - AttributeName: s
          KeyType: HASH
        - AttributeName: id
          KeyType: RANGE

  # Traces (traces.py)""", """      KeySchema:
        - AttributeName: s
          KeyType: HASH
        - AttributeName: id
          KeyType: RANGE
      PointInTimeRecoverySpecification: # students' stored data can be restored to any second of the last 35 days (the tokens table has it too)
        PointInTimeRecoveryEnabled: true

  # Traces (traces.py)""")

# tests
T = "tests/test_data.mjs"
sub(T, "'s3/userdata/index.html']) assert.ok(existsSync(join(backup, f)), f);", "'s3/userdata/index.html', 's3/knowledge/index.html']) assert.ok(existsSync(join(backup, f)), f);")
sub(T, "  assert.match(result.message, /keep it private/);", "  assert.match(result.message, /keep it private/);\n  assert.match(result.message, /class knowledge \\(books and the class library\\)/, 'the books are in the backup');\n  assert.match(result.message, /Traces are not included/, 'traces are said to be left out');\n  assert.ok(manifest.folders.includes('knowledge'));")
tmpl = read("tests/test_template_storage.py")
tmpl = tmpl.replace('table = block("StudentDataTable")', 'table = block("StudentDataTable")\nassert "PointInTimeRecoveryEnabled: true" in table, "students\' stored data can be restored to a point in time"', 1)
write("tests/test_template_storage.py", tmpl)

# docs
sub("docs/TEACHER_ARCHITECTURE.md", "| S3 `sites/`, `archive/`, `userdata/` | published sites, archived sites, student files | yes (`portal/` is republished; `uploads/` is temporary) |",
    "| S3 `sites/`, `archive/`, `userdata/`, `knowledge/` | published sites, archived sites, student files, class knowledge (books, the class library and their indexes) | yes (`portal/` is republished; `uploads/` is temporary) |\n| Traces | step records of answers | no: they expire after 30 to 90 days; download an archive from the Traces tab to keep them |")
s = read("docs/TECHNICAL_SPECIFICATION.md")
s = s.replace("the files under `sites/`, `archive/`, `userdata/`, and the two SSM parameters", "the files under `sites/`, `archive/`, `userdata/` and `knowledge/` (books and the class library), and the two SSM parameters", 1)
write("docs/TECHNICAL_SPECIFICATION.md", s)
s = read("docs/TEACHER_SETUP.md")
a = s.index("**What a backup (`export`) contains, and what it does not.**")
b = s.index("\n", a)
s = s[:a] + "**What a backup (`export`) contains, and what it does not.** It contains tokens and settings, the teacher login, stored student data, the published sites and files, and **class knowledge** (every student's books, the class library and their search indexes). It does **not** contain traces (they expire after 30 to 90 days: download an archive from the Traces tab to keep them), the AI keys (enter them again with `secrets`), student backend functions (students publish again) or custom domains. Students' stored data can also be restored to any point in the last 35 days (point-in-time recovery)." + s[b:]
write("docs/TEACHER_SETUP.md", s)
s = read("README.md")
s = s.replace("**A backup does not yet include class knowledge (books) or traces.**", "A backup includes books but not traces (download those from the Traces tab).")
write("README.md", s)
s = read("docs/CRITICAL_ISSUES.md")
s = s.replace("### 4. Backup and restore leave out books and traces\n", "### 4. Backup and restore leave out books and traces (fixed in code, 2.3.2 unreleased)\n\n> **Status:** `export` and `import` now include `knowledge/`; point-in-time recovery is on for the student-data table (needs an `apply`); traces are documented as expendable. Not yet tested on a real account. The text below describes the problem as found.\n", 1)
write("docs/CRITICAL_ISSUES.md", s)
s = read("CHANGELOG.md")
s = s.replace("**To apply:** `alerts --email <your address>`, then `plan` and `apply <plan id>`; nothing changes for students.\n", "**To apply:** `alerts --email <your address>`, then `plan` and `apply <plan id>`; nothing changes for students.\n\n- **Backups now include class knowledge.** `export` and `import` (and `transfer`) copy the `knowledge/` folder: every student's books, the class library and their search indexes. Before this a restore or a move left the books behind. Students' stored data also gets **point-in-time recovery** (restore to any second of the last 35 days; a few cents at most), which needs an `apply`. Traces are not in a backup on purpose (they expire after 30 to 90 days; download an archive from the Traces tab to keep them), and the messages and documents now say so.\n", 1)
write("CHANGELOG.md", s)
print("ok")
