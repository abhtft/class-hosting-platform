root = r"D:\Github_Codes\class-hosting-platform-github" + "\\"


def sub(p, a, b):
    f = root + p
    s = open(f, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(f, "w", encoding="utf8").write(s.replace(a, b, 1))


sub("web\\teacher.html", '      <button role="tab" id="tab-worker" aria-selected="false">Worker</button>', '      <button role="tab" id="tab-projects" aria-selected="false">Projects</button>\n      <button role="tab" id="tab-worker" aria-selected="false">Worker</button>')
T = "web\\teacher.js"
sub(T, "['students', 'requests', 'storage', 'library', 'traces', 'worker', 'usage']", "['students', 'requests', 'storage', 'library', 'traces', 'projects', 'worker', 'usage']")
sub(T, "  } else if (tab === 'worker') {\n    rows = workerTab();", "  } else if (tab === 'projects') {\n    rows = projectsTab();\n  } else if (tab === 'worker') {\n    rows = workerTab();")
sub(T, "$('tab-worker').onclick =", "$('tab-projects').onclick = async () => { tab = 'projects'; render(); await loadProjects(); render(); };\n$('tab-worker').onclick =")
sub(T, "// ---------- the worker tab:", r'''// ---------- the projects tab: source files students share through the platform instead of GitHub (projects.py) ----------
const PROJ = '/api/admin/projects';
let projects = null; // the answer of GET /api/admin/projects
let projectOpen = null; // { ref, project, files, history } for the project being looked at
let projectBusy = '';

async function loadProjects() {
  try {
    projects = await call(PROJ);
    if (projectOpen) projectOpen = { ref: projectOpen.ref, ...(await call(`${PROJ}/${projectOpen.ref}`)) };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) projectOpen = null;
    else $('error').textContent = e.message;
  }
}

async function projectAct(path, method, body, message) {
  projectBusy = message;
  render();
  let out = null;
  try {
    out = await call(path, { method, body });
    toast(message);
  } catch (e) {
    toast(e.message);
  }
  projectBusy = '';
  await loadProjects();
  render();
  return out;
}

const memberList = (text) => text.split(/[\s,]+/).map((m) => m.trim()).filter(Boolean);

async function newProject() {
  const names = tokens.map((t) => t.slug).join(', ');
  const r = await ask({ title: 'New team project', text: 'A team project is one set of source files that several students share (they use npm run pull and npm run push). Every student also has a personal project of their own.',
    fields: [{ name: 'id', label: 'Project id (lowercase letters, digits and -)', required: true, placeholder: 'robot-club', maxlength: 40 },
      { name: 'title', label: 'Title (optional)', placeholder: 'Robot club', maxlength: 80 },
      { name: 'members', label: `Members: site names separated by commas (${names || 'no students yet'})`, placeholder: 'asha, ravi', maxlength: 600 }], confirm: 'Create' });
  if (!r) return;
  await projectAct(PROJ, 'POST', { id: r.id.trim(), title: r.title.trim(), members: memberList(r.members) }, 'Project created');
}

async function editMembers(p) {
  const r = await ask({ title: `Members of ${p.title}`, text: 'Site names separated by commas. A student who is removed can no longer pull or push; their earlier changes stay in the project.',
    fields: [{ name: 'members', label: 'Members', required: true, value: p.members.join(', '), maxlength: 600 }], confirm: 'Save' });
  if (!r) return;
  await projectAct(`${PROJ}/${p.ref}`, 'POST', { members: memberList(r.members) }, 'Members saved');
}

async function deleteProject(p) {
  const r = await ask({ title: `Delete “${p.title}”`, text: `This deletes all ${p.files} file${p.files === 1 ? '' : 's'}, their older versions and the history, for everyone. It cannot be undone. Computers that already pulled the files keep their copy.`,
    fields: [{ name: 'confirm', label: `Type “${p.name}” to confirm`, required: true, placeholder: p.name, maxlength: 60 }], confirm: 'Delete', danger: true });
  if (!r) return;
  if (r.confirm.trim() !== p.name) { toast('The name did not match, so nothing was deleted'); return; }
  projectOpen = null;
  await projectAct(`${PROJ}/${p.ref}`, 'DELETE', undefined, 'Project deleted');
}

async function openFile(ref, path) {
  try {
    const out = await call(`${PROJ}/${ref}/file`, { method: 'POST', body: { path } });
    window.open(out.files[path].url, '_blank', 'noopener');
  } catch (e) {
    toast(e.message);
  }
}

async function putBack(ref, path, rev) {
  const r = await ask({ title: 'Put this version back', text: `${path} will be set to how it was in revision ${rev}. This makes a new revision; nothing is lost. If students changed the file since, their next push will ask them to pull first.`, fields: [], confirm: 'Put back' });
  if (!r) return;
  await projectAct(`${PROJ}/${ref}/restore`, 'POST', { path, rev }, 'File put back');
}

function projectsTab() {
  if (!projects) return [h('div', { class: 'empty' }, 'Loading the projects…')];
  const note = h('p', { class: 'privacy small muted' },
    'Every student can keep their source files on the platform (npm run push / npm run pull), so the same code is on every computer they use. For a team, make a team project and add its members: they share one set of files, and a file two people changed is never overwritten silently. You can read any file, put an earlier version back, freeze a project and clean up old versions. Limits: 500 files, 5 MB a file, 50 MB a project.');
  const bar = h('div', { class: 'btns', style: 'padding:0 16px 12px' }, h('button', { class: 'primary small', onclick: newProject }, 'New team project…'), projectBusy ? h('span', { class: 'small muted' }, projectBusy) : null);
  if (projectOpen?.project) return [note, bar, projectDetail(projectOpen)];
  const rows = projects.projects.length ? projects.projects.map((p) => h('button', { class: 'student', onclick: async () => { projectOpen = { ref: p.ref }; await loadProjects(); render(); }, 'aria-label': `Open ${p.title}` },
    h('span', { class: 'student-main' },
      h('span', { class: 'title' }, p.kind === 'team' ? p.title : `${p.name} (personal)`),
      h('span', { class: 'small muted' }, `${p.kind === 'team' ? `team · ${p.members.join(', ') || 'no members'}` : 'one student'} · revision ${p.rev} · ${p.files} file${p.files === 1 ? '' : 's'} · ${mb(p.bytes)}${p.updated_at ? ` · ${date(p.updated_at)}` : ''}`)),
    p.frozen ? chip('Frozen', 'Nothing can be pushed') : null))
    : [h('div', { class: 'empty' }, 'No projects yet. A student’s personal project appears when they first run npm run push; make a team project for a group.')];
  return [note, bar, ...rows];
}

function projectDetail(d) {
  const p = { ...d.project, ref: d.ref };
  const back = h('button', { class: 'small', onclick: () => { projectOpen = null; render(); } }, '← All projects');
  const actions = h('div', { class: 'btns' },
    p.kind === 'team' ? h('button', { class: 'small', onclick: () => editMembers(p) }, 'Members…') : null,
    h('button', { class: 'small', onclick: () => projectAct(`${PROJ}/${p.ref}`, 'POST', { frozen: !p.frozen }, p.frozen ? 'Project unfrozen' : 'Project frozen') }, p.frozen ? 'Unfreeze' : 'Freeze'),
    h('button', { class: 'small', onclick: async () => { const out = await projectAct(`${PROJ}/${p.ref}/compact`, 'POST', {}, 'Old versions removed'); if (out) toast(`Freed ${mb(out.freed_bytes)}`); } }, 'Clean up old versions'),
    h('button', { class: 'small danger', onclick: () => deleteProject(p) }, 'Delete…'));
  const head = h('div', { class: 'tiles inner' }, tile('Revision', p.rev, p.frozen ? 'frozen' : 'open'), tile('Files', p.files, `of ${p.limits.files}`), tile('Size', mb(p.bytes), `of ${p.limits.project_mb} MB`), tile('Stored versions', mb(p.stored), 'all uploads'));
  const files = d.files.length ? d.files.map((f) => h('div', { class: 'student' }, h('span', { class: 'student-main' }, h('span', { class: 'title' }, f.path),
      h('span', { class: 'small muted' }, `${mb(f.size)} · ${f.by || '—'} · revision ${f.rev}${f.ts ? ` · ${date(f.ts)}` : ''}`)),
    h('button', { class: 'small', onclick: () => openFile(p.ref, f.path) }, 'Open')))
    : [h('div', { class: 'small muted', style: 'padding:8px 16px' }, 'No files yet.')];
  const history = d.history.length ? d.history.map((r) => h('div', { class: 'student' }, h('span', { class: 'student-main' },
      h('span', { class: 'title' }, `Revision ${r.rev} · ${r.by}${r.label ? ` · ${r.label}` : ''}`),
      h('span', { class: 'small muted' }, `${date(r.ts)} · ${r.changes.map((c) => `${c.op === 'delete' ? 'deleted ' : ''}${c.path}`).join(', ').slice(0, 240)}`)),
    r.changes.length === 1 ? h('button', { class: 'small', onclick: () => putBack(p.ref, r.changes[0].path, r.rev) }, 'Put back') : null))
    : [h('div', { class: 'small muted', style: 'padding:8px 16px' }, 'No history yet.')];
  return h('section', { class: 'feature on', style: 'margin:0 16px 12px' },
    h('div', { class: 'feature-head' }, h('div', {}, h('h3', {}, p.kind === 'team' ? p.title : `${p.name} (personal)`), h('div', { class: 'small muted' }, p.kind === 'team' ? `Members: ${p.members.join(', ') || 'none'}` : 'Only this student')), back),
    h('div', { class: 'feature-body' }, head, actions, h('h4', { style: 'margin:12px 0 4px' }, 'Files'), ...files, h('h4', { style: 'margin:12px 0 4px' }, 'History (newest first)'), ...history));
}

// ---------- the worker tab:''')
print("ok")
