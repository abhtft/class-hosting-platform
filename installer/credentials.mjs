// The teacher's AWS access, saved once so the editor tools and the commands need no `aws configure`: the access key goes into ONE section of the
// standard AWS credentials file (a profile of its own, called class-platform), after a timestamped backup of that file. Nothing else in it is touched.
// The key is never printed, never sent anywhere but AWS, and never passes through an editor tool or the AI.
import { chmodSync, copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

export const PROFILE_NAME = 'class-platform';
const KEY_ID = /^[A-Z0-9]{16,128}$/;
const SECRET = /^[A-Za-z0-9/+=]{30,100}$/;

export const awsFiles = (env = process.env) => ({
  credentials: env.AWS_SHARED_CREDENTIALS_FILE || join(homedir(), '.aws', 'credentials'),
  config: env.AWS_CONFIG_FILE || join(homedir(), '.aws', 'config'),
});

export const teacherHome = (env = process.env) => env.CLASS_PLATFORM_TEACHER_HOME || join(homedir(), '.class-platform-teacher');

/** Replace (or add) one `[header]` section in an INI text; every other line stays exactly as it was. */
export function setSection(text, header, lines) {
  const normal = text.replace(/\r\n/g, '\n');
  const block = `[${header}]\n${lines.join('\n')}\n`;
  const start = normal.split('\n').findIndex((l) => l.trim() === `[${header}]`);
  if (start < 0) return `${normal}${normal && !normal.endsWith('\n') ? '\n' : ''}${normal ? '\n' : ''}${block}`;
  const all = normal.split('\n');
  let end = all.findIndex((l, i) => i > start && /^\s*\[/.test(l));
  if (end < 0) end = all.length;
  return `${[...all.slice(0, start), ...block.trimEnd().split('\n'), '', ...all.slice(end)].join('\n').replace(/\n{3,}/g, '\n\n').trimEnd()}\n`;
}

function backup(file) {
  if (!existsSync(file)) return '';
  const copy = `${file}.bak-${new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)}`;
  copyFileSync(file, copy);
  return copy;
}

/** Save an access key as the class-platform profile. Returns what was written (never the key). */
export function saveProfile({ accessKeyId, secretAccessKey, region = 'us-east-1', env = process.env }) {
  if (!KEY_ID.test(String(accessKeyId ?? ''))) throw new Error('That does not look like an AWS access key id (it is 20 capital letters and digits, for example AKIA...).');
  if (!SECRET.test(String(secretAccessKey ?? ''))) throw new Error('That does not look like an AWS secret access key (about 40 characters).');
  const files = awsFiles(env);
  const made = [];
  for (const [file, header, lines] of [
    [files.credentials, PROFILE_NAME, [`aws_access_key_id = ${accessKeyId}`, `aws_secret_access_key = ${secretAccessKey}`]],
    [files.config, `profile ${PROFILE_NAME}`, [`region = ${region}`, 'output = json']],
  ]) {
    mkdirSync(dirname(file), { recursive: true });
    const saved = backup(file);
    writeFileSync(file, setSection(existsSync(file) ? readFileSync(file, 'utf8') : '', header, lines));
    try { chmodSync(file, 0o600); } catch { /* Windows has no such modes */ }
    made.push({ file, backup: saved });
  }
  return { profile: PROFILE_NAME, files: made };
}

/** The settings kept for this teacher on this computer (which AWS profile to use). Contains no secret. */
export function readGlobalConfig(env = process.env) {
  try { return JSON.parse(readFileSync(join(teacherHome(env), 'config.json'), 'utf8')); } catch { return {}; }
}

export function writeGlobalConfig(config, env = process.env) {
  const dir = teacherHome(env);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'config.json'), `${JSON.stringify(config, null, 2)}\n`);
}
