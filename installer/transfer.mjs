// Move a class to another AWS account, in steps, with the old account untouched until the teacher decides to delete it:
//   1. install the same platform (same features, same domain) in the target account
//   2. copy the data across (backup of the source, import into the target; see data.mjs)
//   3. run the full check on the target (a throwaway student publishes a sample site)
//   4. write a CSV of every DNS change the move needs, for the teacher to apply (and to send to students who use their own domain)
// The DNS switch itself is the teacher's, at the teacher's DNS host: after it, the old account can be deleted with `destroy`. There is no live
// replication and no automatic DNS change: a short, announced pause in publishing is the safe design.
// The function is resumable: it keeps a small state file, so running it again continues where it stopped (for example after the certificate's DNS record).
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { exportData, importData } from './data.mjs';
import * as engine from './engine.mjs';

export const CSV_COLUMNS = ['who', 'name', 'type', 'old_value', 'new_value', 'note'];

const quote = (value) => {
  const text = String(value ?? '');
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

/** RFC 4180 CSV (CRLF line ends, quotes doubled), with a byte order mark so spreadsheets read it as UTF-8. */
export const dnsCsv = (rows) => `﻿${[CSV_COLUMNS.join(','), ...rows.map((row) => CSV_COLUMNS.map((c) => quote(row[c])).join(','))].join('\r\n')}\r\n`;

/**
 * Every DNS change the move needs. `source` and `target` are {outputs, parameters}. `certificateRecord` is the ACM validation record of the target's
 * certificate (needed once, harmless to add before the switch). `domains` are the students' own custom domains found in the data.
 */
export function buildDnsRows({ domain, source, target, certificateRecord = null, domains = [] }) {
  const rows = [];
  if (!domain) {
    rows.push({ who: 'teacher', name: '(no domain)', type: '', old_value: source.outputs.PortalUrl ?? '', new_value: target.outputs.PortalUrl ?? '',
      note: 'No custom domain: there is no DNS to change. Give your students the new portal address and have them sign in again; their tokens still work.' });
    return rows;
  }
  if (certificateRecord) {
    rows.push({ who: 'teacher', name: certificateRecord.name, type: certificateRecord.type, old_value: '', new_value: certificateRecord.value,
      note: 'Step 1, do this first (any time before the switch): lets AWS issue the new account\'s certificate. Keep it in place afterwards.' });
  }
  rows.push({ who: 'teacher', name: `deploy.${domain}`, type: 'CNAME', old_value: source.outputs.PortalDistributionDomain ?? '', new_value: target.outputs.PortalDistributionDomain ?? '',
    note: 'Step 2, the switch: the portal address. Change it when the check on the new account has passed.' });
  rows.push({ who: 'teacher', name: `*.${domain}`, type: 'CNAME', old_value: source.outputs.DistributionDomain ?? '', new_value: target.outputs.DistributionDomain ?? '',
    note: 'Step 2, the switch: all student sites. Change it at the same time as the portal record.' });
  for (const d of domains) {
    rows.push({ who: `student ${d.slug}`, name: d.domain, type: 'CNAME', old_value: source.parameters.RoutingEndpoint ?? '', new_value: '',
      note: 'This student\'s own domain. Custom domains are not moved automatically in this release: switch it on again for this student in the new account, then give the student the new value to put here.' });
  }
  return rows;
}

const statePath = (dir) => join(dir, 'transfer.json');
const readState = (dir) => { try { return JSON.parse(readFileSync(statePath(dir), 'utf8')); } catch { return null; } };
const writeState = (dir, state) => { mkdirSync(dir, { recursive: true }); writeFileSync(statePath(dir), `${JSON.stringify(state, null, 2)}\n`); };

/**
 * source, target: engine contexts for the two accounts (their own profile and runner). `confirm` holds the two account numbers the teacher typed;
 * they must equal the real ones. `login` is the teacher login for the full check on the target (the same login works there: it is copied).
 */
export async function transfer({ source, target, confirm, login = null, csvPath, say = () => {}, keepFolder = '', stateFolder, finish = false }) {
  const sourceFound = engine.preflight(source);
  const targetFound = engine.preflight(target);
  if (sourceFound.account === targetFound.account) throw new engine.InstallError('The source and the target are the same AWS account.', 'Use two different profiles.');
  if (confirm?.source !== sourceFound.account || confirm?.target !== targetFound.account) {
    throw new engine.InstallError('The account numbers typed do not match the two accounts.', `Source is ${sourceFound.account} and target is ${targetFound.account}. Nothing was changed.`);
  }
  if (!sourceFound.stackStatus || !sourceFound.stackStatus.endsWith('_COMPLETE')) throw new engine.InstallError(`There is no working platform "${source.stack}" in the source account.`);
  const dir = stateFolder ?? join(engine.stateDir(source), 'transfer');
  const state = readState(dir) ?? { phase: 'start', source: sourceFound.account, target: targetFound.account };
  if (state.source !== sourceFound.account || state.target !== targetFound.account) throw new engine.InstallError('A transfer between other accounts is already in progress here.', `Remove ${statePath(dir)} to start a new one.`);
  const targetCtx = { ...target, stateDir: join(dir, 'target-install'), progress: say };
  const sourceInfo = engine.stackOutputs(source);
  const features = Object.entries(engine.FEATURES).filter(([, p]) => sourceInfo.parameters[p] === 'true').map(([f]) => f);
  const domain = sourceInfo.parameters.RootDomain || '';
  const result = { ok: true, steps: [], source: sourceFound.account, target: targetFound.account };

  // 1. the target platform
  if (state.phase === 'start' || state.phase === 'installing') {
    say('Installing the platform in the target account...');
    writeState(dir, { ...state, phase: 'installing' });
    const installed = await engine.install(targetCtx, { features, domain, updateNotices: true });
    if (installed.needs === 'certificate') {
      const rows = buildDnsRows({ domain, source: sourceInfo, target: { outputs: {}, parameters: {} }, certificateRecord: installed.record });
      return { ...result, needs: 'certificate', rows, message: 'The target account needs one DNS record to issue the certificate for your domain. Add it (it is in the DNS file), wait a few minutes, then run the transfer again: it continues from here.' };
    }
    if (installed.needs === 'dns') say('The new platform exists. Your domain is attached to it only at the switch (step 2 in the DNS file), because DNS still points at the old one.');
    state.phase = 'installed';
    writeState(dir, state);
    result.steps.push('target installed');
  }

  // 2. the data
  let domains = state.domains ?? [];
  if (state.phase === 'installed') {
    const folder = keepFolder || mkdtempSync(join(tmpdir(), 'class-transfer-'));
    try {
      say('Copying the data out of the source account...');
      if (!existsSync(join(folder, 'manifest.json'))) exportData({ ...source, progress: say }, folder, { say });
      say('Writing it into the target account...');
      const imported = importData({ ...targetCtx }, folder, { forTransfer: true, say });
      domains = imported.domains;
    } finally {
      if (!keepFolder) { try { rmSync(folder, { recursive: true, force: true }); } catch { /* removed with the system's temp clean-up */ } }
    }
    state.phase = 'copied';
    state.domains = domains;
    writeState(dir, state);
    result.steps.push('data copied');
  }

  // 3. the full check on the target
  const targetInfo = engine.stackOutputs(targetCtx);
  let verified = null;
  if (state.phase === 'copied' || state.phase === 'verified') {
    say('Checking the new platform (a throwaway student publishes a sample site)...');
    verified = await engine.verifyInstall(targetCtx, { login });
    state.phase = 'verified';
    state.verified = verified.ok;
    writeState(dir, state);
    result.steps.push(verified.ok ? 'target verified' : 'target check had failures');
  }

  // after the DNS switch the teacher runs the transfer once more with finish: the new platform takes over the domain
  if (finish && domain && (state.phase === 'verified' || state.phase === 'done')) {
    say('Attaching your domain to the new platform...');
    await engine.finishDomain(targetCtx);
    state.phase = 'done';
    writeState(dir, state);
    result.steps.push('domain attached to the new platform');
  }

  // 4. the DNS file
  const rows = buildDnsRows({ domain, source: sourceInfo, target: targetInfo, domains });
  if (csvPath) { writeFileSync(csvPath, dnsCsv(rows)); result.csvPath = csvPath; }
  return { ...result, rows, domains, verified, portalUrl: targetInfo.outputs.PortalUrl,
    message: `The class is copied to account ${targetFound.account}${verified ? (verified.ok ? ' and the new platform passed its check' : ' but the check on it had failures (see above)') : ''}. The old account is untouched. ${csvPath ? `The DNS changes are in ${csvPath}. ` : ''}Apply them at your DNS host, wait for them to spread, check the new portal, and only then delete the old platform with destroy.` };
}
