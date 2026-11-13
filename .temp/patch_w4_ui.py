root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


# ---- template: the portal may delete job files (only under worker/jobs/)
edit("template.yaml", """            - !If
              - UseWorker
              - Effect: Allow
                Action: [s3:GetObject, s3:PutObject]
                Resource: !Sub '${SitesBucket.Arn}/worker/*'
              - !Ref AWS::NoValue
            - Effect: Allow
              Action: [s3:GetObject, s3:PutObject, s3:DeleteObject] # never portal/""", """            - !If
              - UseWorker
              - Effect: Allow
                Action: [s3:GetObject, s3:PutObject]
                Resource: !Sub '${SitesBucket.Arn}/worker/*'
              - !Ref AWS::NoValue
            - !If # students' own jobs (worker_jobs.py): removing a finished job deletes its files under worker/jobs/ only
              - UseWorker
              - Effect: Allow
                Action: s3:DeleteObject
                Resource: !Sub '${SitesBucket.Arn}/worker/jobs/*'
              - !Ref AWS::NoValue
            - Effect: Allow
              Action: [s3:GetObject, s3:PutObject, s3:DeleteObject] # never portal/""")
edit("tests/test_template_worker.py", """assert "ec2:RunInstances" not in portal_fn and "ec2:TerminateInstances" not in portal_fn""", """assert "ec2:RunInstances" not in portal_fn and "ec2:TerminateInstances" not in portal_fn
assert re.search(r"- UseWorker\\n\\s+- Effect: Allow\\n\\s+Action: s3:DeleteObject\\n\\s+Resource: !Sub '\\$\\{SitesBucket\\.Arn\\}/worker/jobs/\\*'", portal_fn), "the portal may delete job files under worker/jobs/ only\"""")

# ---- teacher page: the queue
edit("web/teacher.js", "let worker = null; // the answer of GET /api/admin/worker, or { unavailable: true }", "let worker = null; // the answer of GET /api/admin/worker, or { unavailable: true }\nlet workerJobs = []; // the students' jobs (queue and recent), from GET /api/admin/worker/jobs")
edit("web/teacher.js", """    worker = out.available === false ? { unavailable: true } : out;
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 409)) worker = { unavailable: true };""", """    worker = out.available === false ? { unavailable: true } : out;
    if (!worker.unavailable) workerJobs = (await call('/api/admin/worker/jobs').catch(() => ({ jobs: [] }))).jobs ?? [];
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 409)) worker = { unavailable: true };""")
edit("web/teacher.js", "  return [head, note, buttons, state, schedule, last];\n}", """  return [head, note, buttons, state, schedule, jobsSection(), last];
}

const JOB_STATES = { created: ['Uploading', 'warn'], queued: ['Waiting', 'warn'], running: ['Running', 'active'], done: ['Done', 'active'], failed: ['Failed', 'expired'], cancelled: ['Cancelled', 'offline'], expired: ['Expired', 'offline'] };

/** The students' own-code jobs (access level 3): who is waiting, what ran. Jobs wait for the machine; only you start it. */
function jobsSection() {
  const open = workerJobs.filter((j) => ['created', 'queued', 'running'].includes(j.state));
  const rows = workerJobs.length ? workerJobs.map((j) => {
    const [label, cls] = JOB_STATES[j.state] || [j.state, 'offline'];
    const when_ = j.finished_at || j.queued_at || j.created_at;
    return h('div', { class: 'srow', style: 'grid-template-columns: minmax(90px,1fr) 90px minmax(120px,1.4fr) 140px 150px' },
      h('span', {}, j.slug), h('span', {}, h('span', { class: `pill ${cls}` }, label)),
      h('span', { class: 'small muted', title: j.message || '' }, `${j.language} · ${j.entry}${j.size ? ` · ${mb(j.size)}` : ''}${j.seconds ? ` · ${j.seconds} s` : ''}${j.network ? ' · internet' : ''}`),
      h('span', { class: 'small muted' }, when_ ? when(when_ * 1000) : ''),
      h('span', { class: 'btns' },
        ['created', 'queued'].includes(j.state) ? h('button', { class: 'small', onclick: () => jobAction(j, 'cancel') }, 'Cancel') : null,
        ['done', 'failed', 'cancelled', 'expired'].includes(j.state) ? h('button', { class: 'small danger', onclick: () => jobAction(j, 'delete') }, 'Delete') : null));
  }) : [h('div', { class: 'small muted', style: 'padding:8px 16px' }, 'No jobs yet. A student with access level 3 sends one from their project; it waits here until you start the machine.')];
  return h('section', { class: `feature ${open.length ? 'on' : ''}`, style: 'margin:0 16px 12px' },
    h('div', { class: 'feature-head' }, h('div', {}, h('h3', {}, 'Students’ jobs'), h('div', { class: 'small muted' }, open.length ? `${open.length} waiting or running. They run one at a time when the machine is on` : 'Nothing is waiting'))),
    h('div', { class: 'feature-body' }, rows));
}

async function jobAction(j, action) {
  const ok = action === 'delete' ? await ask({ title: 'Delete this job', text: 'The job record, its uploaded files and its result are removed. This cannot be undone.', confirm: 'Delete', danger: true }) : true;
  if (!ok) return;
  try {
    await call(`/api/admin/worker/jobs/${j.slug}/${j.id}${action === 'cancel' ? '/cancel' : ''}`, { method: action === 'cancel' ? 'POST' : 'DELETE', body: action === 'cancel' ? {} : undefined });
    toast(action === 'cancel' ? 'Job cancelled' : 'Job deleted');
  } catch (e) { toast(e.message); }
  await loadWorker();
  render();
}""")
print("ok")
