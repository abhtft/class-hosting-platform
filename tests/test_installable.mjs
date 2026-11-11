// The starter site can be installed as an app (docs/INSTALLABLE_PLAN.md): a valid web app manifest, real PNG icons of the sizes it names, the links in
// index.html, and an install script that never puts text into the page as HTML, caches nothing and asks for no permission. A built kit must carry all of it.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const pub = join(repo, 'starter', 'template', 'frontend', 'public');
const index = readFileSync(join(repo, 'starter', 'template', 'frontend', 'index.html'), 'utf8');
const say = (m) => console.log(`  ok  ${m}`);

// ---- the manifest
const manifest = JSON.parse(readFileSync(join(pub, 'manifest.webmanifest'), 'utf8'));
assert.equal(manifest.display, 'standalone', 'opens without the browser bar');
assert.equal(manifest.start_url, '/');
assert.ok(manifest.name && manifest.short_name && manifest.description, 'name, short name and description');
assert.ok(/^#[0-9a-f]{6}$/i.test(manifest.theme_color) && /^#[0-9a-f]{6}$/i.test(manifest.background_color), 'colours are hex');
const sizes = new Set(manifest.icons.map((icon) => icon.sizes));
assert.ok(sizes.has('192x192') && sizes.has('512x512'), 'icons for 192 and 512 pixels');
assert.ok(manifest.icons.some((icon) => icon.purpose === 'maskable'), 'a maskable icon');
for (const icon of manifest.icons) {
  assert.ok(icon.src.startsWith('/') && icon.type === 'image/png', `${icon.src} is an absolute PNG address`);
}
say('the manifest: standalone, start at /, name and colours, 192 and 512 icons and a maskable one');

// ---- the icons are real PNG files of the sizes they claim
const pngSize = (file) => {
  const data = readFileSync(join(pub, file));
  assert.deepEqual([...data.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], `${file} has the PNG signature`);
  return [data.readUInt32BE(16), data.readUInt32BE(20)];
};
assert.deepEqual(pngSize('icon-192.png'), [192, 192]);
assert.deepEqual(pngSize('icon-512.png'), [512, 512]);
assert.deepEqual(pngSize('apple-touch-icon.png'), [180, 180]);
for (const icon of manifest.icons) {
  const [w] = pngSize(icon.src.slice(1));
  assert.equal(`${w}x${w}`, icon.sizes, `${icon.src} is ${icon.sizes}`);
}
say('the icons are PNG files of the sizes the manifest names (192, 512, and 180 for iPhones)');

// ---- index.html links it all up
for (const needle of [
  '<link rel="manifest" href="/manifest.webmanifest" />',
  '<meta name="theme-color" content="#2563eb" />',
  '<link rel="apple-touch-icon" href="/apple-touch-icon.png" />',
  'apple-mobile-web-app-capable',
  '<script src="/install.js"></script>',
]) assert.ok(index.includes(needle), `index.html has ${needle}`);
say('index.html links the manifest, the theme colour, the iPhone icon and the install script');

// ---- the install script: safe by construction
const script = readFileSync(join(pub, 'install.js'), 'utf8');
assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function/.test(script), 'no HTML from strings, no eval');
assert.ok(!/localStorage|sessionStorage|indexedDB|caches\.|navigator\.(permissions|geolocation|serviceWorker)|Notification\.requestPermission|getUserMedia/.test(script), 'stores nothing, caches nothing, asks for no permission');
assert.ok(script.includes("'beforeinstallprompt'") && script.includes("'appinstalled'") && script.includes("'(display-mode: standalone)'"), 'handles the install offer, the installed event and the installed state');
assert.ok(script.includes('preventDefault') && script.includes('deferred.prompt()'), 'the install is started only by a tap');
assert.ok(/iphone\|ipad\|ipod/.test(script) && script.includes('Add to Home Screen'), 'iPhone and iPad get the home-screen steps');
say('the install script: no HTML from text, no storage, no caching, no permissions, starts only on a tap, and handles iPhone, Android and installed states');

// ---- a built kit carries everything (public/ is copied to the build; the install script is in the kit)
const { buildKit } = await import(pathToFileURL(join(repo, 'starter', 'build-kit.mjs')).href);
const work = mkdtempSync(join(tmpdir(), 'installable-'));
try {
  const kit = join(work, 'kit');
  buildKit(kit);
  for (const file of ['frontend/public/manifest.webmanifest', 'frontend/public/install.js', 'frontend/public/icon-192.png', 'frontend/public/icon-512.png', 'frontend/public/apple-touch-icon.png', 'frontend/index.html']) {
    assert.ok(existsSync(join(kit, file)), `the kit has ${file}`);
  }
  const manifestFiles = JSON.parse(readFileSync(join(kit, 'kit-manifest.json'), 'utf8')).files;
  assert.ok(manifestFiles['frontend/public/install.js'], 'the kit manifest lists the install script (so updates carry it)');
  say('a built kit carries the manifest, the three icons, the install script and the links, and lists them in its manifest');
} finally {
  rmSync(work, { recursive: true, force: true });
}
console.log('installable starter: all checks passed');
