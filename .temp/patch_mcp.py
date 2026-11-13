root = r"D:\Github_Codes\class-hosting-platform-github" + "\\"
def sub(p, a, b):
    f = root + p
    s = open(f, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(f, "w", encoding="utf8").write(s.replace(a, b, 1))

sub("starter\mcp\server.mjs", "  {\n    name: 'update_kit',", """  {
    name: 'project_status',
    description: 'Project sync: list the student\'s projects on the platform (their personal one and any team projects their teacher made) and say what is not sent yet. Reads only; changes nothing. Use it to see whether a push or a pull is needed, for example when the student sits down at another computer or a teammate may have changed files.',
    inputSchema: { type: 'object', properties: { path: PATH_PROPERTY }, additionalProperties: false },
    run: ({ path } = {}) => { const root = projectRoot(path); return run(scriptFor(root, 'deploy'), ['project'], 60000, root); },
  },
  {
    name: 'pull_project',
    description: 'Project sync: bring the newest source files of the student\'s project from the platform into this folder (another computer, or a teammate\'s changes). It never overwrites a file both sides changed: their copy is saved as <file>.theirs and the student decides. With dry_run=true it only lists what would happen. IMPORTANT: ask the student first and call this with confirm=true only after they say yes (dry_run needs no yes). For a team project pass its id as project (the folder remembers it). Never use theirs=true unless the student asked to take the platform\'s copy; their own file is kept as <file>.mine.',
    inputSchema: {
      type: 'object',
      properties: { confirm: { type: 'boolean', description: 'true only after the student agreed to pull now' }, dry_run: { type: 'boolean' }, project: { type: 'string', description: 'a team project id; leave out for the personal project' }, theirs: { type: 'boolean', description: 'take the platform\'s copy for files that conflict (only if the student asked)' }, path: PATH_PROPERTY },
      additionalProperties: false,
    },
    async run({ confirm, dry_run: dryRun, project, theirs, path } = {}) {
      if (confirm !== true && dryRun !== true) return { ok: false, text: 'Not pulled. Ask the student whether to pull now; if they say yes, call pull_project again with confirm=true (or use dry_run=true to only look).' };
      const root = projectRoot(path);
      return run(scriptFor(root, 'deploy'), ['pull', ...(dryRun ? ['--dry-run'] : []), ...(project ? ['--project', String(project)] : []), ...(theirs ? ['--theirs'] : [])], 120000, root);
    },
  },
  {
    name: 'push_project',
    description: 'Project sync: send the files the student changed (new, changed, deleted) to their project on the platform, so another computer or a teammate can pull them. Secrets (.env files, keys), node_modules, build output and the kit\'s own files are never sent. If a teammate changed the same file, nothing is sent: the answer lists the files; tell the student, run pull_project, and let them decide (mine=true sends the student\'s version over the platform\'s only if they ask). With dry_run=true it only lists what would be sent. IMPORTANT: ask the student first and call this with confirm=true only after they say yes (dry_run needs no yes).',
    inputSchema: {
      type: 'object',
      properties: { confirm: { type: 'boolean', description: 'true only after the student agreed to push now' }, dry_run: { type: 'boolean' }, project: { type: 'string', description: 'a team project id; leave out for the personal project' }, mine: { type: 'boolean', description: 'send the student\'s files over the platform\'s when they conflict (only if the student asked)' }, path: PATH_PROPERTY },
      additionalProperties: false,
    },
    async run({ confirm, dry_run: dryRun, project, mine, path } = {}) {
      if (confirm !== true && dryRun !== true) return { ok: false, text: 'Not pushed. Ask the student whether to push now; if they say yes, call push_project again with confirm=true (or use dry_run=true to only look).' };
      const root = projectRoot(path);
      return run(scriptFor(root, 'deploy'), ['push', ...(dryRun ? ['--dry-run'] : []), ...(project ? ['--project', String(project)] : []), ...(mine ? ['--mine'] : [])], 120000, root);
    },
  },
  {
    name: 'update_kit',""")
sub("tests\test_mcp.mjs", "['init_project', 'check_project', 'deploy', 'status', 'logs', 'update_kit']", "['init_project', 'check_project', 'deploy', 'status', 'logs', 'project_status', 'pull_project', 'push_project', 'update_kit']")
sub("tests\test_mcp.mjs", "  assert.deepEqual(tools.find((t) => t.name === 'deploy').inputSchema.required, ['confirm']);", "  assert.deepEqual(tools.find((t) => t.name === 'deploy').inputSchema.required, ['confirm']);\n  for (const name of ['pull_project', 'push_project']) assert.match(tools.find((t) => t.name === name).description, /ask the student first[\s\S]*confirm=true/i, `${name} asks before it acts`);")
print("ok")
