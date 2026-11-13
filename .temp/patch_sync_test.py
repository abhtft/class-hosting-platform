root = r"D:\Github_Codes\class-hosting-platform-github\tests" + "\\"
p = root + "projects_fixture.py"
s = open(p, encoding="utf8").read()
a = '''    def do_GET(self):
        if self.path.startswith("/s3/"):'''
assert a in s
s = s.replace(a, '''    def do_GET(self):
        if self.path.startswith("/__corrupt/"):  # test only: damage a stored file the way a bad disk or a tampering member would
            key = self.path[len("/__corrupt/"):]
            store[key] = {"Body": b"corrupted" + store[key]["Body"]}
            return self.send(200, b"{}")
        if self.path.startswith("/s3/"):''', 1)
open(p, "w", encoding="utf8").write(s)
p = root + "test_project_sync.mjs"
s = open(p, encoding="utf8").read()
i = s.index("  // a damaged download is refused")
j = s.index("  // the commands")
s = s[:i] + '''  // a damaged download is refused and nothing is written
  const mine = await (await fetch(`${info.url}/api/student/projects/robot-club`, { headers: { authorization: `Bearer ${sessions.alice}` } })).json();
  const entry = mine.files['src/sensor.py'];
  await fetch(`${info.url}/__corrupt/projects/t/robot-club/blobs/${entry.sha256}`);
  const T4 = join(work, 'T4');
  await assert.rejects(tool.pull(opts('bob', T4, { project: 'robot-club' })), /did not match its checksum/);
  assert.equal(existsSync(join(T4, 'src/sensor.py')), false, 'a damaged file is not written');
  say('a downloaded file whose bytes do not match its SHA-256 is refused and not written');

''' + s[j:]
s = s.replace("  fs_touch(cli);\n", "").replace("\nfunction fs_touch() {}\n", "\n")
s = s.replace("  await assert.rejects(tool.push(opts('carol', T3, { project: 'robot-club' })), /not a member|Nothing|no such/i).catch(() => {});\n", "")
open(p, "w", encoding="utf8").write(s)
