// A small reader for JSON-with-comments (editors' config files are often JSONC: comments and trailing commas are allowed there).
// It never rewrites a file; it only tells us what is in it and whether comments would be lost if we saved it back as plain JSON.

/** Walk the text outside of strings and report every comment and trailing comma. Returns { text: <JSON without them>, hadComments } */
export function stripJsonc(source) {
  let out = '';
  let hadComments = false;
  let i = 0;
  const n = source.length;
  while (i < n) {
    const c = source[i];
    const d = source[i + 1];
    if (c === '"') { // a string: copy it as it is, honouring escapes
      let j = i + 1;
      while (j < n && source[j] !== '"') j += source[j] === '\\' ? 2 : 1;
      out += source.slice(i, j + 1);
      i = j + 1;
    } else if (c === '/' && d === '/') {
      hadComments = true;
      while (i < n && source[i] !== '\n') i++;
    } else if (c === '/' && d === '*') {
      hadComments = true;
      const end = source.indexOf('*/', i + 2);
      i = end < 0 ? n : end + 2;
    } else {
      out += c;
      i++;
    }
  }
  // trailing commas before } or ]: a second pass that, like the first, copies strings whole
  let clean = '';
  for (let k = 0; k < out.length; k++) {
    const c = out[k];
    if (c === '"') {
      let j = k + 1;
      while (j < out.length && out[j] !== '"') j += out[j] === '\\' ? 2 : 1;
      clean += out.slice(k, j + 1);
      k = j;
    } else if (c === ',' && /^\s*[}\]]/.test(out.slice(k + 1))) {
      // dropped
    } else {
      clean += c;
    }
  }
  return { text: clean, hadComments };
}

/** Parse JSONC. Returns { value, hadComments } or throws SyntaxError when the text is not JSON even after stripping comments. */
export function parseJsonc(source) {
  const { text, hadComments } = stripJsonc(source.replace(/^﻿/, ''));
  return { value: text.trim() === '' ? {} : JSON.parse(text), hadComments };
}
