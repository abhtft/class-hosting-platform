root = r"D:\Github_Codes\class-hosting-platform-github\tests" + "\\"
def sub(p, a, b):
    s = open(root + p, encoding="utf8").read()
    assert a in s, (p, a[:50])
    open(root + p, "w", encoding="utf8").write(s.replace(a, b, 1))
sub("test_template_ops.py", '''# the single-line grants: class storage's userdata/ and class knowledge's knowledge/ (the portal's, the API's and the book reader's), nothing else
single = re.findall(r"Action: \[s3:GetObject, s3:PutObject, s3:DeleteObject\]\n\s+Resource: !Sub '([^']+)'", template)
assert sorted(single) == ["${SitesBucket.Arn}/knowledge/*"] * 3 + ["${SitesBucket.Arn}/userdata/*"], single''',
'''# the single-line grants: class storage's userdata/, class knowledge's knowledge/ (the portal's, the API's and the book reader's) and project sync's projects/
# (the portal only: it signs upload forms and download links and deletes, and never reads a file), nothing else
single = re.findall(r"Action: \[s3:GetObject, s3:PutObject, s3:DeleteObject\]\n\s+Resource: !Sub '([^']+)'", template)
assert sorted(single) == ["${SitesBucket.Arn}/knowledge/*"] * 3 + ["${SitesBucket.Arn}/projects/*", "${SitesBucket.Arn}/userdata/*"], single
assert "${SitesBucket.Arn}/projects/*" in block("DeployFunction") and template.count("${SitesBucket.Arn}/projects/*") == 1, "only the portal function may touch projects/"''')
sub("test_template_ops.py", 'assert template.count("s3:PutObject") == 9, "every S3 write grant is reviewed (two lists above, class storage\'s userdata/, three for knowledge/, three for worker/)', 'assert template.count("s3:PutObject") == 10, "every S3 write grant is reviewed (two lists above, class storage\'s userdata/, three for knowledge/, project sync\'s projects/, three for worker/)')
sub("run.mjs", "await step('knowledge folder sync (scan", '''await step('project sync: the portal side (personal and team projects, content-addressed uploads, the manifest with its revision, conflicts and a race, every limit, the teacher view, restore, compact, delete) and the kit side (scan, plans, pull, push, conflicts and both ways out, a team, a damaged download, the commands) against the real portal code', () => {
  let r = spawnSync(python, [join(here, 'test_projects.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
  r = spawnSync('node', [join(here, 'test_project_sync.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('knowledge folder sync (scan''')
