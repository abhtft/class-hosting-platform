#!/usr/bin/env node
// Turn every ```mermaid block in the documentation into a static PNG next to it, and keep the two in step.
//
//   node docs/diagrams/render.mjs            render only diagrams that are new or changed
//   node docs/diagrams/render.mjs --force    render all of them again
//
// Why: GitHub draws Mermaid in the browser, but editors' previews, the GitHub mobile app, PDF exports and slide decks often do not, and a big
// diagram is squeezed into a narrow frame. A PNG renders everywhere. The Mermaid source stays in the document (collapsed under the image)
// as the single editable source; this script regenerates the PNG from it. `tests/test_diagrams.mjs` fails when a diagram was edited but its
// image was not regenerated, so the two cannot drift apart.
//
// Needs Chrome, Chromium or Edge (set CHROME_PATH to override) and network access the first time (npx downloads @mermaid-js/mermaid-cli).
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, copyFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const docs = resolve(here, '..');
const force = process.argv.includes('--force');
const DOCS = ['TECHNICAL_SPECIFICATION.md', 'AI_CONNECTOR.md', 'STUDENT_KIT.md', 'STORAGE_CONNECTOR.md', 'AWS_LEARNINGS.md'];
const config = readFileSync(join(here, 'mermaid.json'), 'utf8');

export const sha8 = (source) => createHash('sha256').update(`${source}\n${config}`).digest('hex').slice(0, 8);
export const slugify = (text) => text.toLowerCase().replace(/`/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48);

const WRAPPED = /<!-- diagram:[0-9a-f]{8} -->\n!\[[^\]]*\]\([^)]*\)\n\n<details><summary>[^<]*<\/summary>\n\n(```mermaid\n[\s\S]*?```)\n\n<\/details>\n<!-- \/diagram -->/g;
const BARE = /```mermaid\n([\s\S]*?)```/g;

function chrome() {
  const candidates = [process.env.CHROME_PATH, 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  return candidates.find((p) => p && existsSync(p));
}

/** Plan one document: unwrap old wrappers, then give every Mermaid block a heading-based image name and a hash. */
export function plan(file, text) {
  const docSlug = slugify(basename(file, '.md'));
  const plain = text.replace(WRAPPED, '$1');
  const blocks = [];
  let match;
  while ((match = BARE.exec(plain))) {
    const before = plain.slice(0, match.index);
    const heading = [...before.matchAll(/^#{1,6} (.+)$/gm)].pop()?.[1] ?? basename(file);
    const n = String(blocks.length + 1).padStart(2, '0');
    blocks.push({ start: match.index, end: match.index + match[0].length, source: match[1], heading: heading.replace(/`/g, ''), image: `diagrams/${docSlug}/${n}-${slugify(heading)}.png`, hash: sha8(match[1]) });
  }
  return { plain, blocks };
}

export function wrap(block) {
  return `<!-- diagram:${block.hash} -->\n![Diagram: ${block.heading}](${block.image})\n\n<details><summary>Diagram source (Mermaid, editable; GitHub also draws it)</summary>\n\n\`\`\`mermaid\n${block.source}\`\`\`\n\n</details>\n<!-- /diagram -->`;
}

function main() {
  const browser = chrome();
  if (!browser) { console.error('No Chrome, Chromium or Edge found. Set CHROME_PATH.'); process.exit(1); }
  const temp = mkdtempSync(join(tmpdir(), 'diagrams-'));
  const puppeteer = join(temp, 'puppeteer.json');
  writeFileSync(puppeteer, JSON.stringify({ executablePath: browser, args: ['--no-sandbox', '--disable-gpu'] }));
  let rendered = 0;
  let kept = 0;
  try {
    for (const name of DOCS) {
      const file = join(docs, name);
      const { plain, blocks } = plan(file, readFileSync(file, 'utf8').replace(/\r\n/g, '\n'));
      const todo = blocks.filter((b) => force || !existsSync(join(docs, b.image)) || !readFileSync(file, 'utf8').includes(`diagram:${b.hash} -->\n![Diagram: ${b.heading}](${b.image})`));
      if (todo.length) {
        // one mmdc run per document: it draws all blocks of a markdown file in a single browser session and names them <out>-<n>.png
        const input = join(temp, `${basename(name, '.md')}.md`);
        writeFileSync(input, todo.map((b) => `\`\`\`mermaid\n${b.source}\`\`\``).join('\n\n'));
        const out = join(temp, `${basename(name, '.md')}-out.md`);
        const result = spawnSync('npx', ['-y', '@mermaid-js/mermaid-cli@12', '-i', input, '-o', out, '-e', 'png', '-p', puppeteer, '-c', join(here, 'mermaid.json'), '-b', 'white', '-s', '2'],
          { encoding: 'utf8', shell: true, env: { ...process.env, PUPPETEER_SKIP_DOWNLOAD: '1' } });
        if (result.status !== 0) { console.error(result.stdout, result.stderr); process.exit(1); }
        todo.forEach((b, i) => {
          const png = join(temp, `${basename(name, '.md')}-out-${i + 1}.png`);
          if (!existsSync(png)) { console.error(`mermaid-cli produced no image for diagram ${b.image}`); process.exit(1); }
          mkdirSync(dirname(join(docs, b.image)), { recursive: true });
          copyFileSync(png, join(docs, b.image));
          rendered++;
        });
      }
      kept += blocks.length - todo.length;
      // rewrite the document with every block wrapped (image first, source collapsed underneath)
      let out = '';
      let cursor = 0;
      for (const b of blocks) { out += plain.slice(cursor, b.start) + wrap(b); cursor = b.end; }
      out += plain.slice(cursor);
      const nl = readFileSync(file, 'utf8').includes('\r\n') ? '\r\n' : '\n';
      writeFileSync(file, out.replace(/\n/g, nl));
      console.log(`${name}: ${blocks.length} diagram(s), ${todo.length} rendered`);
    }
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
  console.log(`Done: ${rendered} image(s) rendered, ${kept} already current.`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
