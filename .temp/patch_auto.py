import os

root = "D:/Github_Codes/class-hosting-platform-github/"


def sub(p, a, b):
    f = root + p
    s = open(f, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(f, "w", encoding="utf8").write(s.replace(a, b, 1))


P = "starter/template/tools/project.mjs"
sub(P, """    return { project: state.project || 'personal', rev: state.rev || 0, base: state.base || {}, known: true };
  } catch {
    return { project: 'personal', rev: 0, base: {}, known: false };
  }
}

export function saveState(root, state) {
  writeFileSync(join(root, STATE_FILE), `${JSON.stringify({ project: state.project, rev: state.rev, base: state.base }, null, 1)}\\n`);
}""", """    return { project: state.project || 'personal', rev: state.rev || 0, base: state.base || {}, auto: typeof state.auto === 'boolean' ? state.auto : undefined, known: true };
  } catch {
    return { project: 'personal', rev: 0, base: {}, auto: undefined, known: false };
  }
}

/** Writes what was synced. `auto` (the student's answer to "save this project automatically after every deploy?": true, false or not asked yet) is kept as it is unless given. */
export function saveState(root, state) {
  const auto = state.auto !== undefined ? state.auto : loadState(root).auto;
  writeFileSync(join(root, STATE_FILE), `${JSON.stringify({ project: state.project, rev: state.rev, base: state.base, ...(auto === undefined ? {} : { auto }) }, null, 1)}\\n`);
}

/** Remember the student's answer to "save this project on the platform automatically after every deploy?" (true, false). */
export function setAuto(root, value) {
  const state = loadState(root);
  saveState(root, { ...state, auto: Boolean(value) });
}""")
sub(P, "export async function push({ portalUrl, session, root = '.', project, mine = false, dryRun = false, log = console.log, warn = console.warn }) {\n  const call = http(portalUrl, session);",
    "export async function push({ portalUrl, session, root = '.', project, mine = false, dryRun = false, remember = false, log = console.log, warn = console.warn }) {\n  const call = http(portalUrl, session);\n  if (remember && !dryRun) setAuto(root, true); // the student said yes: this project is saved now and after every deploy")
sub(P, "// What is never synced:", "// After the first yes (npm run push, or the editor tool push_project), every `npm run deploy` also saves the project's files here (never stopping the deploy);\n//   npm run project -- --auto off    stops that (and  --auto on  starts it again).\n// What is never synced:")

D = "student/portal-deploy.mjs"
sub(D, """/** `pull`, `push` and `project`: keep the project's source files on the platform (a second computer, or a team). */
async function projectCommand(name) {
  const tool = join(process.cwd(), 'tools', 'project.mjs');
  if (!existsSync(tool)) fail('tools/project.mjs is missing. Run  npm run platform -- update --apply  to restore the kit files.');
  const { pull, push, status, Stop: ToolStop } = await import(pathToFileURL(tool).href);""", """async function loadProjectTool() {
  const tool = join(process.cwd(), 'tools', 'project.mjs');
  return existsSync(tool) ? import(pathToFileURL(tool).href) : null;
}

/** After a deploy: save the project's files on the platform when the student has said yes (once, by `push`, the editor tool, or the question below). It never stops or fails the deploy. */
async function syncProjectAfterDeploy(session) {
  if (flag('--no-project-sync')) return;
  const tool = await loadProjectTool();
  if (!tool) return;
  try {
    const state = tool.loadState(process.cwd());
    if (state.auto === false) return;
    if (state.auto !== true) {
      if (!process.stdin.isTTY) {
        console.log('Tip: your project files are not saved on the platform yet. Run  npm run push  once (or tell your AI editor "save my project") to keep a copy that you can use on other computers; after that every deploy saves it for you.');
        return;
      }
      const answer = (await ask('\\nSave a copy of your project files on the platform, now and after every deploy? You can use them on other computers, and your teacher can read them. [Y/n] ')).trim().toLowerCase();
      if (answer === 'n' || answer === 'no') { tool.setAuto(process.cwd(), false); console.log('OK, not saved. Change your mind later with  npm run project -- --auto on'); return; }
      tool.setAuto(process.cwd(), true);
    }
    const keep = (message) => { if (/^(Pushed|Nothing to push|Nothing from|\\s+CONFLICT)/.test(message)) console.log(message); };
    const result = await tool.push({ portalUrl: PORTAL_URL, session, root: process.cwd(), log: keep, warn: () => {} });
    if (result.conflicts.length) console.warn('⚠ Your project files were not saved: a teammate changed the same file(s). Run  npm run pull  and then  npm run push.');
    else if (result.pushed) console.log('✔ Project files saved on the platform.');
  } catch (error) {
    console.warn(`⚠ Your project files were not saved (${error.message}). Run  npm run push  to try again. Your deploy is not affected.`);
  }
}

/** `pull`, `push` and `project`: keep the project's source files on the platform (a second computer, or a team). */
async function projectCommand(name) {
  const tool = join(process.cwd(), 'tools', 'project.mjs');
  if (!existsSync(tool)) fail('tools/project.mjs is missing. Run  npm run platform -- update --apply  to restore the kit files.');
  const { pull, push, status, setAuto, Stop: ToolStop } = await import(pathToFileURL(tool).href);
  if (name === 'project' && option('--auto')) {
    if (!['on', 'off'].includes(option('--auto'))) fail('--auto is on or off.');
    setAuto(process.cwd(), option('--auto') === 'on');
    console.log(option('--auto') === 'on' ? 'Your project files will be saved on the platform after every deploy (and by  npm run push).' : 'Your project files will not be saved automatically any more. You can still use  npm run push.');
    return;
  }""")
sub(D, "      const result = await push({ ...common, mine: flag('--mine') });", "      const result = await push({ ...common, mine: flag('--mine'), remember: flag('--remember') });")
sub(D, "  if (!only) await syncBooksAfterDeploy(session, token);", "  if (!only) await syncBooksAfterDeploy(session, token);\n  await syncProjectAfterDeploy(session);")
sub(D, "  for (const line of featureLines(token)) console.log(line);\n}", """  for (const line of featureLines(token)) console.log(line);
  try { // project sync: say whether this project is saved on the platform (the editor tool reads this and asks the student once)
    const tool = await loadProjectTool();
    if (tool) {
      const state = tool.loadState(process.cwd());
      if (state.auto === true) console.log('Project files: saved on the platform after every deploy (turn off with  npm run project -- --auto off).');
      else if (state.auto === false) console.log('Project files: automatic saving is off for this project (npm run project -- --auto on turns it on).');
      else console.log('Project files: NOT saved on the platform yet. Ask the student: "Save a copy of your project files on the platform, so you can use them on other computers? (your teacher can read them; I will also save them after every deploy)". If yes, use push_project with confirm=true (or  npm run push  in a terminal); if no, use project_auto with enabled=false.');
    }
  } catch { /* a status line is never worth a failure */ }
}""")

M = "starter/mcp/server.mjs"
sub(M, "      return run(scriptFor(root, 'deploy'), ['push', ...(dryRun ? ['--dry-run'] : []),", "      return run(scriptFor(root, 'deploy'), ['push', ...(dryRun ? ['--dry-run'] : ['--remember']),")
sub(M, "never overwritten',", "never overwritten',") if False else None
sub(M, "(dry_run needs no yes).',\n    inputSchema: {\n      type: 'object',\n      properties: { confirm: { type: 'boolean', description: 'true only after the student agreed to push now' }", "(dry_run needs no yes). A yes also turns on saving after every deploy, so say that when you ask; the student can turn it off with project_auto.',\n    inputSchema: {\n      type: 'object',\n      properties: { confirm: { type: 'boolean', description: 'true only after the student agreed to push now' }")
sub(M, "  {\n    name: 'update_kit',", """  {
    name: 'project_auto',
    description: 'Project sync: turn the automatic saving of the project files (after every deploy) on or off for this folder. enabled=false needs no question (the student said no or changed their mind). enabled=true needs the student\\u2019s yes first: call it with confirm=true only after they said yes.',
    inputSchema: { type: 'object', properties: { enabled: { type: 'boolean' }, confirm: { type: 'boolean', description: 'true only after the student agreed (needed to turn it on)' }, path: PATH_PROPERTY }, required: ['enabled'], additionalProperties: false },
    async run({ enabled, confirm, path }) {
      if (typeof enabled !== 'boolean') return { ok: false, text: 'enabled must be true or false.' };
      if (enabled && confirm !== true) return { ok: false, text: 'Not turned on. Ask the student whether to save the project files after every deploy; if they say yes, call project_auto again with confirm=true.' };
      const root = projectRoot(path);
      return run(scriptFor(root, 'deploy'), ['project', '--auto', enabled ? 'on' : 'off'], 30000, root);
    },
  },
  {
    name: 'update_kit',""")
print("ok")
