import puppeteer from 'puppeteer-core';
const OUT = 'D:/Github_Codes/class-hosting-platform-github/.temp/launch/shots';
const BASE = 'http://127.0.0.1:8790';
const browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: 'new', args: ['--no-sandbox'] });
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'dark' }]);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
await page.goto(`${BASE}/teacher.html`, { waitUntil: 'networkidle0' });
await page.type('input[name=username]', 'teacher');
await page.type('input[name=password]', 'teacher-pass-123');
await page.click('#login-form button[type=submit]');
await page.waitForSelector('button.student', { timeout: 15000 });
await wait(600);
// Alice's panel: the feature switches
await page.click('button.student[aria-label="Open details for Alice"]');
await wait(1200);
await page.screenshot({ path: `${OUT}/teacher-alice.png` });
await page.keyboard.press('Escape');
await wait(500);
// create a token for a new student: the form, then the "copy it now" card
await page.click('#new-token');
await wait(500);
const inputs = await page.$$('dialog input, .modal input, input[type=text]');
// the student-name field is the first visible text input inside the open dialog
await page.evaluate(() => { const i = [...document.querySelectorAll('input')].find((x) => x.offsetParent && /Student name/i.test(x.closest('label')?.textContent || '')); i?.focus(); });
await page.keyboard.type('Maya');
await wait(200);
await page.screenshot({ path: `${OUT}/teacher-newtoken-filled.png` });
await page.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Create')?.click());
await wait(1500);
await page.screenshot({ path: `${OUT}/teacher-created.png` });
await browser.close();
console.log('captured', inputs.length >= 0);
