// Tells the student, in one line, when a newer class kit is available (or when theirs is too old). Used by `npm run check`, `npm run deploy`
// and the editor's MCP server. It never changes anything: updating is `npm run platform -- update --apply`.
//
// Quiet by design: it asks the portal at most once a day (the answer is kept in .platform-notice.json), waits at most 2 seconds, says nothing
// when offline, and prints only for a person at a terminal (not in CI, not when output is piped), so logs and scripts stay clean.
//   PLATFORM_NOTICE=off     never
//   PLATFORM_NOTICE=force   also when output is piped (used by tests)
//   PLATFORM_PORTAL=<url>   ask another portal than the one in platform.json (used by tests)
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const DAY_MS = 24 * 3600 * 1000;
const TIMEOUT_MS = 2000;
const CACHE = '.platform-notice.json';
const SAFE_SCRIPT = /^node (tools\/[\w.-]+\.mjs|portal-deploy\.mjs)( [\w .-]+)?$/; // the only kind of command `update` will ever add to package.json

/** -1, 0 or 1 for dotted numeric versions ("1.10.0" is newer than "1.9.0"). Anything unparseable counts as 0. */
export function compareVersions(a, b) {
  const x = String(a || '0').split('.').map((n) => parseInt(n, 10) || 0);
  const y = String(b || '0').split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] || 0) - (y[i] || 0);
    if (d) return d < 0 ? -1 : 1;
  }
  return 0;
}

const readJson = (path, fallback = {}) => { try { return JSON.parse(readFileSync(path, 'utf8')); } catch { return fallback; } };

/** The package.json scripts the portal says belong to the kit that this project does not have yet: { name: command }. */
export function missingScripts(root, remote) {
  const have = readJson(join(root, 'package.json')).scripts ?? {};
  return Object.fromEntries(Object.entries(remote?.scripts ?? {}).filter(([name, command]) => !(name in have) && /^[a-z][a-z0-9:-]*$/.test(name) && SAFE_SCRIPT.test(String(command))));
}

/** Where this project stands against the portal's kit: { mine, latest, minKit, tooOld, newer, missing }, or null when the portal cannot be asked.
 *  `fresh` skips the one-a-day memory. */
export async function kitStatus(root, { fresh = false } = {}) {
  const platform = readJson(join(root, 'platform.json'));
  const portal = String(process.env.PLATFORM_PORTAL || platform.portal || '').replace(/\/$/, '');
  if (!portal) return null;
  const cachePath = join(root, CACHE);
  let remote = null;
  const cached = readJson(cachePath, null);
  if (!fresh && cached && cached.portal === portal && Date.now() - cached.checkedAt < DAY_MS && cached.remote) remote = cached.remote;
  if (!remote) {
    try {
      const response = await fetch(`${portal}/kit-version.json`, { signal: AbortSignal.timeout(TIMEOUT_MS) });
      if (!response.ok) return null;
      remote = await response.json();
      try { writeFileSync(cachePath, JSON.stringify({ checkedAt: Date.now(), portal, remote })); } catch { /* a read-only folder just means asking again next time */ }
    } catch {
      return null; // offline, slow or no such portal: say nothing
    }
  }
  const mine = platform.kitVersion || '0';
  return {
    mine, latest: remote.kitVersion, minKit: remote.minKit || '0',
    tooOld: compareVersions(mine, remote.minKit || '0') < 0, newer: compareVersions(mine, remote.kitVersion) < 0,
    missing: Object.keys(missingScripts(root, remote)),
  };
}

export function noticeText(status) {
  if (!status) return '';
  const fix = 'npm run platform -- update --apply';
  if (status.tooOld) return `⚠ Your class kit (${status.mine}) is too old for this platform. Update it now:  ${fix}`;
  if (status.newer) return `ℹ A newer class kit is available (you have ${status.mine}, the portal has ${status.latest}). Update:  ${fix}`;
  if (status.missing.length) return `ℹ Your package.json is missing kit scripts (${status.missing.join(', ')}). Add them with:  ${fix}`;
  return '';
}

/** Print the notice to stderr when it is appropriate. Returns the text (empty when nothing was said). */
export async function updateNotice(root) {
  const mode = process.env.PLATFORM_NOTICE;
  if (mode === 'off') return '';
  if (mode !== 'force' && (!process.stderr.isTTY || process.env.CI)) return '';
  const text = noticeText(await kitStatus(root));
  if (text) console.error(`\n${text}`);
  return text;
}

export const hasCache = (root) => existsSync(join(root, CACHE));
