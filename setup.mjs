#!/usr/bin/env node
// Install and update your own copy of the class hosting platform in your own AWS account.
//
//   node setup.mjs                      guided install (asks for the AWS account, an optional domain, the features and your teacher login)
//   node setup.mjs install              the same, or with flags and no questions:  --profile p --domain apps.example.com --features ai,storage
//   node setup.mjs finish-domain        step two of a domain install, after the DNS records are added
//   node setup.mjs teacher <username>   set or reset the teacher login (asks for the password, hidden)
//   node setup.mjs secrets              save the AI keys in AWS Secrets Manager (asks, hidden)
//   node setup.mjs publish              publish the portal pages, the student kit and the MCP package again
//   node setup.mjs status               what is installed, which version, which features
//   node setup.mjs plan                 show what an update (or a feature switch: --enable ai / --disable ai) would change, change nothing
//   node setup.mjs apply <plan-id>      run exactly that plan, then publish the portal
//   node setup.mjs upgrade              plan, show, ask, apply (after `git pull`)
//   node setup.mjs check                is this computer and AWS account ready
//   node setup.mjs destroy              delete the stack (typed confirmation)
//
// Flags: --profile <aws profile>  --stack <name>  --dry-run (print what would run)  --json (one JSON object, no questions; used by the teacher MCP)  --yes
import { execFileSync } from 'node:child_process';
import { createInterface } from 'node:readline/promises';
import { dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as engine from './installer/engine.mjs';
process.env.PORTAL_DEPLOY_NO_MAIN = '1'; // importing the student CLI for its hidden-input helpers must not start it
const { editLine, maskSecret } = await import('./student/portal-deploy.mjs');

const repo = dirname(fileURLToPath(import.meta.url));

export function parseArgs(argv) {
  const flags = {};
  const rest = [];
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (!a.startsWith('--')) { rest.push(a); continue; }
    const key = a.slice(2);
    if (['dry-run', 'json', 'yes'].includes(key)) flags[key] = true;
    else flags[key] = argv[++i];
  }
  return { command: rest[0], args: rest.slice(1), flags };
}

export function makeContext(flags, overrides = {}) {
  const saved = engine.readState({ repo }) ?? {}; // what an earlier install chose, so later commands need no flags
  const profile = flags.profile ?? process.env.AWS_PROFILE ?? saved.profile ?? '';
  const stack = flags.stack ?? process.env.STACK_NAME ?? saved.stack ?? 'student-hosting';
  const ctx = { repo, profile, stack, dryRun: Boolean(flags['dry-run']), would: [], now: () => new Date().toISOString(), sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    aws: engine.realAws({ profile }), ...overrides };
  return ctx;
}

// ---------- terminal questions ----------
async function readHidden(question) {
  process.stdout.write(question);
  if (!process.stdin.isTTY || typeof process.stdin.setRawMode !== 'function') {
    const rl = createInterface({ input: process.stdin });
    for await (const line of rl) { rl.close(); return line.trim(); }
    return '';
  }
  return new Promise((resolve, reject) => {
    let buffer = '';
    process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.setEncoding('utf8');
    const onData = (chunk) => {
      const r = editLine(buffer, chunk);
      if (r.abort) { cleanup(); reject(new Error('Cancelled.')); return; }
      buffer = r.buffer;
      process.stdout.write(`\r\u001b[2K${question}${maskSecret(buffer)}`);
      if (r.done) { cleanup(); process.stdout.write('\n'); resolve(buffer.trim()); }
    };
    const cleanup = () => { process.stdin.off('data', onData); process.stdin.setRawMode(false); process.stdin.pause(); };
    process.stdin.on('data', onData);
  });
}

async function wizard(ctx, flags) {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const ask = async (q, d = '') => ((await rl.question(d ? `${q} [${d}] ` : `${q} `)).trim() || d);
  const yes = async (q, d = 'n') => /^y/i.test(await ask(`${q} (y/n)`, d));
  console.log('Class platform: guided install into your own AWS account\n');
  console.log('You need: an AWS account, the AWS CLI signed in (aws configure), and optionally a domain name you control.\n');
  const answers = {};
  let profiles = [];
  try { profiles = execFileSync('aws', ['configure', 'list-profiles'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).split(/\s+/).map((x) => x.trim()).filter(Boolean); } catch { /* no AWS CLI yet: the check below says so */ }
  if (profiles.length) console.log(`AWS profiles on this computer: ${profiles.join(', ')}`);
  answers.profile = flags.profile ?? await ask('AWS profile to use (empty = your default credentials)', ctx.profile);
  answers.domain = flags.domain ?? await ask('Domain for student sites, e.g. apps.example.com (empty = free test addresses on cloudfront.net)', '');
  const features = [];
  console.log('\nOptional features (all can be switched on later with `node setup.mjs plan --enable <name>`):');
  if (await yes('  Student backends (a small server per student)')) features.push('backend');
  if (features.includes('backend')) {
    if (await yes('  Class AI (OpenRouter, capped per student)')) features.push('ai');
    if (features.includes('ai') && await yes('  Google Gemini as a second AI provider')) features.push('gemini');
    if (await yes('  Class storage (small database and files per student)')) features.push('storage');
  }
  answers.features = features;
  answers.teacher = await ask('Teacher login name (3-40 letters, digits . _ -)', 'teacher');
  const password = await readHidden('Teacher password (at least 10 characters, hidden): ');
  const keys = {};
  if (features.includes('ai')) {
    console.log('\nAI keys are stored in AWS Secrets Manager, never in files. Press Enter to skip one and add it later with `node setup.mjs secrets`.');
    keys.managementKey = await readHidden('OpenRouter management key (hidden): ');
    if (features.includes('gemini')) keys.googleKey = await readHidden('Google Gemini API key (hidden): ');
  }
  // show the account before anything is created, so the wrong profile is caught here
  let account;
  try { account = engine.preflight({ ...ctx, profile: answers.profile, stack: ctx.stack, aws: engine.realAws({ profile: answers.profile }), dryRun: false, would: [] }); } catch (e) { rl.close(); throw e; }
  console.log(`
This will install into AWS account ${account.account} (signed in as ${account.arn}).`);
  if (!(await yes('Continue?', 'n'))) { rl.close(); throw new engine.InstallError('Stopped before anything was created.'); }
  rl.close();
  return { answers, password, keys };
}

const print = (data, flags) => {
  if (flags.json) { process.stdout.write(`${JSON.stringify(data)}\n`); return; }
  if (data.would?.length) console.log(`Dry run: nothing was changed. These commands would run:\n  ${data.would.join('\n  ')}`);
  if (data.message) console.log(data.message);
  if (data.record) console.log(`\n  ${data.record.type}  ${data.record.name}\n  ->     ${data.record.value}`);
  if (data.records) for (const r of data.records) console.log(`  ${r.type}  ${r.name}  ->  ${r.value}   (${r.for})`);
  if (data.portalUrl) console.log(`\nYour portal: ${data.portalUrl}`);
  if (data.checks?.length) for (const c of data.checks) console.log(`  ${c.ok ? 'ok ' : 'FAIL'} ${c.path} (${c.status})`);
  if (data.items?.length) for (const i of data.items) console.log(`  ${i.action.padEnd(7)} ${i.id} (${i.type}${i.replacement !== 'False' ? `, replacement: ${i.replacement}` : ''})`);
  if (data.next) console.log(`\nNext: ${data.next}`);
};

export async function main(argv, overrides = {}) {
  const { command = 'wizard', args, flags } = parseArgs(argv);
  const ctx = makeContext(flags, overrides);
  const finish = (data) => { print({ ...data, would: ctx.dryRun ? ctx.would : undefined }, flags); return data; };
  try {
    switch (command) {
      case 'wizard': case 'install': {
        if (flags.json || !process.stdin.isTTY || command === 'install' && (flags.domain !== undefined || flags.features !== undefined || flags.yes)) { // no terminal, or all choices given as flags: no questions
          return finish(await engine.install(ctx, { domain: flags.domain, features: (flags.features || '').split(',').filter(Boolean), certificateArn: flags['certificate-arn'] }));
        }
        const { answers, password, keys } = await wizard(ctx, flags);
        const ctx2 = { ...ctx, profile: answers.profile, aws: overrides.aws ?? engine.realAws({ profile: answers.profile }) };
        let result = await engine.install(ctx2, answers);
        if (keys.managementKey || keys.googleKey) engine.saveAiSecret(ctx2, keys);
        if (result.needs === 'certificate') { print(result, flags); console.log('\nAfter the record is added (AWS checks it within minutes), run `node setup.mjs install --domain ' + answers.domain + '` again.'); return result; }
        if (result.needs === 'dns') {
          print(result, flags);
          const rl = createInterface({ input: process.stdin, output: process.stdout });
          await rl.question('\nPress Enter once the records are added (DNS can take a few minutes to spread)... ');
          rl.close();
          result = await engine.finishDomain(ctx2);
        }
        if (password.length >= 10) engine.teacherLogin(ctx2, answers.teacher, password); else console.log('Teacher password was too short: set it later with `node setup.mjs teacher <name>`.');
        print(result, flags);
        console.log(`\nSign in at the portal with "${answers.teacher}". Create a student token on the teacher page and give it to a student with the "Start here" command shown there.`);
        return result;
      }
      case 'finish-domain': return finish(await engine.finishDomain(ctx));
      case 'teacher': {
        if (!args[0]) throw new engine.InstallError('Usage: node setup.mjs teacher <username>');
        if (flags.json) throw new engine.InstallError('The teacher password is never typed through an editor.', `Run in your own terminal: node setup.mjs teacher ${args[0]}`);
        return finish(engine.teacherLogin(ctx, args[0], process.env.TEACHER_PASSWORD || await readHidden('New teacher password (hidden): ')));
      }
      case 'secrets': {
        if (flags.json) throw new engine.InstallError('AI keys are never typed through an editor.', 'Run in your own terminal: node setup.mjs secrets');
        const managementKey = await readHidden('OpenRouter management key (hidden, empty to skip): ');
        const googleKey = await readHidden('Google Gemini API key (hidden, empty to skip): ');
        return finish({ ...engine.saveAiSecret(ctx, { managementKey, googleKey }), message: 'Saved in AWS Secrets Manager.' });
      }
      case 'status': return finish(await engine.status(ctx));
      case 'publish': return finish({ ok: true, ...engine.adminScript(ctx, ['portal-publish']) && {}, message: ctx.dryRun ? undefined : 'Portal pages, student kit and MCP package published.' });
      case 'check': return finish(engine.checkEnvironment(ctx));
      case 'plan': {
        const feature = flags.enable ?? flags.disable;
        return finish(feature ? await engine.featurePlan(ctx, feature, Boolean(flags.enable)) : await engine.plan(ctx));
      }
      case 'apply': return finish(await engine.apply(ctx, args[0]));
      case 'upgrade': {
        const planned = await engine.plan(ctx);
        print(planned, flags);
        if (flags.json || planned.blocked || planned.empty || ctx.dryRun) return planned;
        const rl = createInterface({ input: process.stdin, output: process.stdout });
        const go = flags.yes || /^y/i.test((await rl.question('\nApply this update? (y/n) ')).trim());
        rl.close();
        return go ? finish(await engine.apply(ctx, planned.planId)) : planned;
      }
      case 'destroy': {
        if (flags.json) throw new engine.InstallError('Deleting the stack is only done by typing the name in your own terminal.', 'Run: node setup.mjs destroy');
        const rl = createInterface({ input: process.stdin, output: process.stdout });
        const typed = (await rl.question(`Type the stack name (${ctx.stack}) to delete it and everything students published: `)).trim();
        rl.close();
        if (typed !== ctx.stack) { console.log('Not deleted.'); return { ok: false }; }
        return finish(engine.destroy(ctx));
      }
      default:
        console.log('Unknown command. Run: node setup.mjs --help (see the top of setup.mjs).');
        process.exitCode = 1;
        return { ok: false };
    }
  } catch (e) {
    if (!(e instanceof engine.InstallError)) throw e;
    if (flags.json) process.stdout.write(`${JSON.stringify({ ok: false, error: e.message, hint: e.hint })}\n`);
    else console.error(`\n${e.message}${e.hint ? `\n  ${e.hint}` : ''}`);
    process.exitCode = 1;
    return { ok: false, error: e.message };
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main(process.argv.slice(2));
