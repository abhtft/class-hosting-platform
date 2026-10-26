#!/usr/bin/env node
// Build the npm package a teacher installs with one npx command (name: see PACKAGE_NAME). It carries the platform itself, taken from the files git
// tracks (so no local or personal file can slip in), plus the teacher MCP and a small command entry.
//
//   node build-teacher-package.mjs [outDir]          build into dist/teacher-package (default)
//   node build-teacher-package.mjs [outDir] --pack   also run `npm pack` there, giving the tarball to `npm publish`
//   --repo <owner/name>                              where the releases live (default: $GITHUB_REPOSITORY); stamped into the package so its
//                                                    editor entry and update check point at that repository's GitHub Releases
//   --release <dir>                                  with --pack: put the files for a GitHub Release there (versioned tarball, version-less alias,
//                                                    sha256sums.txt); default dist/release
//
// The package version is the repository's VERSION file. CI builds this from a release tag, so the package is exactly what was tested.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const PACKAGE_NAME = 'class-platform-teacher';
const here = dirname(fileURLToPath(import.meta.url));

// what goes into the package: whole folders (tracked files only) and single files
const FOLDERS = ['lambda', 'web', 'starter', 'student', 'installer'];
const FILES = ['template.yaml', 'admin.mjs', 'setup.mjs', 'portal-downloads.json', 'VERSION', 'CHANGELOG.md', 'docs/TEACHER_SETUP.md', 'docs/NEW_ACCOUNT_AND_TRANSFER.md'];
const SKIP = [/^starter\/eval\//, /(^|\/)__pycache__\//, /\.pyc$/, /\.local$/, /(^|\/)test_/];
const MCP = ['server.mjs', 'setup.mjs', 'jsonc.mjs']; // from teacher-mcp/, placed in mcp/

const tracked = (paths) => execFileSync('git', ['ls-files', '-z', '--', ...paths], { cwd: here, encoding: 'utf8' }).split('\0').filter(Boolean);

export function packageFiles() {
  const files = [...tracked(FOLDERS), ...FILES].filter((f) => !SKIP.some((re) => re.test(f)));
  return [...new Set(files)].sort();
}

export const releaseUrl = (repo, version) => `https://github.com/${repo}/releases/download/v${version}/${PACKAGE_NAME}-${version}.tgz`;

export function buildTeacherPackage(outDir = join(here, 'dist', 'teacher-package'), { pack = false, repo = process.env.GITHUB_REPOSITORY || '', releaseDir = join(here, 'dist', 'release') } = {}) {
  if (repo && !/^[\w.-]+\/[\w.-]+$/.test(repo)) throw new Error(`--repo must look like owner/name, not "${repo}"`);
  const out = resolve(outDir);
  rmSync(out, { recursive: true, force: true });
  const put = (from, to) => { mkdirSync(dirname(join(out, to)), { recursive: true }); copyFileSync(join(here, from), join(out, to)); };
  const files = packageFiles();
  for (const f of files) put(f, f);
  for (const f of MCP) put(join('teacher-mcp', f), join('mcp', f));
  put(join('teacher-package', 'bin.mjs'), 'bin.mjs');
  put(join('teacher-package', 'README.md'), 'README.md');
  writeFileSync(join(out, '.packaged'), 'This folder is the npm package: install state is kept in the home folder, not here.\n');
  const version = readFileSync(join(here, 'VERSION'), 'utf8').trim();
  writeFileSync(join(out, 'package.json'), `${JSON.stringify({
    name: PACKAGE_NAME, version, description: 'Install and update your own copy of the class hosting platform in your own AWS account with one command, plus MCP tools for your AI editor.',
    type: 'module', bin: { [PACKAGE_NAME]: 'bin.mjs' }, engines: { node: '>=20' }, license: 'MIT',
    files: ['*', '.packaged'], publishConfig: { access: 'public', provenance: true },
  }, null, 2)}\n`);
  writeFileSync(join(out, 'release.json'), `${JSON.stringify({ repo, version, tag: `v${version}`, url: repo ? releaseUrl(repo, version) : '' }, null, 2)}
`);
  let tarball = null;
  const release = [];
  if (pack) {
    execFileSync('npm', ['pack', '--silent'], { cwd: out, shell: process.platform === 'win32' });
    tarball = `${PACKAGE_NAME}-${version}.tgz`;
    // the files of a GitHub Release: the pinned tarball, an alias without the version (for "latest" links), and checksums
    mkdirSync(releaseDir, { recursive: true });
    copyFileSync(join(out, tarball), join(releaseDir, tarball));
    copyFileSync(join(out, tarball), join(releaseDir, `${PACKAGE_NAME}.tgz`));
    const sums = [tarball, `${PACKAGE_NAME}.tgz`].map((f) => `${createHash('sha256').update(readFileSync(join(releaseDir, f))).digest('hex')}  ${f}`);
    writeFileSync(join(releaseDir, 'sha256sums.txt'), `${sums.join(String.fromCharCode(10))}${String.fromCharCode(10)}`);
    release.push(...[tarball, `${PACKAGE_NAME}.tgz`, 'sha256sums.txt'].map((f) => join(releaseDir, f)));
  }
  return { out, version, files: files.length + MCP.length + 5, tarball, release, repo };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const positional = process.argv.slice(2).filter((a) => !a.startsWith('--'));
  const opt = (name) => (process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : undefined);
  const flagValues = new Set([opt('--repo'), opt('--release')]);
  const result = buildTeacherPackage(positional.find((p) => !flagValues.has(p)), { pack: process.argv.includes('--pack'), repo: opt('--repo') ?? process.env.GITHUB_REPOSITORY ?? '', releaseDir: opt('--release') ? resolve(opt('--release')) : undefined });
  console.log(`Built ${PACKAGE_NAME} ${result.version} (${result.files} files) in ${result.out}${result.tarball ? `, tarball ${result.tarball}` : ''}${result.repo ? `, releases at github.com/${result.repo}` : ' (no repository stamped: pass --repo owner/name for a release)'}`);
  for (const file of result.release) console.log(`Release file: ${file}`);
}
