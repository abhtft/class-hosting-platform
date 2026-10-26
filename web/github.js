// The student's "Start here" card: download the kit, connect the GitHub repository (deep links, no tokens), and a checklist that ticks
// as things happen. Below it, the older downloads for a student who already has a project. Nothing here touches the API, and no
// token is ever put in a link or a downloaded file. The files are published next to the pages (see admin.mjs and portal-downloads.json).
import { copyText, fill, h } from './common.js';

export const DOWNLOAD_FILES = {
  cli: 'portal-deploy.mjs',
  guide: 'github-deploy-guide.md',
  workflow: { frontend: 'deploy-frontend-folder.yml', root: 'deploy-root-app.yml' },
};
export const KIT_FILES = { zip: 'starter-kit.zip', version: 'kit-version.json' };

// ---- connecting an AI editor without downloading anything: the MCP package, started by the editor with npx ----
/** The one command a student runs once per computer. `origin` is the portal's address. */
export const setupCommand = (origin) => `npx -y ${String(origin).replace(/\/$/, '')}/class-platform-mcp.tgz setup`;

/** What an editor runs to start the server. Windows editors need `cmd /c npx` because they cannot start npx directly. */
export function mcpEntry(version, origin, windows = false) {
  const npx = ['-y', `${String(origin).replace(/\/$/, '')}/class-platform-mcp-${version}.tgz`];
  return windows ? { command: 'cmd', args: ['/c', 'npx', ...npx] } : { command: 'npx', args: npx };
}

/** A link that opens VS Code and offers to add the server (VS Code's "mcp/install" link format). */
export const vscodeInstallLink = (entry) => `vscode:mcp/install?${encodeURIComponent(JSON.stringify({ name: 'class-platform', ...entry }))}`;

/** The complete text for Antigravity's raw MCP config when the student has no other servers. */
export const antigravityConfig = (entry) => JSON.stringify({ mcpServers: { 'class-platform': entry } }, null, 2);
/** Set to the public template repository (https://github.com/abhtft/class-hosting-platform) once it exists: a "Use this template" link then appears. */
export const TEMPLATE_URL = '';

const REPO_KEY = 'student-github-repo';
let built = false;

export function stopGithub() {
  built = false;
}

/** "https://github.com/alice/my-site" (also without https://, with .git or a trailing slash) -> {owner, repo, base}; anything else -> null. */
export function parseRepo(text) {
  const match = String(text ?? '').trim().match(/^(?:https:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]{1,100}?)(?:\.git)?\/?$/);
  if (!match || match[2] === '.' || match[2] === '..' || match[1].endsWith('-')) return null;
  return { owner: match[1], repo: match[2], base: `https://github.com/${match[1]}/${match[2]}` };
}

export function repoLinks(repo) {
  return { secret: `${repo.base}/settings/secrets/actions/new`, actions: `${repo.base}/actions`, settings: `${repo.base}/settings` };
}

const store = {
  get() { try { return localStorage.getItem(REPO_KEY) || ''; } catch { return ''; } },
  set(value) { try { localStorage.setItem(REPO_KEY, value); } catch { /* storage unavailable: the box just starts empty next time */ } },
};

function checklist(token) {
  const item = (done, text, hint) => h('li', { style: 'display:flex;gap:8px;align-items:baseline' },
    h('span', { 'aria-hidden': 'true', style: `color:var(${done ? '--ok' : '--muted'})` }, done ? '✔' : '○'),
    h('span', {}, text, !done && hint ? h('span', { class: 'muted' }, ` · ${hint}`) : null));
  const backendReady = token.backend?.available && token.backend.state === 'ready';
  return h('ul', { class: 'small', 'aria-label': 'Setup progress', style: 'list-style:none;margin:0;padding:0;display:grid;gap:4px' },
    item(Boolean(token.last_deploy_at), 'Your site is published', 'push to GitHub, or upload a build folder above'),
    token.backend?.available ? item(backendReady, 'Your backend is running', token.backend.enabled ? 'it appears after your first push' : 'ask your teacher to switch Backend on') : null,
    token.ai?.available ? item(Boolean(token.ai.enabled), 'Class AI is switched on for you', 'ask your teacher if your project needs it') : null);
}

export function renderGithub(box, token) {
  if (!token.can_deploy && token.status === 'revoked') { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  if (built) { // keep the typed repository and the open sections; only refresh the checklist
    const old = box.querySelector('[aria-label="Setup progress"]');
    if (old) old.replaceWith(checklist(token));
    return;
  }
  built = true;

  const version = h('span', { class: 'small muted' });
  fetch(KIT_FILES.version).then((r) => (r.ok ? r.json() : null)).then((v) => { if (v?.kitVersion) version.textContent = `kit ${v.kitVersion}`; }).catch(() => {});

  const links = h('div', { class: 'stack', style: 'gap:6px' });
  const input = h('input', { value: store.get(), placeholder: 'https://github.com/your-name/your-repo', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Your GitHub repository address' });
  const showLinks = () => {
    const repo = parseRepo(input.value);
    if (!input.value.trim()) return fill(links);
    if (!repo) return fill(links, h('div', { class: 'small error' }, 'That does not look like a GitHub repository address. Example: https://github.com/your-name/your-repo'));
    store.set(repo.base);
    const l = repoLinks(repo);
    const link = (href, text) => h('a', { class: 'btn', href, target: '_blank', rel: 'noopener noreferrer' }, text);
    fill(links, h('div', { class: 'actions', style: 'display:flex;gap:8px;flex-wrap:wrap' },
      link(l.secret, '1. Add the PORTAL_TOKEN secret'), link(l.actions, '2. Watch your deployments')),
    h('p', { class: 'small muted', style: 'margin:0' }, 'On the first page: name ', h('code', {}, 'PORTAL_TOKEN'), ' and paste the token you signed in with. Never put the token in a file or in your code.'));
  };
  input.addEventListener('input', showLinks);
  showLinks();

  const layout = (value, text, checked) => h('label', { style: 'display:flex;gap:8px;align-items:center;color:var(--text)' },
    h('input', { type: 'radio', name: 'gh-layout', value, checked, style: 'width:auto', onchange: () => workflow.setAttribute('href', DOWNLOAD_FILES.workflow[value]) }), text);
  const workflow = h('a', { class: 'btn', href: DOWNLOAD_FILES.workflow.frontend, download: 'deploy.yml' }, 'Download deploy.yml');

  // the editor connection (no download): needs the newest package version, which the portal publishes in mcp-version.json
  const windows = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent || '');
  const origin = typeof location !== 'undefined' ? location.origin : '';
  const command = setupCommand(origin);
  const vscodeLink = h('a', { class: 'btn', href: '#', 'aria-disabled': 'true' }, 'Add to VS Code');
  let agConfig = '';
  const copyAg = h('button', { class: 'small', disabled: true, onclick: () => copyText(agConfig) }, 'Copy Antigravity config');
  fetch('mcp-version.json').then((r) => (r.ok ? r.json() : null)).then((v) => {
    if (!v?.version) return;
    const entry = mcpEntry(v.version, origin, windows);
    vscodeLink.setAttribute('href', vscodeInstallLink(entry));
    vscodeLink.removeAttribute('aria-disabled');
    agConfig = antigravityConfig(entry);
    copyAg.disabled = false;
  }).catch(() => {});
  const connect = h('div', { class: 'stack', style: 'gap:8px' },
    h('strong', {}, 'Fastest: connect your AI editor (nothing to download)'),
    h('p', { class: 'small muted', style: 'margin:0' }, 'Run this once on your computer (it needs Node). It asks for your token, saves it for every project, and adds the class tools to VS Code and Antigravity.'),
    h('div', { class: 'secret', style: 'font-size:13px' }, command),
    h('div', { class: 'actions', style: 'display:flex;gap:8px;flex-wrap:wrap;align-items:center' },
      h('button', { class: 'primary small', onclick: () => copyText(command) }, 'Copy the command'), vscodeLink, copyAg),
    h('ol', { class: 'small', style: 'margin:0;padding-left:20px;display:grid;gap:4px' },
      h('li', {}, 'Run the command in a terminal and paste your token when asked (you will see ', h('code', {}, 'mds_'), ' and stars).'),
      h('li', {}, 'Restart your editor, or press Refresh in its MCP list. (Antigravity without the command: ', h('em', {}, 'Manage MCP servers, View raw config'), ', paste the copied config, save.)'),
      h('li', {}, 'Open an ', h('strong', {}, 'empty folder'), ' and tell the AI: ', h('code', {}, 'set up my class project'), '. Then describe your app, and say ', h('code', {}, 'publish my site'), ' when you are happy.')));

  fill(box,
    h('h2', {}, 'Start here'),
    connect,
    h('strong', {}, 'Or download the starter kit'),
    h('p', { class: 'small muted', style: 'margin:0' }, 'Build your site and backend with your AI editor, push to GitHub, and it deploys by itself. You only need your token.'),
    h('div', { class: 'actions', style: 'display:flex;gap:8px;flex-wrap:wrap;align-items:center' },
      h('a', { class: 'btn primary', href: KIT_FILES.zip, download: 'class-platform-starter.zip' }, 'Download the starter kit'),
      TEMPLATE_URL ? h('a', { class: 'btn', href: `${TEMPLATE_URL}/generate`, target: '_blank', rel: 'noopener noreferrer' }, 'or use the GitHub template') : null,
      version),
    h('ol', { class: 'small', style: 'margin:0;padding-left:20px;display:grid;gap:4px' },
      h('li', {}, 'Unzip it and open the folder in your AI editor (Antigravity, Cursor, VS Code, Claude Code, ...). It already contains the rules your AI follows.'),
      h('li', {}, 'Tell the AI what to build. Ideas are in ', h('code', {}, 'PROMPTS.md'), '. Try it with ', h('code', {}, 'npm run dev'), ' (the AI is an offline mock there, nothing is spent).'),
      h('li', {}, 'Run ', h('code', {}, 'npm run check'), ', then create a GitHub repository for the folder and push it.'),
      h('li', {}, 'Paste the repository address below for one-click links to add your token and watch the deploy.')),
    h('div', { class: 'stack', style: 'gap:6px' }, h('strong', {}, 'Your GitHub repository'), input, links),
    h('div', { class: 'stack', style: 'gap:6px' }, h('strong', {}, 'Progress'), checklist(token)),
    h('details', {},
      h('summary', {}, 'Already have a project? Add auto-deploy to it'),
      h('div', { class: 'stack', style: 'margin-top:8px' },
        h('strong', {}, 'Where is your app in the repo?'),
        layout('frontend', h('span', {}, 'In a ', h('code', {}, 'frontend/'), ' folder (with ', h('code', {}, 'backend/'), ' beside it)'), true),
        layout('root', h('span', {}, 'At the top of the repo (a normal Vite or React project, with ', h('code', {}, 'backend/'), ' beside ', h('code', {}, 'src/'), ')'), false),
        h('div', { class: 'actions', style: 'display:flex;gap:8px;flex-wrap:wrap' },
          h('a', { class: 'btn', href: DOWNLOAD_FILES.cli, download: DOWNLOAD_FILES.cli }, 'Download portal-deploy.mjs'),
          workflow,
          h('a', { class: 'btn', href: DOWNLOAD_FILES.guide, download: 'GITHUB-DEPLOY-GUIDE.md' }, 'Full guide')),
        h('p', { class: 'small muted', style: 'margin:0' }, 'Put ', h('code', {}, 'portal-deploy.mjs'), ' in the root of your repo, save the workflow as ', h('code', {}, '.github/workflows/deploy.yml'),
          ', and add the secret ', h('code', {}, 'PORTAL_TOKEN'), '. The pipeline stops when your token expires: request regeneration here, then update the secret.'))));
}
