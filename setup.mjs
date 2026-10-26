#!/usr/bin/env node
// Install and update your own copy of the class hosting platform in your own AWS account.
//
//   node setup.mjs                      guided install (asks for the AWS account, an optional domain, the features and your teacher login)
//   node setup.mjs install              the same, or with flags and no questions:  --profile p --domain apps.example.com --features ai,storage
//   node setup.mjs finish-domain        step two of a domain install, after the DNS records are added
//   node setup.mjs teacher <username>   set or reset the teacher login (asks for the password, hidden)
//   node setup.mjs secrets              save the AI keys in AWS Secrets Manager (asks, hidden)
//   node setup.mjs publish              publish the portal pages, the student kit and the MCP package again
//   node setup.mjs export <folder>      back up everything the class made (tokens, stored student data, published files, teacher login) to a new private folder
//   node setup.mjs import <folder>      add such a backup to this account's platform (deletes nothing; safe to repeat)
//   node setup.mjs transfer --from <profile> --to <profile>   move the class to another AWS account, with a CSV of the DNS changes; the old account is only read
//   node setup.mjs setup                one-time setup of this computer: saves your AWS access (as its own profile) and adds the teacher tools to your editor
//   node setup.mjs editor-update        move the editor's teacher tools to the newest release (asks first)
//   node setup.mjs verify               does it really work? a throwaway student publishes a sample site; asks for your teacher login (used once, never saved); --no-login = public checks only
//   node setup.mjs custom-domains       set up what students need to use their own domain (the install does this; use it to retry or on an older platform)
//   node setup.mjs status               what is installed, which version, which features
//   node setup.mjs plan                 show what an update (or a feature switch: --enable ai / --disable ai) would change, change nothing
//   node setup.mjs apply <plan-id>      run exactly that plan, then publish the portal
//   node setup.mjs upgrade              plan, show, ask, apply (after `git pull`)
//   node setup.mjs check                is this computer and AWS account ready
//   node setup.mjs destroy [--everything]  delete the platform (typed confirmation); --everything also removes student data and the AI keys, for test accounts
//
// Flags: --profile <aws profile>  --stack <name>  --dry-run (print what would run)  --json (one JSON object, no questions; used by the teacher MCP)  --yes
import { execFileSync } from 'node:child_process';
import { dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as engine from './installer/engine.mjs';
import { createPrompter } from './installer/prompts.mjs';
import { checkTeacherLogin } from './installer/verify.mjs';
import { PROFILE_NAME, readGlobalConfig, saveProfile, writeGlobalConfig } from './installer/credentials.mjs';
import { exportData, importData } from './installer/data.mjs';
import { transfer } from './installer/transfer.mjs';
import { resolve } from 'node:path';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

const prompt = createPrompter();

const repo = dirname(fileURLToPath(import.meta.url));

export function parseArgs(argv) {
  const flags = {};
  const rest = [];
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (!a.startsWith('--')) { rest.push(a); continue; }
    const key = a.slice(2);
    if (['dry-run', 'json', 'yes', 'everything', 'no-login', 'finish'].includes(key)) flags[key] = true;
    else flags[key] = argv[++i];
  }
  return { command: rest[0], args: rest.slice(1), flags };
}

/** --features a,b  (none given = every feature; "none" = no optional feature) */
export const parseFeatures = (text) => (String(text) === 'none' ? [] : String(text).split(',').map((x) => x.trim()).filter(Boolean));

export function makeContext(flags, overrides = {}) {
  const saved = engine.readState({ repo }) ?? {}; // what an earlier install chose, so later commands need no flags
  const profile = flags.profile ?? process.env.AWS_PROFILE ?? saved.profile ?? readGlobalConfig().profile ?? ''; // the profile `setup` saved is the last fallback
  const stack = flags.stack ?? process.env.STACK_NAME ?? saved.stack ?? 'student-hosting';
  const ctx = { repo, profile, stack, dryRun: Boolean(flags['dry-run']), would: [], now: () => new Date().toISOString(), sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    aws: engine.realAws({ profile }), progress: (text) => (flags.json ? console.error(text) : console.log(text)), ...overrides };
  return ctx;
}

async function wizard(ctx, flags) {
  const { ask, yes } = prompt;
  console.log('Class platform: guided install into your own AWS account');
  console.log('');
  console.log('Only what must be decided now is asked: the AWS account, your teacher login, and whether you want your own domain.');
  console.log('Every feature (backends, class AI, Gemini, class storage) is installed; each stays off for every student until you switch it on for that student. AI needs your keys: run the secrets command after the install.');
  console.log('');
  const answers = {};
  // The AWS account. Nothing to ask in AWS CloudShell (already signed in as you), or when the choice is clear.
  let profiles = [];
  try { profiles = execFileSync('aws', ['configure', 'list-profiles'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).split(/\s+/).map((x) => x.trim()).filter(Boolean); } catch { /* no AWS CLI yet: the check below says so */ }
  if (flags.profile !== undefined) answers.profile = flags.profile;
  else if (engine.inCloudShell() || ctx.profile || profiles.length <= 1) answers.profile = engine.inCloudShell() ? '' : ctx.profile;
  else {
    console.log(`AWS profiles on this computer: ${profiles.join(', ')}`);
    answers.profile = await ask('Which one should the platform be installed with? (type its name; Enter = your default credentials)', '');
  }
  // A domain cannot be added to an existing install later, so it is the one optional choice asked here.
  const wantsDomain = flags.domain !== undefined ? Boolean(flags.domain) : await yes('Use your own domain name for student sites? (No = free test addresses; everything can still be tried)', 'n');
  answers.domain = flags.domain ?? (wantsDomain ? await ask('Domain, for example apps.example.com:') : '');
  answers.features = flags.features === undefined ? undefined : parseFeatures(flags.features); // none given: every feature is installed
  answers.teacher = await ask('Choose your teacher login name (3-40 letters, digits . _ -):', 'teacher');
  const password = await prompt.newPassword('Choose your teacher password');
  answers.updateNotices = await yes('Tell you when a new version is released? (a notice only: nothing ever updates by itself)', 'y');
  // show the account before anything is created, so the wrong one is caught here
  const account = engine.preflight({ ...ctx, profile: answers.profile, stack: ctx.stack, aws: engine.realAws({ profile: answers.profile }), dryRun: false, would: [] });
  console.log('');
  console.log(`This will install into AWS account ${account.account} (signed in as ${account.arn}${answers.profile ? `, profile ${answers.profile}` : ''}).`);
  if (!(await yes('Continue?', 'n'))) throw new engine.InstallError('Stopped before anything was created.');
  return { answers, password, keys: {} };
}

async function loadEditorSetup() {
  for (const folder of ['mcp', 'teacher-mcp']) {
    if (existsSync(join(repo, folder, 'setup.mjs'))) return import(pathToFileURL(join(repo, folder, 'setup.mjs')).href);
  }
  throw new engine.InstallError('The editor setup files are missing from this copy.');
}

const print = (data, flags) => {
  if (data.results?.length) for (const r of data.results) console.log(`  ${r.editor}: ${r.action === 'same' ? 'already on this version' : r.action === 'manual' ? `left alone (${r.reason}); add this entry by hand: ${JSON.stringify(r.entry)}` : `${r.action}d${r.backup ? `, old file saved as ${r.backup}` : ''}`}`);
  if (flags.json) { process.stdout.write(`${JSON.stringify(data)}\n`); return; }
  if (data.would?.length) console.log(`Dry run: nothing was changed. These commands would run:\n  ${data.would.join('\n  ')}`);
  if (data.message) console.log(data.message);
  if (data.record) console.log(`\n  ${data.record.type}  ${data.record.name}\n  ->     ${data.record.value}`);
  if (data.records) for (const r of data.records) console.log(`  ${r.type}  ${r.name}  ->  ${r.value}   (${r.for})`);
  if (data.portalUrl) console.log(`\nYour portal: ${data.portalUrl}`);
  if (data.checks?.length) for (const c of data.checks) console.log(c.name ? `  ${c.ok ? 'ok  ' : 'FAIL'} ${c.name}${c.detail ? `: ${c.detail}` : ''}` : `  ${c.ok ? 'ok ' : 'FAIL'} ${c.path} (${c.status})`);
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
          return finish(await engine.install(ctx, { domain: flags.domain, features: flags.features === undefined ? undefined : parseFeatures(flags.features), certificateArn: flags['certificate-arn'] }));
        }
        const { answers, password, keys } = await wizard(ctx, flags);
        const ctx2 = { ...ctx, profile: answers.profile, aws: overrides.aws ?? engine.realAws({ profile: answers.profile }) };
        let result = await engine.install(ctx2, answers);
        if (keys.managementKey || keys.googleKey) engine.saveAiSecret(ctx2, keys);
        if (result.needs === 'certificate') {
          print(result, flags);
          console.log('');
          console.log(`You will add three DNS records for ${answers.domain} in total: this one now (it lets AWS issue your certificate), and two more after the platform is built (one for the student sites, one for the portal).`);
          console.log('In most DNS hosts (Cloudflare, for example) enter the name without the domain ending, and choose "DNS only" (no proxy).');
          console.log('');
          console.log(`After this record is added (AWS checks it within minutes), run the install again with:  ${engine.cli(ctx2)} install --domain ${answers.domain}${answers.profile ? ` --profile ${answers.profile}` : ''}`);
          return result;
        }
        if (result.needs === 'dns') {
          print(result, flags);
          await prompt.ask('\nPress Enter once the records are added (DNS can take a few minutes to spread)...');
          result = await engine.finishDomain(ctx2);
        }
        if (password.length >= 10) engine.teacherLogin(ctx2, answers.teacher, password); else console.log('Teacher password was too short: set it later with `node setup.mjs teacher <name>`.');
        print(result, flags);
        if (result.portalUrl && !result.needs && password.length >= 10) {
          console.log('');
          console.log('Checking that everything works: a throwaway student publishes a sample site (removed again afterwards)...');
          const checked = await engine.verifyInstall(ctx2, { login: { username: answers.teacher, password }, portal: result.portalUrl });
          print({ checks: checked.checks, message: checked.message }, flags);
        }
        console.log('');
        console.log(`Sign in at the portal with "${answers.teacher}". Create a student token on the teacher page and give it to a student with the "Start here" command shown there.`);
        console.log(`Every feature is installed. For each student, switch on what you want on the teacher page. The AI features need your keys first:  ${engine.cli(ctx2)} secrets.  To remove features you do not want:  plan --disable <name>  then  apply <id>.`);
        return result;
      }
      case 'finish-domain': return finish(await engine.finishDomain(ctx));
      case 'teacher': {
        if (!args[0]) throw new engine.InstallError('Usage: node setup.mjs teacher <username>');
        if (flags.json) throw new engine.InstallError('The teacher password is never typed through an editor.', `Run in your own terminal: node setup.mjs teacher ${args[0]}`);
        return finish(engine.teacherLogin(ctx, args[0], process.env.TEACHER_PASSWORD || await prompt.newPassword('New teacher password')));
      }
      case 'secrets': {
        if (flags.json) throw new engine.InstallError('AI keys are never typed through an editor.', 'Run in your own terminal: node setup.mjs secrets');
        const managementKey = await prompt.hidden('OpenRouter management key (hidden, empty to skip): ', { prefix: 4 });
        const googleKey = await prompt.hidden('Google Gemini API key (hidden, empty to skip): ', { prefix: 4 });
        return finish({ ...engine.saveAiSecret(ctx, { managementKey, googleKey }), message: 'Saved in AWS Secrets Manager.' });
      }
      case 'setup': {
        if (flags.json) throw new engine.InstallError('Setup asks for your AWS access key, which is never typed through an editor.', `Run in your own terminal: ${engine.cli(ctx)} setup`);
        console.log('Class platform: one-time setup of this computer');
        console.log('');
        let chosen = '';
        if (engine.inCloudShell()) {
          console.log('You are in AWS CloudShell: it is already signed in as you, so there is no AWS access to save.');
        } else {
          let profiles = [];
          try { profiles = execFileSync('aws', ['configure', 'list-profiles'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).split(/\s+/).map((x) => x.trim()).filter(Boolean); } catch { /* no AWS CLI: said below */ }
          if (profiles.length) console.log(`AWS profiles on this computer: ${profiles.join(', ')}`);
          const answer = await prompt.ask(profiles.length
            ? `Which AWS profile should the platform use? (a name from the list, or "new" to save an AWS access key as the profile ${PROFILE_NAME})`
            : `No AWS profile found. Type "new" to save an AWS access key as the profile ${PROFILE_NAME}:`, profiles.includes(ctx.profile) ? ctx.profile : (profiles.length ? '' : 'new'));
          if (answer === 'new') {
            console.log('Create the key in the AWS console: IAM > Users > your user > Security credentials > Create access key (use case: Command Line Interface).');
            const accessKeyId = (await prompt.ask('AWS access key id (starts with AKIA or ASIA):')).trim();
            const secretAccessKey = await prompt.hidden('AWS secret access key (hidden): ');
            const saved = saveProfile({ accessKeyId, secretAccessKey });
            chosen = saved.profile;
            console.log(`Saved as the profile "${chosen}" (the old files were backed up first).`);
          } else chosen = answer;
          const who = engine.preflight({ ...ctx, profile: chosen, aws: engine.realAws({ profile: chosen }), dryRun: false, would: [] });
          console.log('');
          console.log(`That signs in to AWS account ${who.account} as ${who.arn}.`);
          if (!(await prompt.yes('Use it for the class platform?', 'y'))) throw new engine.InstallError('Stopped. Nothing was changed except the profile file backup, if you saved a key.');
          writeGlobalConfig({ ...readGlobalConfig(), profile: chosen });
        }
        console.log('');
        console.log('Now the teacher tools for your AI editor:');
        const editorSetup = await loadEditorSetup();
        await editorSetup.main([]);
        console.log('');
        console.log('Done. Restart your editor and tell the AI:  install my class platform   (it asks you before it changes anything).');
        console.log('Passwords and API keys are never typed into the editor: the AI will tell you to run those steps in a terminal.');
        return { ok: true, profile: chosen };
      }
      case 'editor-update': {
        const info = engine.releaseInfo(ctx);
        if (!info) throw new engine.InstallError('This copy was not made from a GitHub release, so its editor entry cannot be moved to a release.', 'Use the release address with `editor` instead.');
        const latest = await engine.latestRelease(ctx);
        const version = latest?.version ?? engine.repoVersion(ctx);
        if (flags.json && !flags.yes) throw new engine.InstallError('Updating the editor entry needs the teacher to agree first.', 'Ask the teacher, then run it with --yes.');
        if (!flags.yes && !(await prompt.yes(`Move the teacher tools in your editors to version ${version}?`, 'y'))) return { ok: false };
        const editorSetup = await loadEditorSetup();
        const results = editorSetup.updateAll({ version, repo: info.repo });
        return finish({ ok: true, version, results, message: `Editor tools set to ${version}. Restart the editor (or refresh its MCP list) so the new version is used.` });
      }
      case 'export': {
        if (!args[0]) throw new engine.InstallError('Usage: node setup.mjs export <new folder>', 'The folder must be new or empty. It will hold student AI keys and the teacher login hash: keep it private.');
        return finish(exportData(ctx, resolve(args[0]), { say: ctx.progress }));
      }
      case 'import': {
        if (!args[0]) throw new engine.InstallError('Usage: node setup.mjs import <backup folder>', 'Adds the backup to the platform in this account; it deletes nothing, and repeating it is safe.');
        if (flags.json) throw new engine.InstallError('Importing is done in your own terminal, after you confirm.', `Run: ${engine.cli(ctx)} import ${args[0]}`);
        if (!ctx.dryRun && !flags.yes && (await prompt.ask(`Add the backup in ${args[0]} to the platform "${ctx.stack}" in this AWS account? Type "import" to continue:`)) !== 'import') { console.log('Nothing was imported.'); return { ok: false }; }
        return finish(importData(ctx, resolve(args[0]), { say: ctx.progress }));
      }
      case 'transfer': {
        if (flags.json) throw new engine.InstallError('A transfer needs account numbers and your teacher login typed by you, which is never done through an editor.', `Run in your own terminal: ${engine.cli(ctx)} transfer --from <profile> --to <profile>`);
        if (!flags.from || !flags.to) throw new engine.InstallError('Usage: node setup.mjs transfer --from <source AWS profile> --to <target AWS profile>  [--csv file.csv] [--finish]', 'Both accounts need a profile (aws configure --profile <name>). --finish is the last run, after the DNS switch.');
        const side = (profile) => ({ ...ctx, profile, aws: overrides.aws ?? engine.realAws({ profile }), progress: ctx.progress });
        const from = side(flags.from);
        const to = { ...side(flags.to), stack: flags['target-stack'] ?? ctx.stack };
        const [a, b] = [engine.preflight(from), engine.preflight(to)];
        console.log('');
        console.log(`Source: AWS account ${a.account} (${a.arn}), platform "${from.stack}"${a.stackStatus ? '' : ' (NOT FOUND)'}`);
        console.log(`Target: AWS account ${b.account} (${b.arn})${b.stackStatus ? `, a platform "${to.stack}" already exists there` : ', empty'}`);
        console.log('');
        console.log('The old account is only read. Stop students from publishing while this runs: anything published after the copy is not included.');
        const typedSource = await prompt.ask('Type the SOURCE account number to confirm:');
        const typedTarget = await prompt.ask('Type the TARGET account number to confirm:');
        const username = await prompt.ask('Teacher login name:', 'teacher');
        const password = await prompt.hidden('Teacher password (hidden): ');
        const sourcePortal = engine.stackOutputs(from).outputs.PortalUrl;
        if ((await checkTeacherLogin(sourcePortal, username, password)) === 'denied') { console.log('That teacher login was not accepted. Nothing was changed.'); process.exitCode = 1; return { ok: false }; }
        const csvPath = resolve(flags.csv ?? 'class-transfer-dns.csv');
        const done = await transfer({ source: from, target: to, confirm: { source: typedSource, target: typedTarget }, login: { username, password }, csvPath, say: ctx.progress, finish: Boolean(flags.finish) });
        if (done.needs === 'certificate') {
          console.log('');
          console.log(done.message);
          console.log(`  ${done.rows[0].type}  ${done.rows[0].name}  ->  ${done.rows[0].new_value}`);
          return done;
        }
        print({ checks: done.verified?.checks, message: done.message }, flags);
        console.log('');
        console.log(`DNS changes written to ${csvPath} (open it in a spreadsheet). In AWS CloudShell: Actions > Download file.`);
        console.log(`When the check above passed and DNS has been switched, run this again with --finish to attach your domain to the new platform, then keep the old one a week before ${engine.cli(ctx)} destroy.`);
        console.log(`The AI keys secret is not copied: run  ${engine.cli(ctx)} secrets --profile ${flags.to}  for the new account.`);
        return done;
      }
      case 'verify': {
        let login = null;
        if (!flags.json && !flags['no-login']) {
          const username = flags.user ?? await prompt.ask('Teacher login name:', 'teacher');
          const password = process.env.TEACHER_PASSWORD || await prompt.hidden('Teacher password (hidden, used once, never saved): ');
          login = { username, password };
        }
        const checked = await engine.verifyInstall(ctx, { login });
        finish({ ...checked, ok: checked.ok });
        if (!checked.ok) process.exitCode = 1;
        return checked;
      }
      case 'custom-domains': {
        if (!ctx.dryRun && !flags.yes && !flags.json && !(await prompt.yes('Set up custom domains for students (a few CloudFront pieces in this AWS account, about 10 minutes)?', 'y'))) return { ok: false };
        return finish(await engine.setupCustomDomains(ctx));
      }
      case 'status': return finish(await engine.status(ctx));
      case 'publish': return finish({ ok: true, ...engine.adminScript(ctx, ['portal-publish']) && {}, message: ctx.dryRun ? undefined : 'Portal pages, student kit and MCP package published.' });
      case 'check': return finish(engine.checkEnvironment(ctx));
      case 'plan': {
        const feature = flags.enable ?? flags.disable;
        return finish(feature ? await engine.featurePlan(ctx, parseFeatures(feature), Boolean(flags.enable)) : await engine.plan(ctx));
      }
      case 'apply': return finish(await engine.apply(ctx, args[0]));
      case 'upgrade': {
        const planned = await engine.plan(ctx);
        print(planned, flags);
        if (flags.json || planned.blocked || planned.empty || ctx.dryRun) return planned;
        const go = flags.yes || await prompt.yes('\nApply this update?');
        return go ? finish(await engine.apply(ctx, planned.planId)) : planned;
      }
      case 'destroy': {
        if (flags.json) throw new engine.InstallError('Deleting the stack is only done by typing the name in your own terminal.', 'Run: node setup.mjs destroy');
        const inv = engine.inventory(ctx);
        console.log('');
        if (inv.exists) {
          console.log(`This will delete the platform "${ctx.stack}"${inv.portalUrl ? ` (${inv.portalUrl})` : ''}:`);
          console.log(`  ${inv.students ?? 'an unknown number of'} student${inv.students === 1 ? '' : 's'}, ${inv.distributions} CloudFront distributions, ${inv.buckets} bucket${inv.buckets === 1 ? '' : 's'} (every published site), ${inv.functions} functions, ${inv.tables} table${inv.tables === 1 ? '' : 's'}`);
          console.log(flags.everything ? '  ALSO: the student data table, the saved parameters and the AI keys secret (--everything).' : '  Kept: the student data table and the AI keys secret (add --everything to remove those too).');
        } else console.log(`There is no platform stack "${ctx.stack}" here; only leftovers (such as the installer's staging bucket) are cleaned.`);
        console.log('');
        if (inv.exists && inv.portalUrl && !flags.yes) { // the teacher login proves this is the teacher, not a slip of the keyboard
          const username = await prompt.ask('Teacher login name:', 'teacher');
          const password = await prompt.hidden('Teacher password (hidden): ');
          const result = await checkTeacherLogin(inv.portalUrl, username, password);
          if (result === 'denied') { console.log('That teacher login was not accepted. Nothing was deleted.'); process.exitCode = 1; return { ok: false }; }
          if (result === 'unreachable') console.log('The portal did not answer, so the login could not be checked; the typed confirmation below is still required.');
        }
        const phrase = flags.everything ? 'delete everything' : ctx.stack;
        const what = flags.everything ? 'Type "delete everything" to continue:' : `Type the stack name (${ctx.stack}) to continue:`;
        const typed = flags.yes ? phrase : await prompt.ask(what);
        if (typed !== phrase) { console.log('Not deleted.'); return { ok: false }; }
        return finish(await engine.destroy(ctx, { everything: Boolean(flags.everything) }));
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
