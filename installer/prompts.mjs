// Questions in the teacher's terminal: plain ones, yes/no, and hidden ones (passwords, API keys).
//
// Each plain question opens its own short-lived reader and closes it again. Mixing one long-lived reader with the raw-mode hidden prompts left the
// terminal input paused after the first hidden answer, so the next question never got its answer and the program exited (found on a real terminal).
// Everything takes the streams as parameters, so the exact sequence "question, hidden answer, question" is tested without a terminal.
import { createInterface } from 'node:readline/promises';

/** One step of typing into a hidden prompt. Escape sequences (arrow keys) are ignored; Ctrl+C aborts; Backspace removes a character. */
export function editLine(buffer, chunk) {
  if (chunk.startsWith('\u001b')) return { buffer, done: false, abort: false };
  for (const ch of chunk) {
    if (ch === '\r' || ch === '\n') return { buffer, done: true, abort: false };
    if (ch === '\u0003') return { buffer, done: false, abort: true };
    if (ch === '\u007f' || ch === '\b') buffer = buffer.slice(0, -1);
    else if (ch >= ' ') buffer += ch;
  }
  return { buffer, done: false, abort: false };
}

/** What is shown while typing: `prefix` characters (a key's first letters prove the paste arrived), then one star each. A password uses prefix 0. */
export const mask = (text, prefix = 0) => text.slice(0, prefix) + '*'.repeat(Math.max(0, text.length - prefix));

export function createPrompter({ input = process.stdin, output = process.stdout } = {}) {
  const raw = Boolean(input.isTTY) && typeof input.setRawMode === 'function';

  async function ask(question, fallback = '') {
    const rl = createInterface({ input, output });
    try {
      const answer = (await rl.question(fallback ? `${question} [${fallback}] ` : `${question} `)).trim();
      return answer || fallback;
    } finally {
      rl.close();
    }
  }

  const yes = async (question, fallback = 'n') => /^y/i.test(await ask(`${question} (y/n)`, fallback));

  /** A hidden answer. In a terminal each key is masked as it is typed; piped input (tests, scripts) is read as one line. */
  async function hidden(question, { prefix = 0 } = {}) {
    output.write(question);
    if (!raw) {
      const rl = createInterface({ input });
      try {
        for await (const line of rl) { output.write('\n'); return line.trim(); }
        return '';
      } finally { rl.close(); }
    }
    return new Promise((resolve, reject) => {
      let buffer = '';
      input.setRawMode(true);
      input.resume();
      input.setEncoding('utf8');
      const finish = () => { input.off('data', onData); input.setRawMode(false); input.pause(); };
      const onData = (chunk) => {
        const step = editLine(buffer, String(chunk));
        if (step.abort) { finish(); output.write('\n'); reject(new Error('Cancelled.')); return; }
        buffer = step.buffer;
        output.write(`\r\u001b[2K${question}${mask(buffer, prefix)}`);
        if (step.done) { finish(); output.write('\n'); resolve(buffer.trim()); }
      };
      input.on('data', onData);
    });
  }

  /** A new password asked twice, so a typing slip cannot lock the teacher out. */
  async function newPassword(question, { min = 10, tries = 3 } = {}) {
    for (let i = 0; i < tries; i += 1) {
      const first = await hidden(`${question} (at least ${min} characters, hidden): `);
      if (first.length < min) { output.write(`  Too short: use at least ${min} characters.\n`); continue; }
      const again = await hidden('Type it again: ');
      if (first === again) return first;
      output.write('  The two did not match. Try again.\n');
    }
    return '';
  }

  return { ask, yes, hidden, newPassword };
}
