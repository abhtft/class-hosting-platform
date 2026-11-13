import puppeteer from 'puppeteer-core';
import { readFileSync } from 'node:fs';
const OUT = 'D:/Github_Codes/class-hosting-platform-github/.temp/launch/shots';
const LOG = process.argv[2];
const log = readFileSync(LOG, 'utf8');
const alice = log.match(/Alice\s+(mds_\S+)/)[1]; // a fake demo token, never a real one
const BASE = 'http://127.0.0.1:8790';
const browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: 'new', args: ['--no-sandbox'] });
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'dark' }]);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// teacher: sign in, the student list, one student's panel with the feature switches
await page.goto(`${BASE}/teacher.html`, { waitUntil: 'networkidle0' });
await page.type('input[name=username]', 'teacher');
await page.type('input[name=password]', 'teacher-pass-123');
await page.click('#login-form button[type=submit]');
await page.waitForSelector('button.student', { timeout: 15000 });
await wait(800);
await page.screenshot({ path: `${OUT}/teacher-list.png` });
await page.click('button.student');
await wait(1200);
await page.screenshot({ path: `${OUT}/teacher-panel.png` });

// the moment a new token is created (the demo token is fake)
await page.keyboard.press('Escape');
await wait(300);
await page.click('#new-token');
await wait(600);
await page.screenshot({ path: `${OUT}/teacher-newtoken-form.png` });

// student: sign in with a demo token and show the page
const s = await browser.newPage();
await s.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
await s.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'dark' }]);
await s.goto(`${BASE}/student.html`, { waitUntil: 'networkidle0' });
await s.type('input[name=token]', alice);
await s.click('#login-form button[type=submit]');
await wait(2500);
await s.screenshot({ path: `${OUT}/student-top.png` });
await s.screenshot({ path: `${OUT}/student-full.png`, fullPage: true });
await browser.close();
console.log('captured');
