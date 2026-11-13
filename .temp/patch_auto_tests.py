root = "D:/Github_Codes/class-hosting-platform-github/"
def sub(p, a, b):
    f = root + p
    s = open(f, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(f, "w", encoding="utf8").write(s.replace(a, b, 1))

T = "tests/test_mcp.mjs"
sub(T, "  assert.match(status.content[0].text, /Signed in as .*alice/i);\n", "  assert.match(status.content[0].text, /Signed in as .*alice/i);\n  assert.match(status.content[0].text, /Project files: NOT saved on the platform yet\. Ask the student/, 'status tells the AI to ask the student once about saving the project');\n")
sub(T, "  say('deploy with confirm=true checks, then publishes the backend through the same portal-deploy.mjs');", """  say('deploy with confirm=true checks, then publishes the backend through the same portal-deploy.mjs');

  // the yes given to push_project also turns on saving after every deploy; project_auto turns it off and on
  assert.match(deployed.content[0].text, /Project files saved on the platform|Nothing to push/, 'a deploy saves the project files after the first yes');
  assert.match((await mcp.tool('status')).content[0].text, /Project files: saved on the platform after every deploy/);
  appendFileSync(join(kit, 'README.md'), '\nA note written after the first save.\n');
  const again = await mcp.tool('deploy', { confirm: true, only: 'backend' });
  assert.match(again.content[0].text, /Pushed 1 change\./, 'a changed file is saved by the next deploy without anyone asking');
  assert.equal((await mcp.tool('project_auto', { enabled: true })).isError, true, 'turning it on needs a yes');
  assert.equal((await mcp.tool('project_auto', { enabled: false })).isError, false);
  assert.match((await mcp.tool('status')).content[0].text, /automatic saving is off for this project/);
  appendFileSync(join(kit, 'README.md'), '\nAnother note.\n');
  const quiet = await mcp.tool('deploy', { confirm: true, only: 'backend' });
  assert.doesNotMatch(quiet.content[0].text, /Pushed|Project files saved/, 'with it off a deploy saves nothing');
  assert.equal((await mcp.tool('project_auto', { enabled: true, confirm: true })).isError, false);
  say('after the first yes every deploy saves the project files by itself; project_auto turns that off (no question) and on (needs a yes), and status says which');""")

S = "tests/test_project_sync.mjs"
sub(S, "  // a team project\n", """  // the answer to "save automatically after every deploy?" is remembered and survives every later sync
  const auto = join(work, 'auto');
  put(auto, 'a.txt', 'one');
  assert.equal(tool.loadState(auto).auto, undefined, 'not asked yet');
  await tool.push(opts('carol', auto));
  assert.equal(tool.loadState(auto).auto, undefined, 'a plain push does not decide for the student');
  await tool.push(opts('carol', auto, { remember: true }));
  assert.equal(tool.loadState(auto).auto, true, 'a push with a yes turns it on');
  put(auto, 'a.txt', 'two');
  await tool.push(opts('carol', auto));
  await tool.pull(opts('carol', auto));
  assert.equal(tool.loadState(auto).auto, true, 'pushes and pulls keep the answer');
  tool.setAuto(auto, false);
  assert.equal(tool.loadState(auto).auto, false);
  await tool.push(opts('carol', auto, { dryRun: true, remember: true }));
  assert.equal(tool.loadState(auto).auto, false, 'a dry run never turns it on');
  say('the automatic-saving answer: unknown until the student says yes (a push with a yes) or no, and every later sync keeps it');

  // a team project
""")
print("ok")
