#!/usr/bin/env node
// One-off scrub: replaces the maintainer's own identifiers in tracked text files with neutral placeholders.
//
//   node tools/scrub-identifiers.mjs            dry run: lists the files that would change
//   node tools/scrub-identifiers.mjs --apply    rewrites them
//
// Two kinds of rules:
//   built-in   shapes that are never wanted in the repository (12-digit account id inside an ARN, execute-api hosts, *.cloudfront.net hosts)
//   identifiers.local   git-ignored JSON: { "domain": "...", "literals": { "<exact value>": "<placeholder>" } }
//              the real values live only there, so this file is the one place that names them. tests/test_no_identifiers.py reads it too.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = join(dirname(fileURLToPath(import.meta.url)), '..');
const PLACEHOLDER_DOMAIN = 'example.edu';

export function privateMatchers() {
  const { paths } = JSON.parse(readFileSync(join(repo, 'tools', 'private-files.json'), 'utf8'));
  return paths.map((p) => {
    if (p.endsWith('/')) return (f) => f.startsWith(p);
    const re = new RegExp(`^${p.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*')}$`);
    return (f) => re.test(f);
  });
}

export function loadLocal() {
  const file = join(repo, 'identifiers.local');
  if (!existsSync(file)) return { domain: '', literals: {} };
  const data = JSON.parse(readFileSync(file, 'utf8'));
  return { domain: data.domain ?? '', literals: data.literals ?? {} };
}

/** The ordered list of [pattern, replacement] pairs. Longest literals first so a longer value is never half replaced. */
export function rules() {
  const { domain, literals } = loadLocal();
  const out = Object.entries(literals).sort((a, b) => b[0].length - a[0].length).map(([from, to]) => [new RegExp(from.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), to]);
  // the regex-escaped form (example.edu), as written in test assertions
  if (domain) out.push([new RegExp(domain.split('.').join('\\\\\\.'), 'gi'), PLACEHOLDER_DOMAIN.split('.').join('\\.')]);
  if (domain) out.push([new RegExp(domain.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), PLACEHOLDER_DOMAIN]);
  out.push([/(arn:aws:[a-z0-9-]+:[a-z0-9-]*:)\d{12}(:)/g, '$1<ACCOUNT_ID>$2']);
  out.push([/\b[a-z0-9]{10}\.execute-api\./g, '<api-id>.execute-api.']);
  out.push([/\bd[a-z0-9]{12,13}\.cloudfront\.net\b/g, '<id>.cloudfront.net']);
  return out;
}

export function trackedTextFiles() {
  const isPrivate = privateMatchers();
  const files = execFileSync('git', ['ls-files', '-z'], { cwd: repo, encoding: 'utf8' }).split('\0').filter(Boolean);
  return files.filter((f) => !isPrivate.some((m) => m(f)));
}

if (process.argv[1] && join(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const apply = process.argv.includes('--apply');
  const all = rules();
  if (!loadLocal().domain) console.error('Note: identifiers.local is missing or has no domain; only the built-in shapes are replaced.');
  let changed = 0;
  for (const rel of trackedTextFiles()) {
    const buffer = readFileSync(join(repo, rel));
    if (buffer.includes(0)) continue; // binary
    const before = buffer.toString('utf8');
    let after = before;
    for (const [pattern, to] of all) after = after.replace(pattern, to);
    if (after === before) continue;
    changed++;
    console.log(`${apply ? 'rewrote' : 'would change'}  ${rel}`);
    if (apply) writeFileSync(join(repo, rel), after);
  }
  console.log(`${changed} file(s) ${apply ? 'rewritten' : 'would change (run with --apply)'}.`);
}
