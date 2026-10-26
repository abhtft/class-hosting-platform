// Every Mermaid diagram in the documentation has a static PNG next to it (docs/diagrams/render.mjs). This test needs no browser: it checks that
// each diagram is wrapped, that its image exists and is a PNG, and that the recorded hash still matches the Mermaid source. Edit a diagram
// without running `node docs/diagrams/render.mjs` and this fails, naming the diagram.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { plan } from '../docs/diagrams/render.mjs';

const docs = join(dirname(fileURLToPath(import.meta.url)), '..', 'docs');
const problems = [];
let total = 0;
for (const name of ['TECHNICAL_SPECIFICATION.md', 'AI_CONNECTOR.md', 'STUDENT_KIT.md', 'STORAGE_CONNECTOR.md', 'AWS_LEARNINGS.md']) {
  const text = readFileSync(join(docs, name), 'utf8').replace(/\r\n/g, '\n');
  const { blocks } = plan(name, text);
  assert.ok(blocks.length > 0, `${name} has no diagrams`);
  for (const b of blocks) {
    total++;
    const marker = `<!-- diagram:${b.hash} -->\n![Diagram: ${b.heading}](${b.image})`;
    if (!text.includes(marker)) { problems.push(`${name}: "${b.heading}" is not wrapped with an image for its current source (run node docs/diagrams/render.mjs)`); continue; }
    const png = join(docs, b.image);
    if (!existsSync(png)) { problems.push(`${name}: missing ${b.image}`); continue; }
    if (readFileSync(png).subarray(1, 4).toString() !== 'PNG') problems.push(`${b.image} is not a PNG`);
  }
}
assert.equal(problems.length, 0, problems.join('\n'));
console.log(`test_diagrams.mjs: ${total} diagrams, each with a current PNG`);
